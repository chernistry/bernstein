"""Tests for the GlitchTip insights surface and ``bernstein doctor glitchtip``.

Covers the data fetcher, baseline persistence, nudge logic, soft-fail
behaviour when env vars are missing, and the Click wiring. The HTTP
client is replaced by a tiny dependency-injected stub so the suite
stays hermetic and fast.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from bernstein.cli.commands.advanced_cmd import doctor as doctor_group
from bernstein.cli.commands.doctor.glitchtip import (
    Baseline,
    baseline_from_result,
    default_baseline_path,
    detect_new_issues,
    glitchtip_cmd,
    load_baseline,
    suggest_nudge_line,
    write_baseline,
)
from bernstein.core.observability.glitchtip_insights import (
    DEFAULT_ORG_SLUG,
    ENV_GLITCHTIP_BASE_URL,
    ENV_GLITCHTIP_DSN,
    ENV_GLITCHTIP_ORG,
    ENV_GLITCHTIP_TOKEN,
    KNOWN_LEVELS,
    GlitchTipIssue,
    InsightsResult,
    count_new_since,
    fetch_insights,
    summarise_severity,
    top_unresolved,
)

# Illustrative base URL used across the suite. The package ships with no
# hardcoded host; tests must supply one explicitly or via a DSN.
_TEST_BASE_URL = "https://glitchtip.example.com"

# ---------------------------------------------------------------------------
# Stub HTTP getter
# ---------------------------------------------------------------------------


def _issue_row(
    short_id: str,
    *,
    level: str = "error",
    status: str = "unresolved",
    count: int = 1,
    first_seen: str = "2026-05-20T00:00:00Z",
) -> dict[str, Any]:
    """Build a minimal GlitchTip API row used by the stub responder."""
    return {
        "shortId": short_id,
        "title": f"issue {short_id}",
        "level": level,
        "status": status,
        "count": str(count),
        "userCount": "0",
        "firstSeen": first_seen,
        "lastSeen": first_seen,
        "permalink": f"{_TEST_BASE_URL}/bernstein/issues/{short_id}",
    }


def _make_getter(payload_24h: Any, payload_7d: Any | None = None) -> Any:
    """Return a getter callable matching ``_http_get`` for injection."""

    def _getter(url: str, token: str, timeout: float) -> tuple[int, Any]:
        assert token, "stub getter requires a non-empty token"
        if "7d" in url:
            return 200, payload_7d if payload_7d is not None else []
        return 200, payload_24h

    return _getter


# ---------------------------------------------------------------------------
# fetch_insights
# ---------------------------------------------------------------------------


def test_fetch_insights_soft_fails_when_token_missing() -> None:
    """No token -> ok=False with a precise reason; no HTTP call."""
    called = {"n": 0}

    def _getter(*_args: Any, **_kwargs: Any) -> tuple[int, Any]:
        called["n"] += 1
        return 200, []

    result = fetch_insights(env={}, http_get=_getter)

    assert called["n"] == 0
    assert not result.ok
    assert ENV_GLITCHTIP_TOKEN in result.reason
    # No hardcoded host: base_url is empty until configured.
    assert result.base_url == ""
    assert result.org_slug == DEFAULT_ORG_SLUG


def test_fetch_insights_soft_fails_when_base_url_unresolved() -> None:
    """A token but no base URL or DSN host -> ok=False; no HTTP call.

    Guards the privacy contract: with no base URL configured the feature
    must never fall back to any specific host.
    """
    called = {"n": 0}

    def _getter(*_args: Any, **_kwargs: Any) -> tuple[int, Any]:
        called["n"] += 1
        return 200, []

    result = fetch_insights(env={ENV_GLITCHTIP_TOKEN: "tok"}, http_get=_getter)

    assert called["n"] == 0
    assert not result.ok
    assert result.base_url == ""
    assert ENV_GLITCHTIP_BASE_URL in result.reason


def test_fetch_insights_derives_base_url_from_dsn_host() -> None:
    """When no base URL is set, the host is derived from the DSN."""
    captured: dict[str, str] = {}

    def _getter(url: str, token: str, timeout: float) -> tuple[int, Any]:
        captured["url"] = url
        return 200, []

    fetch_insights(
        env={
            ENV_GLITCHTIP_TOKEN: "tok",
            ENV_GLITCHTIP_DSN: "https://pub_key@glitchtip.example.com/42",
        },
        http_get=_getter,
    )
    assert captured["url"].startswith("https://glitchtip.example.com/api/0/organizations/bernstein/issues/")


def test_fetch_insights_populates_from_24h_payload() -> None:
    """A well-formed payload yields severity buckets and top unresolved."""
    payload = [
        _issue_row("A", level="error", count=5),
        _issue_row("B", level="warning", count=2),
        _issue_row("C", level="error", status="resolved", count=99),
    ]
    result = fetch_insights(
        env={ENV_GLITCHTIP_TOKEN: "tok", ENV_GLITCHTIP_BASE_URL: _TEST_BASE_URL},
        http_get=_make_getter(payload),
    )

    assert result.ok
    assert result.issues_24h == 3
    assert result.severity_24h["error"] == 5 + 99
    assert result.severity_24h["warning"] == 2
    # resolved issues drop out of the top-N list
    ids = [i.short_id for i in result.top_unresolved]
    assert "C" not in ids
    assert ids[0] == "A"  # higher count wins


def test_fetch_insights_handles_non_2xx() -> None:
    """A 5xx response soft-fails with the status code in the reason."""

    def _getter(*_args: Any, **_kwargs: Any) -> tuple[int, Any]:
        return 503, None

    result = fetch_insights(
        env={ENV_GLITCHTIP_TOKEN: "tok", ENV_GLITCHTIP_BASE_URL: _TEST_BASE_URL},
        http_get=_getter,
    )
    assert not result.ok
    assert "503" in result.reason


def test_fetch_insights_uses_env_overrides() -> None:
    """Base URL and org slug overrides flow into the result."""
    captured: dict[str, str] = {}

    def _getter(url: str, token: str, timeout: float) -> tuple[int, Any]:
        captured["url"] = url
        return 200, []

    fetch_insights(
        env={
            ENV_GLITCHTIP_TOKEN: "tok",
            ENV_GLITCHTIP_BASE_URL: "https://custom.example.test/",
            ENV_GLITCHTIP_ORG: "custom-org",
        },
        http_get=_getter,
    )
    assert captured["url"].startswith("https://custom.example.test/api/0/organizations/custom-org/issues/")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_summarise_severity_zero_fills_known_levels() -> None:
    """Every known level is present even when there are no matching issues."""
    counts = summarise_severity([])
    for level in KNOWN_LEVELS:
        assert level in counts
    assert counts["other"] == 0


def test_top_unresolved_orders_by_count_desc() -> None:
    """Higher count beats lower count regardless of input order."""
    issues = [
        GlitchTipIssue("A", "a", "error", "unresolved", 1, 0, "", "", ""),
        GlitchTipIssue("B", "b", "error", "unresolved", 9, 0, "", "", ""),
        GlitchTipIssue("C", "c", "error", "unresolved", 4, 0, "", "", ""),
    ]
    top = top_unresolved(issues, limit=2)
    assert [i.short_id for i in top] == ["B", "C"]


def test_top_unresolved_filters_resolved() -> None:
    """Resolved issues are excluded from the surfaced list."""
    issues = [
        GlitchTipIssue("A", "a", "error", "resolved", 99, 0, "", "", ""),
        GlitchTipIssue("B", "b", "error", "unresolved", 1, 0, "", "", ""),
    ]
    assert [i.short_id for i in top_unresolved(issues)] == ["B"]


def test_count_new_since_uses_first_seen() -> None:
    """Only issues first-seen at or after the cutoff are counted."""
    cutoff = dt.datetime(2026, 5, 20, tzinfo=dt.UTC)
    issues = [
        GlitchTipIssue("A", "a", "error", "unresolved", 1, 0, "2026-05-19T23:59:59Z", "", ""),
        GlitchTipIssue("B", "b", "error", "unresolved", 1, 0, "2026-05-20T00:00:01Z", "", ""),
        GlitchTipIssue("C", "c", "error", "unresolved", 1, 0, "not-iso", "", ""),
    ]
    assert count_new_since(issues, cutoff) == 1


# ---------------------------------------------------------------------------
# Baseline cache
# ---------------------------------------------------------------------------


def test_baseline_roundtrip(tmp_path: Path) -> None:
    """A written baseline reloads with the same shape."""
    target = tmp_path / "baseline.json"
    baseline = Baseline(checked_at="2026-05-20T00:00:00+00:00", issues_24h=3, last_short_id="BERNSTEIN-7")
    write_baseline(baseline, target)
    loaded = load_baseline(target)
    assert loaded == baseline


def test_load_baseline_returns_none_for_missing_file(tmp_path: Path) -> None:
    """A non-existent path is the expected fresh-install state."""
    assert load_baseline(tmp_path / "missing.json") is None


def test_load_baseline_returns_none_for_malformed_json(tmp_path: Path) -> None:
    """Corrupt JSON degrades to None rather than crashing."""
    target = tmp_path / "bad.json"
    target.write_text("{not json", encoding="utf-8")
    assert load_baseline(target) is None


def test_default_baseline_path_uses_override(monkeypatch: Any, tmp_path: Path) -> None:
    """The env override wins over XDG and the home fallback."""
    custom = tmp_path / "custom" / "baseline.json"
    monkeypatch.setenv("BERNSTEIN_GLITCHTIP_BASELINE", str(custom))
    assert default_baseline_path() == custom


def test_default_baseline_path_uses_xdg(monkeypatch: Any, tmp_path: Path) -> None:
    """XDG_DATA_HOME wins over the home fallback when the override is absent."""
    monkeypatch.delenv("BERNSTEIN_GLITCHTIP_BASELINE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_baseline_path() == tmp_path / "bernstein" / "glitchtip-baseline.json"


def test_detect_new_issues_no_baseline() -> None:
    """Without a baseline the result reports its own ``new_24h`` count."""
    result = InsightsResult(ok=True, issues_24h=4, new_24h=2)
    assert detect_new_issues(result, None) == 2


def test_detect_new_issues_with_baseline_diff() -> None:
    """The larger of ``new_24h`` and the baseline delta wins."""
    baseline = Baseline(checked_at="2026-05-19T00:00:00+00:00", issues_24h=3, last_short_id="X")
    result = InsightsResult(ok=True, issues_24h=10, new_24h=1)
    # delta = 7, new_24h = 1 -> 7 wins
    assert detect_new_issues(result, baseline) == 7


def test_detect_new_issues_ignores_unfetched_result() -> None:
    """A soft-failed result never yields a positive delta."""
    result = InsightsResult(ok=False, reason="unreachable")
    baseline = Baseline(checked_at="2026-05-19T00:00:00+00:00", issues_24h=0, last_short_id="")
    assert detect_new_issues(result, baseline) == 0


# ---------------------------------------------------------------------------
# Nudge helper
# ---------------------------------------------------------------------------


def test_suggest_nudge_line_returns_none_without_token(tmp_path: Path) -> None:
    """Missing token -> no nudge line."""
    assert suggest_nudge_line(env={}, baseline_path=tmp_path / "baseline.json") is None


def test_suggest_nudge_line_returns_none_on_soft_fail(tmp_path: Path) -> None:
    """A soft-failed fetch yields no nudge line."""

    def _fail(*_args: Any, **_kwargs: Any) -> InsightsResult:
        return InsightsResult(ok=False, reason="api down")

    line = suggest_nudge_line(
        env={ENV_GLITCHTIP_TOKEN: "tok"},
        baseline_path=tmp_path / "baseline.json",
        fetcher=_fail,
    )
    assert line is None


def test_suggest_nudge_line_emits_when_new_issues_detected(tmp_path: Path) -> None:
    """A populated result with deltas produces a single-line nudge."""

    def _fetch(*_args: Any, **_kwargs: Any) -> InsightsResult:
        return InsightsResult(ok=True, issues_24h=5, new_24h=3)

    line = suggest_nudge_line(
        env={ENV_GLITCHTIP_TOKEN: "tok"},
        baseline_path=tmp_path / "baseline.json",
        fetcher=_fetch,
    )
    assert line is not None
    assert "3 new unresolved" in line
    # Baseline must be persisted so the next invocation compares against it.
    assert (tmp_path / "baseline.json").exists()


def test_suggest_nudge_line_silent_when_no_delta(tmp_path: Path) -> None:
    """A populated result with no deltas yields no nudge line."""

    target = tmp_path / "baseline.json"
    write_baseline(
        Baseline(checked_at="2026-05-20T00:00:00+00:00", issues_24h=5, last_short_id="X"),
        target,
    )

    def _fetch(*_args: Any, **_kwargs: Any) -> InsightsResult:
        return InsightsResult(ok=True, issues_24h=5, new_24h=0)

    line = suggest_nudge_line(
        env={ENV_GLITCHTIP_TOKEN: "tok"},
        baseline_path=target,
        fetcher=_fetch,
    )
    assert line is None


# ---------------------------------------------------------------------------
# Baseline-from-result
# ---------------------------------------------------------------------------


def test_baseline_from_result_captures_count_and_top_id() -> None:
    """The baseline records the 24h count and the first top issue's id."""
    result = InsightsResult(
        ok=True,
        issues_24h=4,
        new_24h=4,
        top_unresolved=[
            GlitchTipIssue("X", "x", "error", "unresolved", 9, 0, "", "", ""),
        ],
    )
    baseline = baseline_from_result(result)
    assert baseline.issues_24h == 4
    assert baseline.last_short_id == "X"


# ---------------------------------------------------------------------------
# Click wiring
# ---------------------------------------------------------------------------


def test_cli_soft_fails_when_env_not_wired(monkeypatch: Any, tmp_path: Path) -> None:
    """No token, no DSN -> the command exits 0 with a yellow warning."""
    monkeypatch.delenv(ENV_GLITCHTIP_TOKEN, raising=False)
    monkeypatch.delenv("BERNSTEIN_GLITCHTIP_DSN", raising=False)
    monkeypatch.setenv("BERNSTEIN_GLITCHTIP_BASELINE", str(tmp_path / "baseline.json"))

    runner = CliRunner()
    result = runner.invoke(glitchtip_cmd, [])
    assert result.exit_code == 0
    assert "unavailable" in result.output.lower()


def test_cli_json_output_when_unwired(monkeypatch: Any, tmp_path: Path) -> None:
    """``--json`` returns a machine-readable soft-fail payload."""
    monkeypatch.delenv(ENV_GLITCHTIP_TOKEN, raising=False)
    monkeypatch.delenv("BERNSTEIN_GLITCHTIP_DSN", raising=False)
    monkeypatch.setenv("BERNSTEIN_GLITCHTIP_BASELINE", str(tmp_path / "baseline.json"))

    runner = CliRunner()
    result = runner.invoke(glitchtip_cmd, ["--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_cli_renders_table_when_wired(monkeypatch: Any, tmp_path: Path) -> None:
    """A live-style fetch returns issue counts and writes the baseline."""

    monkeypatch.setenv(ENV_GLITCHTIP_TOKEN, "tok")
    monkeypatch.setenv(ENV_GLITCHTIP_BASE_URL, _TEST_BASE_URL)
    monkeypatch.setenv("BERNSTEIN_GLITCHTIP_BASELINE", str(tmp_path / "baseline.json"))

    payload_24h = [_issue_row("Q", level="error", count=4)]

    # Patch the module-level fetcher so the CLI hits the stub instead of the network.
    from bernstein.cli.commands.doctor import glitchtip as gt_module

    def _fake(env: Any = None, top_n: int = 5) -> InsightsResult:
        from bernstein.core.observability.glitchtip_insights import fetch_insights

        return fetch_insights(env=env, http_get=_make_getter(payload_24h), top_n=top_n)

    monkeypatch.setattr(gt_module, "fetch_insights", _fake)

    runner = CliRunner()
    result = runner.invoke(glitchtip_cmd, ["--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["issues_24h"] == 1
    assert any(i["short_id"] == "Q" for i in payload["top_unresolved"])
    assert (tmp_path / "baseline.json").exists()


def test_doctor_group_lists_glitchtip_subcommand() -> None:
    """The ``glitchtip`` subcommand is attached to the existing doctor group."""
    assert "glitchtip" in doctor_group.commands
