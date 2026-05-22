"""Backend probes for the observability doctor.

Each probe returns a :class:`BackendReport` with a list of metric rows
plus an overall :class:`ProbeStatus`. Probes are deliberately small,
synchronous, and tolerant of missing credentials: when a backend is not
configured the probe returns ``status=ProbeStatus.SKIPPED`` with an
empty metric list so the umbrella command can keep going.

Persistence: each probe caches its last numeric values to
``.sdd/observability/<backend>.json`` so the next run can compute a
``delta-since-last-check`` column. The cache is operator-readable JSON
and may be deleted at any time without breaking the probe.

The Sonar and GlitchTip probes try to import their richer sibling
modules (``bernstein.core.observability.sonar`` and
``bernstein.core.observability.glitchtip_insights``) when available so
the umbrella reuses the same backend contract as the per-backend
``bernstein doctor sonar`` / ``bernstein doctor glitchtip`` commands.
If those modules are missing, the probes fall back to a direct API
call.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


class ProbeStatus(StrEnum):
    """Overall status reported by a single backend probe."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class MetricRow:
    """A single metric row in a backend report."""

    name: str
    value: str
    numeric: float | None = None
    threshold: str = ""
    threshold_status: str = "info"
    delta: str = "-"


@dataclass
class BackendReport:
    """Result of a single backend probe."""

    backend: str
    status: ProbeStatus
    detail: str = ""
    metrics: list[MetricRow] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view."""

        return {
            "backend": self.backend,
            "status": self.status.value,
            "detail": self.detail,
            "error": self.error,
            "metrics": [dataclasses.asdict(m) for m in self.metrics],
        }


def _cache_dir(workdir: Path | None = None) -> Path:
    root = workdir or Path.cwd()
    return root / ".sdd" / "observability"


def load_previous(backend: str, workdir: Path | None = None) -> dict[str, float]:
    """Return the previous numeric snapshot for ``backend``."""

    path = _cache_dir(workdir) / f"{backend}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    metrics = data.get("metrics", {})
    return {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}


def save_snapshot(report: BackendReport, workdir: Path | None = None) -> None:
    """Persist the numeric metrics from ``report`` for the next run."""

    if report.status in (ProbeStatus.SKIPPED, ProbeStatus.ERROR):
        return
    cache_dir = _cache_dir(workdir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": report.backend,
        "captured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "metrics": {m.name: m.numeric for m in report.metrics if m.numeric is not None},
    }
    (cache_dir / f"{report.backend}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def apply_deltas(report: BackendReport, workdir: Path | None = None) -> BackendReport:
    """Annotate each metric row with a delta-since-last-check label."""

    previous = load_previous(report.backend, workdir=workdir)
    for row in report.metrics:
        if row.numeric is None:
            row.delta = "-"
            continue
        old = previous.get(row.name)
        if old is None:
            row.delta = "new"
            continue
        diff = row.numeric - old
        if abs(diff) < 1e-9:
            row.delta = "0"
        else:
            row.delta = f"{diff:+.2f}".rstrip("0").rstrip(".")
    return report


def _classify(
    value: float,
    *,
    warn_above: float | None = None,
    fail_above: float | None = None,
) -> str:
    """Bucket a numeric value into ``ok|warn|fail`` against thresholds."""

    if fail_above is not None and value >= fail_above:
        return "fail"
    if warn_above is not None and value >= warn_above:
        return "warn"
    return "ok"


def _security_fail_threshold(severity: str) -> int | None:
    """Return the failure threshold for security severity buckets."""

    if severity == "critical":
        return 1
    if severity == "high":
        return 5
    return None


def probe_sonar(env: dict[str, str] | None = None) -> BackendReport:
    """Probe SonarQube. Soft-fails if not configured.

    Reads ``SONAR_HOST_URL`` and ``SONAR_TOKEN`` from env. Tries the
    richer ``bernstein.core.observability.sonar`` module first; falls
    back to a quality-gate fetch if that is unavailable.
    """

    env = env or os.environ.copy()
    host = (env.get("SONAR_HOST_URL") or "").strip()
    token = (env.get("SONAR_TOKEN") or "").strip()
    if not host or not token:
        return BackendReport(
            backend="sonar",
            status=ProbeStatus.SKIPPED,
            detail="SONAR_HOST_URL or SONAR_TOKEN not set",
        )

    try:
        from bernstein.core.observability.sonar import (  # type: ignore[import-not-found]
            DEFAULT_SMELL_NUDGE,
            collect_insights,
            load_config,
        )

        cfg = load_config(env=env)
        if cfg is not None:
            insights = collect_insights(cfg)
            if not getattr(insights, "fetched", True):
                return BackendReport(
                    backend="sonar",
                    status=ProbeStatus.WARN,
                    detail=getattr(insights, "note", "soft-fail"),
                )

            coverage = getattr(insights, "coverage_pct", None)
            smells = int(getattr(insights, "code_smells_total", 0) or 0)
            bugs = int(getattr(insights, "bugs", 0) or 0)
            vulns = int(getattr(insights, "vulnerabilities", 0) or 0)
            hotspots = int(getattr(insights, "security_hotspots", 0) or 0)

            overall = ProbeStatus.OK
            if bugs > 0 or vulns > 0 or hotspots > 0:
                overall = ProbeStatus.WARN
            if smells >= DEFAULT_SMELL_NUDGE:
                overall = ProbeStatus.WARN

            rows = [
                MetricRow(
                    name="coverage_pct",
                    value=f"{coverage:.1f}%" if coverage is not None else "n/a",
                    numeric=float(coverage) if coverage is not None else None,
                    threshold="80.0%",
                    threshold_status=_classify(
                        100.0 - (coverage or 0.0),
                        warn_above=20.0,
                        fail_above=40.0,
                    )
                    if coverage is not None
                    else "info",
                ),
                MetricRow(
                    name="code_smells",
                    value=str(smells),
                    numeric=float(smells),
                    threshold=str(DEFAULT_SMELL_NUDGE),
                    threshold_status=_classify(
                        float(smells),
                        warn_above=DEFAULT_SMELL_NUDGE,
                        fail_above=DEFAULT_SMELL_NUDGE * 4,
                    ),
                ),
                MetricRow(
                    name="bugs",
                    value=str(bugs),
                    numeric=float(bugs),
                    threshold="0",
                    threshold_status=_classify(float(bugs), warn_above=1, fail_above=10),
                ),
                MetricRow(
                    name="vulnerabilities",
                    value=str(vulns),
                    numeric=float(vulns),
                    threshold="0",
                    threshold_status=_classify(float(vulns), warn_above=1, fail_above=5),
                ),
                MetricRow(
                    name="security_hotspots",
                    value=str(hotspots),
                    numeric=float(hotspots),
                    threshold="0",
                    threshold_status=_classify(float(hotspots), warn_above=1, fail_above=10),
                ),
            ]
            return BackendReport(
                backend="sonar",
                status=overall,
                detail=f"project {cfg.project_key}",
                metrics=rows,
            )
    except Exception as exc:
        # Fall back to the direct API call below. Log at debug so the
        # failure is discoverable without crashing the umbrella probe.
        _LOGGER.debug("sonar insights module failed, falling back: %s", exc)

    try:
        import httpx
    except ImportError:
        return BackendReport(
            backend="sonar",
            status=ProbeStatus.ERROR,
            error="httpx not installed",
        )
    project = (env.get("SONAR_PROJECT_KEY") or "").strip()
    try:
        params: dict[str, str] = {}
        if project:
            params["projectKey"] = project
        resp = httpx.get(
            f"{host.rstrip('/')}/api/qualitygates/project_status",
            params=params,
            auth=(token, ""),
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return BackendReport(backend="sonar", status=ProbeStatus.ERROR, error=str(exc))

    gate_status = (data.get("projectStatus") or {}).get("status", "UNKNOWN")
    overall = ProbeStatus.OK if gate_status == "OK" else ProbeStatus.WARN
    return BackendReport(
        backend="sonar",
        status=overall,
        detail=f"quality gate {gate_status}",
        metrics=[
            MetricRow(
                name="quality_gate",
                value=gate_status,
                numeric=1.0 if gate_status == "OK" else 0.0,
                threshold="OK",
                threshold_status="ok" if gate_status == "OK" else "warn",
            ),
        ],
    )


def probe_glitchtip(env: dict[str, str] | None = None) -> BackendReport:
    """Probe GlitchTip for open issues. Soft-fails if not configured.

    Delegates to
    :func:`bernstein.core.observability.glitchtip_insights.fetch_insights`
    when available so the umbrella reuses the same backend contract as
    ``bernstein doctor glitchtip``.
    """

    env = env or os.environ.copy()
    token = (env.get("BERNSTEIN_GLITCHTIP_TOKEN") or "").strip()
    if not token:
        return BackendReport(
            backend="glitchtip",
            status=ProbeStatus.SKIPPED,
            detail="BERNSTEIN_GLITCHTIP_TOKEN not set",
        )

    try:
        from bernstein.core.observability.glitchtip_insights import (  # type: ignore[import-not-found]
            fetch_insights,
        )
    except Exception:
        return BackendReport(
            backend="glitchtip",
            status=ProbeStatus.WARN,
            detail="glitchtip insights module not yet available",
        )

    try:
        result = fetch_insights(env=env)
    except Exception as exc:
        return BackendReport(backend="glitchtip", status=ProbeStatus.ERROR, error=str(exc))

    if not getattr(result, "ok", False):
        return BackendReport(
            backend="glitchtip",
            status=ProbeStatus.WARN,
            detail=getattr(result, "reason", "soft-fail"),
        )

    total = int(getattr(result, "issues_24h", 0) or 0)
    new_24h = int(getattr(result, "new_24h", 0) or 0)
    severity = dict(getattr(result, "severity_24h", {}) or {})

    overall = ProbeStatus.OK if total == 0 else ProbeStatus.WARN
    if severity.get("fatal", 0) > 0 or severity.get("error", 0) > 5:
        overall = ProbeStatus.WARN

    rows: list[MetricRow] = [
        MetricRow(
            name="issues_24h",
            value=str(total),
            numeric=float(total),
            threshold="0",
            threshold_status=_classify(float(total), warn_above=1, fail_above=25),
        ),
        MetricRow(
            name="new_24h",
            value=str(new_24h),
            numeric=float(new_24h),
            threshold="0",
            threshold_status=_classify(float(new_24h), warn_above=1, fail_above=10),
        ),
    ]
    for level in ("fatal", "error", "warning", "info"):
        count = int(severity.get(level, 0) or 0)
        rows.append(
            MetricRow(
                name=f"{level}_count",
                value=str(count),
                numeric=float(count),
                threshold="0" if level in ("fatal", "error") else "",
                threshold_status=_classify(
                    float(count),
                    warn_above=1 if level in ("fatal", "error") else None,
                    fail_above=10 if level in ("fatal", "error") else None,
                ),
            )
        )
    return BackendReport(
        backend="glitchtip",
        status=overall,
        detail=f"{total} issue(s) in last 24h ({new_24h} new)",
        metrics=rows,
    )


def probe_dt(env: dict[str, str] | None = None) -> BackendReport:
    """Probe Dependency-Track for vulnerability counts.

    Reads ``DTRACK_URL``, ``DTRACK_TOKEN``, and ``DTRACK_PROJECT`` (uuid)
    from env. Soft-fails if not configured.
    """

    env = env or os.environ.copy()
    url = (env.get("DTRACK_URL") or "").strip()
    token = (env.get("DTRACK_TOKEN") or "").strip()
    project = (env.get("DTRACK_PROJECT") or "").strip()
    if not url or not token or not project:
        return BackendReport(
            backend="dt",
            status=ProbeStatus.SKIPPED,
            detail="DTRACK_URL/TOKEN/PROJECT not set",
        )
    try:
        import httpx
    except ImportError:
        return BackendReport(backend="dt", status=ProbeStatus.ERROR, error="httpx not installed")
    try:
        resp = httpx.get(
            f"{url.rstrip('/')}/api/v1/finding/project/{project}",
            headers={"X-Api-Key": token},
            timeout=5.0,
        )
        resp.raise_for_status()
        findings = resp.json()
    except Exception as exc:
        return BackendReport(backend="dt", status=ProbeStatus.ERROR, error=str(exc))

    if not isinstance(findings, list):
        return BackendReport(
            backend="dt",
            status=ProbeStatus.ERROR,
            error="unexpected response shape",
        )

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unassigned": 0}
    for f in findings:
        vuln = f.get("vulnerability") or {}
        sev = (vuln.get("severity") or "unassigned").lower()
        counts[sev] = counts.get(sev, 0) + 1
    overall = ProbeStatus.OK
    if counts["critical"] > 0:
        overall = ProbeStatus.FAIL
    elif counts["high"] > 0:
        overall = ProbeStatus.WARN
    rows: list[MetricRow] = [
        MetricRow(
            name=f"{sev}_vulns",
            value=str(value),
            numeric=float(value),
            threshold="0" if sev in ("critical", "high") else "",
            threshold_status=_classify(
                float(value),
                warn_above=1 if sev in ("critical", "high", "medium") else None,
                fail_above=_security_fail_threshold(sev),
            ),
        )
        for sev, value in counts.items()
    ]
    return BackendReport(
        backend="dt",
        status=overall,
        detail=f"{sum(counts.values())} total finding(s)",
        metrics=rows,
    )


def probe_code_scanning(env: dict[str, str] | None = None) -> BackendReport:
    """Probe GitHub Code Scanning alerts.

    Reads ``GITHUB_TOKEN`` (with ``security_events: read``) and
    ``GITHUB_REPOSITORY`` (``owner/repo``) from env. Soft-fails if
    either is missing.
    """

    env = env or os.environ.copy()
    token = (env.get("GITHUB_TOKEN") or "").strip()
    repo = (env.get("GITHUB_REPOSITORY") or "").strip()
    if not token or not repo:
        return BackendReport(
            backend="code-scanning",
            status=ProbeStatus.SKIPPED,
            detail="GITHUB_TOKEN or GITHUB_REPOSITORY not set",
        )
    try:
        import httpx
    except ImportError:
        return BackendReport(
            backend="code-scanning",
            status=ProbeStatus.ERROR,
            error="httpx not installed",
        )
    api_base = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    try:
        resp = httpx.get(
            f"{api_base}/repos/{repo}/code-scanning/alerts",
            params={"state": "open", "per_page": "100"},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=5.0,
        )
        resp.raise_for_status()
        alerts = resp.json()
    except Exception as exc:
        return BackendReport(backend="code-scanning", status=ProbeStatus.ERROR, error=str(exc))

    if not isinstance(alerts, list):
        return BackendReport(
            backend="code-scanning",
            status=ProbeStatus.ERROR,
            error="unexpected response shape",
        )

    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "warning": 0, "note": 0}
    for a in alerts:
        rule = a.get("rule") or {}
        sev = (rule.get("security_severity_level") or rule.get("severity") or "warning").lower()
        by_severity[sev] = by_severity.get(sev, 0) + 1
    total = sum(by_severity.values())
    overall = ProbeStatus.OK
    if by_severity.get("critical", 0):
        overall = ProbeStatus.FAIL
    elif by_severity.get("high", 0):
        overall = ProbeStatus.WARN
    rows = [
        MetricRow(
            name="open_alerts",
            value=str(total),
            numeric=float(total),
            threshold="0",
            threshold_status=_classify(float(total), warn_above=1, fail_above=10),
        ),
    ]
    for sev in ("critical", "high", "medium", "low"):
        count = by_severity.get(sev, 0)
        rows.append(
            MetricRow(
                name=f"{sev}_alerts",
                value=str(count),
                numeric=float(count),
                threshold="0" if sev in ("critical", "high") else "",
                threshold_status=_classify(
                    float(count),
                    warn_above=1 if sev in ("critical", "high") else None,
                    fail_above=_security_fail_threshold(sev),
                ),
            )
        )
    return BackendReport(
        backend="code-scanning",
        status=overall,
        detail=f"{total} open alert(s)",
        metrics=rows,
    )


__all__ = [
    "BackendReport",
    "MetricRow",
    "ProbeStatus",
    "apply_deltas",
    "load_previous",
    "probe_code_scanning",
    "probe_dt",
    "probe_glitchtip",
    "probe_sonar",
    "save_snapshot",
]
