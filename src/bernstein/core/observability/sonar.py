"""SonarQube insights client and baseline tracking for Bernstein.

Surfaces SonarQube measures (coverage, code smells by severity, bugs,
vulnerabilities, security hotspots, cognitive complexity hotspots) into
the operator's terminal flow via ``bernstein doctor sonar``.

Design notes
------------
- Network calls go through ``httpx`` with a short, explicit timeout so
  the doctor never hangs an operator's terminal.
- All inputs are validated and the module degrades to an empty
  ``SonarInsights`` rather than raising when env vars are missing or
  the server is unreachable. The doctor renders a "not configured"
  hint in that case.
- The baseline file lives under
  ``~/.local/share/bernstein/sonar-baseline.json`` and stores the
  last-seen counts plus a timestamp so the periodic nudge can compute
  deltas without paging through Sonar history.

Env vars (matching the CI contract):
  - ``SONAR_HOST_URL``: e.g. ``https://sonar.example.com``
  - ``SONAR_TOKEN``: a user token with ``Browse`` permission on the project
  - ``SONAR_PROJECT_KEY``: optional, defaults to ``bernstein``
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_KEY = "bernstein"
ENV_HOST = "SONAR_HOST_URL"
ENV_TOKEN = "SONAR_TOKEN"
ENV_PROJECT_KEY = "SONAR_PROJECT_KEY"

# Metric keys we request from /api/measures/component. See
# https://docs.sonarsource.com/sonarqube-server/latest/user-guide/metric-definitions/
_METRIC_KEYS = (
    "coverage",
    "code_smells",
    "bugs",
    "vulnerabilities",
    "security_hotspots",
    "cognitive_complexity",
    "ncloc",
)

_SEVERITIES = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")

# Severity counts default to zero so callers can show a complete table
# even when Sonar omits buckets with no issues.
_SEVERITY_ZERO: dict[str, int] = dict.fromkeys(_SEVERITIES, 0)

# Default soft-fail nudge thresholds. The CLI may override via flags;
# this module only surfaces the raw values + deltas.
DEFAULT_SMELL_NUDGE = 50

DOC_POINTER = "docs/observability/sonar.md"

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class HotspotFile:
    """One file in the cognitive-complexity hotspot ranking."""

    path: str
    cognitive_complexity: int


@dataclass(frozen=True)
class SonarInsights:
    """Snapshot of Sonar measures for a single project at a single time."""

    project_key: str
    coverage_pct: float | None = None
    code_smells_total: int = 0
    smells_by_severity: dict[str, int] = field(default_factory=_SEVERITY_ZERO.copy)
    bugs: int = 0
    vulnerabilities: int = 0
    security_hotspots: int = 0
    cognitive_complexity: int = 0
    ncloc: int = 0
    hotspots: tuple[HotspotFile, ...] = field(default_factory=tuple)
    fetched: bool = True
    note: str = ""


@dataclass(frozen=True)
class SonarConfig:
    """Resolved configuration for talking to a Sonar server."""

    host: str
    token: str
    project_key: str


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config(env: dict[str, str] | None = None) -> SonarConfig | None:
    """Return a :class:`SonarConfig` from env vars, or ``None`` when missing."""
    src = env if env is not None else os.environ
    host = (src.get(ENV_HOST) or "").strip()
    token = (src.get(ENV_TOKEN) or "").strip()
    if not host or not token:
        return None
    project_key = (src.get(ENV_PROJECT_KEY) or DEFAULT_PROJECT_KEY).strip() or DEFAULT_PROJECT_KEY
    return SonarConfig(host=host.rstrip("/"), token=token, project_key=project_key)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def _auth(token: str) -> tuple[str, str]:
    """Sonar uses HTTP basic with the token as username and empty password."""
    return token, ""


def _safe_int(value: Any) -> int:
    """Coerce a Sonar metric value to int, returning ``0`` on parse failure."""
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    """Coerce a Sonar metric value to float, returning ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_measures(
    config: SonarConfig,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Fetch raw measures for the configured project."""
    url = f"{config.host}/api/measures/component"
    params = {
        "component": config.project_key,
        "metricKeys": ",".join(_METRIC_KEYS),
    }
    try:
        if client is None:
            with httpx.Client(timeout=timeout, auth=_auth(config.token)) as c:
                resp = c.get(url, params=params)
        else:
            resp = client.get(url, params=params)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    component = payload.get("component")
    if not isinstance(component, dict):
        return None
    measures = component.get("measures", [])
    if not isinstance(measures, list):
        return None
    by_metric: dict[str, Any] = {}
    for item in measures:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        if isinstance(metric, str):
            by_metric[metric] = item.get("value")
    return by_metric


def fetch_smell_severities(
    config: SonarConfig,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, int]:
    """Return ``{severity: count}`` for open CODE_SMELL issues."""
    url = f"{config.host}/api/issues/search"
    params = {
        "componentKeys": config.project_key,
        "types": "CODE_SMELL",
        "resolved": "false",
        "facets": "severities",
        "ps": "1",
    }
    out: dict[str, int] = _SEVERITY_ZERO.copy()
    try:
        if client is None:
            with httpx.Client(timeout=timeout, auth=_auth(config.token)) as c:
                resp = c.get(url, params=params)
        else:
            resp = client.get(url, params=params)
    except httpx.HTTPError:
        return out
    if resp.status_code != 200:
        return out
    try:
        payload = resp.json()
    except ValueError:
        return out
    if not isinstance(payload, dict):
        return out
    facets = payload.get("facets", [])
    if not isinstance(facets, list):
        return out
    for facet in facets:
        if not isinstance(facet, dict) or facet.get("property") != "severities":
            continue
        values = facet.get("values", [])
        if not isinstance(values, list):
            continue
        for entry in values:
            if not isinstance(entry, dict):
                continue
            severity = entry.get("val")
            count = entry.get("count")
            if isinstance(severity, str) and severity in out:
                out[severity] = _safe_int(count)
    return out


def fetch_complexity_hotspots(
    config: SonarConfig,
    *,
    client: httpx.Client | None = None,
    top_n: int = 5,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[HotspotFile, ...]:
    """Return the top-N files ranked by cognitive complexity."""
    if top_n <= 0:
        return ()
    url = f"{config.host}/api/measures/component_tree"
    params = {
        "component": config.project_key,
        "metricKeys": "cognitive_complexity",
        "qualifiers": "FIL",
        "s": "metric",
        "metricSort": "cognitive_complexity",
        "asc": "false",
        "ps": str(top_n),
    }
    try:
        if client is None:
            with httpx.Client(timeout=timeout, auth=_auth(config.token)) as c:
                resp = c.get(url, params=params)
        else:
            resp = client.get(url, params=params)
    except httpx.HTTPError:
        return ()
    if resp.status_code != 200:
        return ()
    try:
        payload = resp.json()
    except ValueError:
        return ()
    if not isinstance(payload, dict):
        return ()
    components = payload.get("components", [])
    if not isinstance(components, list):
        return ()
    out: list[HotspotFile] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        path = comp.get("path") or comp.get("key")
        if not isinstance(path, str):
            continue
        measures = comp.get("measures", [])
        if not isinstance(measures, list):
            continue
        value: int = 0
        for measure in measures:
            if isinstance(measure, dict) and measure.get("metric") == "cognitive_complexity":
                value = _safe_int(measure.get("value"))
                break
        out.append(HotspotFile(path=path, cognitive_complexity=value))
    return tuple(out[:top_n])


def collect_insights(
    config: SonarConfig,
    *,
    client: httpx.Client | None = None,
    top_n_hotspots: int = 5,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> SonarInsights:
    """Aggregate measures, severities, and hotspots into a single snapshot."""
    measures = fetch_measures(config, client=client, timeout=timeout)
    if measures is None:
        return SonarInsights(
            project_key=config.project_key,
            fetched=False,
            note="server unreachable or project not yet scanned",
        )
    severities = fetch_smell_severities(config, client=client, timeout=timeout)
    hotspots = fetch_complexity_hotspots(
        config,
        client=client,
        top_n=top_n_hotspots,
        timeout=timeout,
    )
    return SonarInsights(
        project_key=config.project_key,
        coverage_pct=_safe_float(measures.get("coverage")),
        code_smells_total=_safe_int(measures.get("code_smells")),
        smells_by_severity=severities,
        bugs=_safe_int(measures.get("bugs")),
        vulnerabilities=_safe_int(measures.get("vulnerabilities")),
        security_hotspots=_safe_int(measures.get("security_hotspots")),
        cognitive_complexity=_safe_int(measures.get("cognitive_complexity")),
        ncloc=_safe_int(measures.get("ncloc")),
        hotspots=hotspots,
        fetched=True,
    )


# ---------------------------------------------------------------------------
# Baseline tracking for the nudge
# ---------------------------------------------------------------------------


def _xdg_data_home() -> Path:
    """Return the XDG_DATA_HOME path with the documented fallback."""
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def baseline_path() -> Path:
    """Resolved on-disk path for the baseline JSON."""
    return _xdg_data_home() / "bernstein" / "sonar-baseline.json"


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    """Read the last-seen baseline, returning ``{}`` on any error."""
    target = path or baseline_path()
    if not target.exists():
        return {}
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def save_baseline(insights: SonarInsights, *, path: Path | None = None) -> None:
    """Persist the current insights as the new baseline."""
    target = path or baseline_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    payload = {
        "project_key": insights.project_key,
        "coverage_pct": insights.coverage_pct,
        "code_smells_total": insights.code_smells_total,
        "smells_by_severity": insights.smells_by_severity.copy(),
        "bugs": insights.bugs,
        "vulnerabilities": insights.vulnerabilities,
        "security_hotspots": insights.security_hotspots,
        "cognitive_complexity": insights.cognitive_complexity,
        "ncloc": insights.ncloc,
        "hotspots": [asdict(h) for h in insights.hotspots],
    }
    try:
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


@dataclass(frozen=True)
class NudgeSignal:
    """Operator-visible nudge derived from a snapshot + baseline."""

    should_nudge: bool
    reasons: tuple[str, ...]


def evaluate_nudge(
    insights: SonarInsights,
    baseline: dict[str, Any],
    *,
    smell_threshold: int = DEFAULT_SMELL_NUDGE,
) -> NudgeSignal:
    """Decide whether to surface a nudge to the operator."""
    if not insights.fetched:
        return NudgeSignal(should_nudge=False, reasons=())
    reasons: list[str] = []
    if insights.code_smells_total > smell_threshold:
        reasons.append(f"{insights.code_smells_total} code smells (threshold {smell_threshold})")
    prev_vulns_raw = baseline.get("vulnerabilities")
    prev_vulns = _safe_int(prev_vulns_raw) if prev_vulns_raw is not None else None
    if prev_vulns is not None and insights.vulnerabilities > prev_vulns:
        delta = insights.vulnerabilities - prev_vulns
        reasons.append(f"{delta} new vulnerability(ies) since last check")
    return NudgeSignal(should_nudge=bool(reasons), reasons=tuple(reasons))
