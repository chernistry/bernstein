"""``bernstein doctor glitchtip`` -- GlitchTip insights surface.

Pulls the GlitchTip API for last-24h issue counts by severity, a 7-day
trend, and the top 5 unresolved issues. Outputs a Rich table or JSON.

Soft-fail behaviour
-------------------
The command exits with code 0 when both env vars are unset
(``BERNSTEIN_GLITCHTIP_TOKEN`` and ``BERNSTEIN_GLITCHTIP_DSN``) so the
fresh-checkout case does not block any operator workflow. When the
token is set but the API call fails, the command exits with code 0 and
prints the failure reason on stderr -- the operator can re-run with
``--json`` for a machine-readable signal.

Baseline cache
--------------
The command persists a tiny baseline file at
``~/.local/share/bernstein/glitchtip-baseline.json`` capturing the last
observed issue count and timestamp. The ``--suggest-docs`` hook reads
this cache to decide whether to nudge the operator about new unresolved
issues since their last check.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.observability.glitchtip_insights import (
    DEFAULT_TOP_N,
    ENV_GLITCHTIP_TOKEN,
    InsightsResult,
    fetch_insights,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import Console

_LOGGER = logging.getLogger(__name__)

#: Environment variable carrying the runtime DSN. We only read this so we
#: can produce a precise soft-fail reason when neither the token nor the
#: DSN is configured (the operator has not started observability at all).
ENV_GLITCHTIP_DSN = "BERNSTEIN_GLITCHTIP_DSN"

#: Override for the baseline cache location. Used by the test-suite so
#: ``~/.local/share/bernstein/`` is never touched.
ENV_BASELINE_PATH = "BERNSTEIN_GLITCHTIP_BASELINE"


# ---------------------------------------------------------------------------
# Baseline cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """Captured insights signature used by the periodic nudge."""

    checked_at: str
    issues_24h: int
    last_short_id: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping."""
        return {
            "checked_at": self.checked_at,
            "issues_24h": self.issues_24h,
            "last_short_id": self.last_short_id,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Baseline | None:
        """Build a baseline from a parsed JSON object.

        Returns ``None`` for malformed input so the nudge degrades to
        a "no baseline yet" state rather than crashing.
        """
        try:
            return cls(
                checked_at=str(raw["checked_at"]),
                issues_24h=int(raw.get("issues_24h", 0)),
                last_short_id=str(raw.get("last_short_id", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def default_baseline_path() -> Path:
    """Resolve the baseline cache path with env override and XDG fallback.

    Resolution order:

    1. ``BERNSTEIN_GLITCHTIP_BASELINE`` -- used by tests.
    2. ``$XDG_DATA_HOME/bernstein/glitchtip-baseline.json``.
    3. ``~/.local/share/bernstein/glitchtip-baseline.json``.
    """
    override = os.environ.get(ENV_BASELINE_PATH)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "bernstein" / "glitchtip-baseline.json"
    return Path.home() / ".local" / "share" / "bernstein" / "glitchtip-baseline.json"


def load_baseline(path: Path | None = None) -> Baseline | None:
    """Load a baseline from disk, returning ``None`` when absent or bad."""
    target = path if path is not None else default_baseline_path()
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return Baseline.from_dict(raw)


def write_baseline(baseline: Baseline, path: Path | None = None) -> None:
    """Persist ``baseline`` atomically to disk.

    The target directory is created if missing. Errors are swallowed
    silently because failing to update the cache must never block the
    doctor command itself.
    """
    target = path if path is not None else default_baseline_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps(baseline.to_dict(), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(target)
    except OSError:
        return


def baseline_from_result(result: InsightsResult) -> Baseline:
    """Construct a baseline snapshot from a populated insights result."""
    last_id = result.top_unresolved[0].short_id if result.top_unresolved else ""
    return Baseline(
        checked_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        issues_24h=result.issues_24h,
        last_short_id=last_id,
    )


def detect_new_issues(
    result: InsightsResult,
    baseline: Baseline | None,
) -> int:
    """Return the number of new unresolved issues since the baseline.

    The 24h ``new_24h`` count from the API already reflects deltas
    against the rolling window, but we additionally compare against
    the persisted baseline so the nudge fires when the operator has
    not run the doctor in over a day.
    """
    if not result.ok:
        return 0
    if baseline is None:
        return result.new_24h
    delta = max(0, result.issues_24h - baseline.issues_24h)
    return max(result.new_24h, delta)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


_LEVEL_STYLE: dict[str, str] = {
    "fatal": "bold red",
    "error": "red",
    "warning": "yellow",
    "info": "cyan",
    "debug": "dim",
    "other": "white",
}


def _format_short(ts: str) -> str:
    """Render an ISO8601 timestamp as ``YYYY-MM-DD HH:MM`` UTC."""
    if not ts:
        return "-"
    try:
        normalised = ts.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalised)
    except ValueError:
        return ts
    return parsed.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M")


def render_severity_table(result: InsightsResult, target: Console) -> None:
    """Render the 24h severity summary table."""
    table = Table(title="GlitchTip - last 24h by severity", show_header=True)
    table.add_column("Level", style="bold")
    table.add_column("Events", justify="right")
    for level, count in result.severity_24h.items():
        style = _LEVEL_STYLE.get(level, "white")
        table.add_row(f"[{style}]{level}[/{style}]", str(count))
    target.print(table)


def render_top_issues_table(result: InsightsResult, target: Console) -> None:
    """Render the top-N unresolved issues table."""
    table = Table(
        title=f"Top {len(result.top_unresolved)} unresolved issues",
        show_header=True,
    )
    table.add_column("Id", style="cyan")
    table.add_column("Level")
    table.add_column("Events", justify="right")
    table.add_column("Users", justify="right")
    table.add_column("First seen")
    table.add_column("Last seen")
    table.add_column("Title", overflow="fold")
    for issue in result.top_unresolved:
        style = _LEVEL_STYLE.get(issue.level, "white")
        table.add_row(
            issue.short_id or "-",
            f"[{style}]{issue.level}[/{style}]",
            str(issue.count),
            str(issue.user_count),
            _format_short(issue.first_seen),
            _format_short(issue.last_seen),
            issue.title or "-",
        )
    target.print(table)


def render_trend_line(result: InsightsResult, target: Console) -> None:
    """Render a single-line 7-day trend sparkline."""
    if not result.trend_7d:
        target.print("[dim]7-day trend: (no data)[/dim]")
        return
    blocks = " ▁▂▃▄▅▆▇█"
    peak = max(result.trend_7d) or 1
    spark = "".join(blocks[min(len(blocks) - 1, int((n / peak) * (len(blocks) - 1)))] for n in result.trend_7d)
    target.print(f"7-day trend (oldest -> newest): [cyan]{spark}[/cyan]")


def render_report(result: InsightsResult, target: Console | None = None) -> None:
    """Print the full GlitchTip insights report."""
    out = target if target is not None else console
    if not result.ok:
        out.print(f"[yellow]GlitchTip insights unavailable:[/yellow] {result.reason}")
        return

    out.print(f"GlitchTip [cyan]{result.org_slug}[/cyan] @ [dim]{result.base_url}[/dim]")
    out.print(f"Issues in last 24h: [bold]{result.issues_24h}[/bold] (new: [bold]{result.new_24h}[/bold])")
    render_severity_table(result, out)
    render_trend_line(result, out)
    if result.top_unresolved:
        render_top_issues_table(result, out)
    else:
        out.print("[dim]No unresolved issues to surface.[/dim]")


# ---------------------------------------------------------------------------
# Nudge helper for ``bernstein doctor --suggest-docs``
# ---------------------------------------------------------------------------


def suggest_nudge_line(
    *,
    env: dict[str, str] | None = None,
    baseline_path: Path | None = None,
    fetcher: Any = None,
) -> str | None:
    """Return a one-line nudge string when GlitchTip shows new issues.

    Returns ``None`` when:

    * the GlitchTip token is not configured (no signal to nudge with)
    * the API call soft-fails
    * there are no new issues since the last baseline

    The function is wired into ``bernstein doctor --suggest-docs`` so the
    operator sees a single concise line at the end of the diagnostic
    report rather than another full table.
    """
    source = env if env is not None else os.environ.copy()
    if not source.get(ENV_GLITCHTIP_TOKEN):
        return None

    fetch = fetcher if fetcher is not None else fetch_insights
    try:
        result: InsightsResult = fetch(env=source)
    except Exception:
        # The nudge is purely advisory: log the unexpected failure so we
        # can diagnose regressions, but never propagate it to the doctor
        # command above us.
        _LOGGER.warning("GlitchTip fetch failed in suggest_nudge_line", exc_info=True)
        return None

    if not result.ok:
        return None

    baseline = load_baseline(baseline_path)
    new_count = detect_new_issues(result, baseline)

    # Always refresh the baseline so the next invocation compares against
    # the freshest observation.
    write_baseline(baseline_from_result(result), baseline_path)

    if new_count <= 0:
        return None
    plural = "issues" if new_count != 1 else "issue"
    return (
        f"GlitchTip has {new_count} new unresolved {plural} since your "
        "last check -- run 'bernstein doctor glitchtip' for details."
    )


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


@click.command("glitchtip")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of the Rich tables.",
)
@click.option(
    "--top-n",
    "top_n",
    type=click.IntRange(min=1),
    default=DEFAULT_TOP_N,
    show_default=True,
    help="Number of top unresolved issues to surface.",
)
@click.option(
    "--no-baseline",
    "skip_baseline",
    is_flag=True,
    default=False,
    help="Do not update the baseline cache after the report.",
)
def glitchtip_cmd(as_json: bool, top_n: int, skip_baseline: bool) -> None:
    """Surface GlitchTip issue counts and top unresolved issues.

    \b
    Reads:
      - BERNSTEIN_GLITCHTIP_TOKEN  (API token, required for non-trivial output)
      - BERNSTEIN_GLITCHTIP_DSN    (runtime DSN, informational only here)
      - BERNSTEIN_GLITCHTIP_BASE_URL (API base URL; e.g. https://glitchtip.example.com.
        No default host: derived from the DSN host when unset, else soft-fails)
      - BERNSTEIN_GLITCHTIP_ORG    (optional override, default: bernstein)

    \b
    Examples:
      bernstein doctor glitchtip
      bernstein doctor glitchtip --json
      bernstein doctor glitchtip --top-n 10
    """
    env = os.environ.copy()
    has_token = bool(env.get(ENV_GLITCHTIP_TOKEN))
    has_dsn = bool(env.get(ENV_GLITCHTIP_DSN))

    if not has_token and not has_dsn:
        if as_json:
            sys.stdout.write(
                json.dumps(
                    {
                        "ok": False,
                        "reason": (
                            f"{ENV_GLITCHTIP_TOKEN} and {ENV_GLITCHTIP_DSN} are both unset; observability is not wired"
                        ),
                    }
                )
                + "\n"
            )
            return
        console.print(
            "[yellow]GlitchTip insights unavailable:[/yellow] "
            f"export {ENV_GLITCHTIP_TOKEN} to enable the read-side surface."
        )
        return

    result = fetch_insights(env=env, top_n=top_n)

    if as_json:
        sys.stdout.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
    else:
        render_report(result)

    if result.ok and not skip_baseline:
        write_baseline(baseline_from_result(result))


def register(parent: click.Group) -> None:
    """Attach the GlitchTip subcommand to the parent ``doctor`` group."""
    parent.add_command(glitchtip_cmd)
