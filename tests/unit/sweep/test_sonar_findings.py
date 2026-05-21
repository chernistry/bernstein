"""Unit tests for ``scripts/sweep_sonar_findings.py``.

Covers de-dup, severity filter, per-day cap, idempotence, the public-safe
``## Why`` table, exclusive-create emission, and HTTP retry semantics.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from sweep_sonar_findings import (  # type: ignore[import-not-found]
    DEFAULT_BLURB,
    FORBIDDEN_SUBSTRINGS,
    RULE_FAMILY_BLURBS,
    SEVERITY_TO_PRIORITY,
    Finding,
    SonarAPIError,
    _assert_no_forbidden,
    _component_path,
    _request_with_retries,
    build_dedup_index,
    emit_ticket,
    fetch_findings,
    main,
    maybe_create_gh_issue,
    safe_why,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_ticket(
    dir_: Path,
    *,
    name: str,
    sonar_issue_key: str | None = None,
    sonar_rule: str | None = None,
    sonar_component: str | None = None,
    sonar_line: int | None = None,
    status: str = "open",
) -> Path:
    fm: dict[str, Any] = {
        "id": name.replace(".md", ""),
        "created": "2026-05-20",
        "status": status,
        "priority": "P1",
        "effort": "S",
    }
    if sonar_issue_key is not None:
        fm["sonar_issue_key"] = sonar_issue_key
    if sonar_rule is not None:
        fm["sonar_rule"] = sonar_rule
    if sonar_component is not None:
        fm["sonar_component"] = sonar_component
    if sonar_line is not None:
        fm["sonar_line"] = sonar_line
    body = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# body\n"
    path = dir_ / name
    path.write_text(body, encoding="utf-8")
    return path


def _args(
    *,
    fixture: Path,
    out_dir: Path,
    backlog_root: Path,
    severity_min: str = "MINOR",
    max_per_day: int = 100,
    dry_run: bool = False,
    create_gh_issues: bool = False,
    day: str = "2026-05-21",
) -> argparse.Namespace:
    return argparse.Namespace(
        severity_min=severity_min,
        max_per_day=max_per_day,
        out_dir=str(out_dir),
        backlog_root=str(backlog_root),
        dry_run=dry_run,
        create_gh_issues=create_gh_issues,
        fixture=str(fixture),
        day=day,
    )


# ---------------------------------------------------------------------------
# 1. backlog walker / de-dup
# ---------------------------------------------------------------------------


def test_parses_existing_frontmatter_for_dedup(sweep_workspace: Path) -> None:
    _write_ticket(
        sweep_workspace / "open",
        name="2026-05-19-fix-a.md",
        sonar_issue_key="KEY1",
    )
    _write_ticket(
        sweep_workspace / "done",
        name="2026-05-18-fix-b.md",
        sonar_issue_key="KEY2",
        status="closed_hit",
    )
    roots = [
        sweep_workspace / "open",
        sweep_workspace / "claimed",
        sweep_workspace / "closed",
        sweep_workspace / "done",
        sweep_workspace / "deferred",
    ]
    index = build_dedup_index(roots)
    assert index.keys == frozenset({"KEY1", "KEY2"})


def test_skips_dup_by_key(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
    tmp_path: Path,
) -> None:
    out_dir = sweep_workspace / "open"
    _write_ticket(out_dir, name="existing.md", sonar_issue_key="FINDING-BLOCKER-001")
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "BLOCKER",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    new = [p for p in out_dir.glob("2026-05-21-*.md")]
    assert new == []


def test_skips_dup_by_rule_component_line(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
) -> None:
    out_dir = sweep_workspace / "open"
    # Existing open ticket with the same (rule, component, line) but a stale key.
    _write_ticket(
        out_dir,
        name="2026-05-15-refactor-stale.md",
        sonar_issue_key="STALE-KEY-NOT-MATCHING",
        sonar_rule="python:S3776",
        sonar_component="src/bernstein/core/agents/spawn_supervisor.py",
        sonar_line=230,
    )
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "CRITICAL",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    # The FINDING-CRITICAL-001 finding matches the stale ticket's (rule, component, line)
    # and must be skipped. Other findings can still be emitted.
    emitted_files = list(out_dir.glob("2026-05-21-*.md"))
    for path in emitted_files:
        fm = yaml.safe_load(path.read_text().split("---")[1])
        assert fm["sonar_issue_key"] != "FINDING-CRITICAL-001"


# ---------------------------------------------------------------------------
# 4. idempotence
# ---------------------------------------------------------------------------


def test_idempotent_double_run(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
) -> None:
    out_dir = sweep_workspace / "open"
    rc1 = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MINOR",
            "--max-per-day",
            "100",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc1 == 0
    first_count = len(list(out_dir.glob("2026-05-21-*.md")))
    assert first_count > 0
    rc2 = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MINOR",
            "--max-per-day",
            "100",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc2 == 0
    second_count = len(list(out_dir.glob("2026-05-21-*.md")))
    assert second_count == first_count


# ---------------------------------------------------------------------------
# 5 + 6. per-day cap and severity filter
# ---------------------------------------------------------------------------


def test_per_day_cap(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
) -> None:
    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MINOR",
            "--max-per-day",
            "2",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    files = sorted(out_dir.glob("2026-05-21-*.md"))
    assert len(files) == 2
    # The two highest-rank findings should win: 1 BLOCKER, then 1 of the CRITICALs.
    parsed = [yaml.safe_load(p.read_text().split("---")[1]) for p in files]
    severities = sorted(p["sonar_severity"] for p in parsed)
    assert "BLOCKER" in severities
    assert "CRITICAL" in severities


def test_severity_filter(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
) -> None:
    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "CRITICAL",
            "--max-per-day",
            "100",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    files = list(out_dir.glob("2026-05-21-*.md"))
    severities = []
    for f in files:
        fm = yaml.safe_load(f.read_text().split("---")[1])
        severities.append(fm["sonar_severity"])
    assert all(s in ("BLOCKER", "CRITICAL") for s in severities)
    assert "MAJOR" not in severities
    assert "MINOR" not in severities


# ---------------------------------------------------------------------------
# 7 + 8 + 9. safe_why behaviour
# ---------------------------------------------------------------------------


def test_safe_why_known_rule() -> None:
    text = safe_why("python:S3776", "CRITICAL", "x", 1)
    assert "cognitive complexity" in text.lower()


def test_safe_why_unknown_rule_fallback() -> None:
    text = safe_why("python:S99999", "MINOR", "x", None)
    assert "python:S99999" in text
    assert "static-analysis" in text.lower()


def test_safe_why_no_forbidden_substrings() -> None:
    for prefix, _category, blurb in RULE_FAMILY_BLURBS:
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden.lower() not in blurb.lower(), (
                f"blurb for {prefix} contains forbidden substring {forbidden!r}"
            )
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden.lower() not in DEFAULT_BLURB.lower(), (
            f"DEFAULT_BLURB contains forbidden substring {forbidden!r}"
        )


# ---------------------------------------------------------------------------
# 10 + 14 + 15. ticket emission
# ---------------------------------------------------------------------------


def test_filename_collision_uses_exclusive_create(tmp_path: Path) -> None:
    finding = Finding(
        key="K1",
        rule="python:S3776",
        severity="CRITICAL",
        type="CODE_SMELL",
        component="bernstein:src/foo.py",
        line=10,
        creation_date="2026-05-20T08:00:00+0000",
    )
    out_dir = tmp_path / "open"
    out_dir.mkdir()
    # Pre-populate the exact filename the emitter would pick.
    path, _body1, wrote1 = emit_ticket(finding, out_dir, day="2026-05-21")
    assert wrote1 is True
    assert path.exists()
    # Second emit should hit the exclusive-create branch and NOT overwrite.
    pre_text = path.read_text()
    path2, _body2, wrote2 = emit_ticket(finding, out_dir, day="2026-05-21")
    assert path2 == path
    assert wrote2 is False
    assert path.read_text() == pre_text


def test_emitted_frontmatter_contract(tmp_path: Path) -> None:
    finding = Finding(
        key="K2",
        rule="python:S1192",
        severity="MAJOR",
        type="CODE_SMELL",
        component="bernstein:src/bar.py",
        line=20,
        creation_date="2026-05-20T08:00:00+0000",
    )
    out_dir = tmp_path / "open"
    out_dir.mkdir()
    path, _body, wrote = emit_ticket(finding, out_dir, day="2026-05-21")
    assert wrote is True
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    parts = text.split("---", 2)
    fm = yaml.safe_load(parts[1])
    for required in ("id", "created", "status", "priority", "effort"):
        assert required in fm, f"missing required key: {required}"
    for sweep_key in (
        "sonar_issue_key",
        "sonar_rule",
        "sonar_component",
        "sonar_severity",
        "sonar_type",
    ):
        assert sweep_key in fm, f"missing sweep key: {sweep_key}"
    assert fm["sonar_issue_key"] == "K2"
    assert fm["sonar_rule"] == "python:S1192"


def test_ticket_body_is_ascii_safe(tmp_path: Path) -> None:
    finding = Finding(
        key="K3",
        rule="python:S3776",
        severity="BLOCKER",
        type="CODE_SMELL",
        component="bernstein:src/baz.py",
        line=30,
        creation_date="2026-05-20T08:00:00+0000",
    )
    out_dir = tmp_path / "open"
    out_dir.mkdir()
    path, _body, wrote = emit_ticket(finding, out_dir, day="2026-05-21")
    assert wrote is True
    text = path.read_text(encoding="utf-8")
    # Reject em-dash and curly quotes per Bernstein hard constraints.
    # Use chr() so this test source itself stays ASCII-only.
    em_dash = chr(0x2014)
    left_single = chr(0x2018)
    right_single = chr(0x2019)
    left_double = chr(0x201C)
    right_double = chr(0x201D)
    assert em_dash not in text
    assert left_single not in text and right_single not in text
    assert left_double not in text and right_double not in text


# ---------------------------------------------------------------------------
# 11 + 12 + 13. HTTP client retry semantics
# ---------------------------------------------------------------------------


def _stub_sleep_calls() -> tuple[list[float], Any]:
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    return calls, _sleep


def test_5xx_retry_then_success() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"issues": [], "paging": {"pageIndex": 1, "pageSize": 500, "total": 0}})

    transport = httpx.MockTransport(handler)
    sleeps, sleep_fn = _stub_sleep_calls()
    with httpx.Client(transport=transport) as client:
        resp = _request_with_retries(client, "https://sonar.example.com/api/issues/search", {}, sleep_fn=sleep_fn)
    assert resp.status_code == 200
    assert calls["count"] == 3
    assert len(sleeps) == 2  # two backoff sleeps before the third (successful) try


def test_5xx_final_failure_exits_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still busy")

    transport = httpx.MockTransport(handler)
    _, sleep_fn = _stub_sleep_calls()
    with httpx.Client(transport=transport) as client:
        with pytest.raises(SonarAPIError):
            _request_with_retries(
                client,
                "https://sonar.example.com/api/issues/search",
                {},
                sleep_fn=sleep_fn,
            )


def test_429_respects_retry_after() -> None:
    seq = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seq["n"] += 1
        if seq["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(
            200,
            json={
                "issues": [],
                "paging": {"pageIndex": 1, "pageSize": 500, "total": 0},
            },
        )

    transport = httpx.MockTransport(handler)
    sleeps, sleep_fn = _stub_sleep_calls()
    with httpx.Client(transport=transport) as client:
        resp = _request_with_retries(
            client,
            "https://sonar.example.com/api/issues/search",
            {},
            sleep_fn=sleep_fn,
        )
    assert resp.status_code == 200
    assert sleeps == [3.0]


# ---------------------------------------------------------------------------
# fetch_findings paging integration
# ---------------------------------------------------------------------------


def test_fetch_findings_paginates() -> None:
    page1 = {
        "issues": [
            {
                "key": "k1",
                "rule": "python:S1481",
                "severity": "MAJOR",
                "type": "CODE_SMELL",
                "component": "bernstein:src/x.py",
                "line": 1,
                "creationDate": "2026-05-20T01:00:00+0000",
            }
        ],
        "paging": {"pageIndex": 1, "pageSize": 1, "total": 2},
    }
    page2 = {
        "issues": [
            {
                "key": "k2",
                "rule": "python:S1481",
                "severity": "MAJOR",
                "type": "CODE_SMELL",
                "component": "bernstein:src/y.py",
                "line": 2,
                "creationDate": "2026-05-20T02:00:00+0000",
            }
        ],
        "paging": {"pageIndex": 2, "pageSize": 1, "total": 2},
    }

    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = dict(request.url.params).get("p", "1")
        seen_pages.append(page)
        if page == "1":
            return httpx.Response(200, json=page1)
        return httpx.Response(200, json=page2)

    from sweep_sonar_findings import SonarConfig  # type: ignore[import-not-found]

    cfg = SonarConfig(host="https://sonar.example.com", token="t", project_key="bernstein")
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        findings = fetch_findings(
            cfg,
            severities=["MAJOR"],
            page_size=1,
            client=client,
            sleep_fn=lambda _x: None,
        )
    assert [f.key for f in findings] == ["k1", "k2"]
    assert seen_pages == ["1", "2"]


# ---------------------------------------------------------------------------
# Dry-run does not write
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_files(
    sweep_workspace: Path,
    issues_search_fixture_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MINOR",
            "--dry-run",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    assert list(out_dir.glob("2026-05-21-*.md")) == []
    captured = capsys.readouterr()
    assert "would-emit" in captured.out


# ---------------------------------------------------------------------------
# GH issue creation gating
# ---------------------------------------------------------------------------


def test_maybe_create_gh_issue_skips_when_disabled(tmp_path: Path) -> None:
    path = tmp_path / "t.md"
    path.write_text("body")
    assert maybe_create_gh_issue(path, "title", enable=False, priority="P0") is None


def test_maybe_create_gh_issue_p1_now_fires(tmp_path: Path) -> None:
    """MAJOR -> P1 promotes to a GH issue after the widening change."""
    path = tmp_path / "t.md"
    path.write_text("body")
    captured: dict[str, Any] = {}

    class _R:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_runner(cmd: list[str], **kwargs: Any) -> _R:
        captured["cmd"] = cmd
        return _R(0, "https://github.com/org/repo/issues/77\n")

    url = maybe_create_gh_issue(
        path,
        "title",
        enable=True,
        priority="P1",
        runner=fake_runner,
    )
    assert url == "https://github.com/org/repo/issues/77"
    assert captured["cmd"][:3] == ["gh", "issue", "create"]


def test_maybe_create_gh_issue_skips_p2(tmp_path: Path) -> None:
    """P2 tickets stay file-only and never auto-promote to a GH issue."""
    path = tmp_path / "t.md"
    path.write_text("body")
    assert maybe_create_gh_issue(path, "title", enable=True, priority="P2") is None


def test_maybe_create_gh_issue_calls_runner(tmp_path: Path) -> None:
    path = tmp_path / "t.md"
    path.write_text("body")
    captured: dict[str, Any] = {}

    class _R:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_runner(cmd: list[str], **kwargs: Any) -> _R:
        captured["cmd"] = cmd
        return _R(0, "https://github.com/org/repo/issues/42\n")

    url = maybe_create_gh_issue(path, "title", enable=True, priority="P0", runner=fake_runner)
    assert url == "https://github.com/org/repo/issues/42"
    assert captured["cmd"][:3] == ["gh", "issue", "create"]


# ---------------------------------------------------------------------------
# Component path stripping
# ---------------------------------------------------------------------------


def test_component_path_strips_project_prefix() -> None:
    assert _component_path("bernstein:src/foo/bar.py") == "src/foo/bar.py"
    assert _component_path("src/foo/bar.py") == "src/foo/bar.py"


# ---------------------------------------------------------------------------
# Severity to priority mapping
# ---------------------------------------------------------------------------


def test_severity_to_priority_mapping() -> None:
    """MAJOR moves from P2 to P1 so the queue actually drains.

    MAJOR findings accumulate faster than operators can hand-triage them,
    so they now auto-promote to GH issues alongside BLOCKER (P0) and
    CRITICAL (P1). MINOR and INFO stay at P2 (file-only).
    """
    assert SEVERITY_TO_PRIORITY["BLOCKER"] == "P0"
    assert SEVERITY_TO_PRIORITY["CRITICAL"] == "P1"
    assert SEVERITY_TO_PRIORITY["MAJOR"] == "P1"
    assert SEVERITY_TO_PRIORITY["MINOR"] == "P2"
    assert SEVERITY_TO_PRIORITY["INFO"] == "P2"


# ---------------------------------------------------------------------------
# Top-10 MAJOR rule families have vetted blurb coverage
# ---------------------------------------------------------------------------


def test_top_major_rules_have_blurb_coverage() -> None:
    """The top MAJOR rule families seen in production must have a blurb.

    Lifted from the open MAJOR cohort on the Sonar tracker so the next
    sweep run does not emit `DEFAULT_BLURB` placeholders.
    """
    top_major_rules = [
        "python:S5886",  # return-type mismatch
        "python:S5864",  # isinstance with non-type-like arg
        "python:S3358",  # nested ternary
        "python:S5869",  # regex character-class duplicates
        "python:S1244",  # float equality compare
        "python:S5843",  # regex super-linear backtracking
        "python:S1764",  # identical sub-expressions
        "python:S8495",  # generic typing misuse
        "python:S3923",  # all branches identical
    ]
    covered_prefixes = {prefix for prefix, _slug, _blurb in RULE_FAMILY_BLURBS}
    for rule in top_major_rules:
        assert rule in covered_prefixes, f"missing RULE_FAMILY_BLURBS entry for {rule}"


# ---------------------------------------------------------------------------
# Widened sweeper: MAJOR-severity scenarios
# ---------------------------------------------------------------------------


def test_major_finding_emits_p1_ticket(tmp_path: Path) -> None:
    """A MAJOR-severity finding emits a ticket whose priority is P1."""
    finding = Finding(
        key="MAJOR-K1",
        rule="python:S1481",
        severity="MAJOR",
        type="CODE_SMELL",
        component="bernstein:src/a.py",
        line=5,
        creation_date="2026-05-21T08:00:00+0000",
    )
    out_dir = tmp_path / "open"
    out_dir.mkdir()
    path, _body, wrote = emit_ticket(finding, out_dir, day="2026-05-21")
    assert wrote is True
    fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
    assert fm["priority"] == "P1"
    assert fm["sonar_severity"] == "MAJOR"


def test_capping_picks_blocker_first_then_major_in_receipt_order(
    sweep_workspace: Path,
    tmp_path: Path,
) -> None:
    """5 BLOCKER + 25 MAJOR + 5 MINOR with cap=25 keeps 5 BLOCKER + 20 MAJOR.

    The cap applies after severity sort (BLOCKER first), so all five
    BLOCKERs land and the remainder of the cap (20 slots) goes to the
    MAJOR cohort in receipt order (newest creation_date first).
    """
    issues = []
    for idx in range(5):
        issues.append(
            {
                "key": f"BLOCKER-K{idx:02d}",
                "rule": "python:S2068",
                "severity": "BLOCKER",
                "type": "VULNERABILITY",
                "component": f"bernstein:src/b{idx}.py",
                "line": idx + 1,
                "creationDate": f"2026-05-20T10:{idx:02d}:00+0000",
            }
        )
    for idx in range(25):
        issues.append(
            {
                "key": f"MAJOR-K{idx:02d}",
                "rule": "python:S1481",
                "severity": "MAJOR",
                "type": "CODE_SMELL",
                "component": f"bernstein:src/m{idx}.py",
                "line": idx + 100,
                "creationDate": f"2026-05-19T08:{idx:02d}:00+0000",
            }
        )
    for idx in range(5):
        issues.append(
            {
                "key": f"MINOR-K{idx:02d}",
                "rule": "python:S125",
                "severity": "MINOR",
                "type": "CODE_SMELL",
                "component": f"bernstein:src/n{idx}.py",
                "line": idx + 200,
                "creationDate": f"2026-05-18T08:{idx:02d}:00+0000",
            }
        )
    payload = {
        "paging": {"pageIndex": 1, "pageSize": 500, "total": len(issues)},
        "issues": issues,
    }
    fixture = tmp_path / "issues.json"
    fixture.write_text(__import__("json").dumps(payload), encoding="utf-8")

    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(fixture),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MAJOR",
            "--max-per-day",
            "25",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    files = sorted(out_dir.glob("2026-05-21-*.md"))
    assert len(files) == 25
    parsed = [yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) for p in files]
    severities = [p["sonar_severity"] for p in parsed]
    keys = {p["sonar_issue_key"] for p in parsed}
    # All 5 BLOCKERs must be present (highest priority bucket).
    assert severities.count("BLOCKER") == 5
    assert {f"BLOCKER-K{idx:02d}" for idx in range(5)} <= keys
    # Remaining 20 slots filled with MAJORs in newest-first receipt order:
    # the 5 oldest MAJORs (K00-K04) are dropped by the cap, K05-K24 survive.
    assert severities.count("MAJOR") == 20
    assert {f"MAJOR-K{idx:02d}" for idx in range(5, 25)} <= keys
    assert not any(f"MAJOR-K{idx:02d}" in keys for idx in range(5))
    # Severity-min=MAJOR excludes MINOR entirely.
    assert "MINOR" not in severities


def test_widen_fixture_caps_at_25_and_bodies_pass_guard(
    sweep_workspace: Path,
    issues_search_widen_fixture_path: Path,
) -> None:
    """On-disk widen fixture: severity-min=MAJOR, cap=25 -> 25 clean tickets.

    The fixture carries 5 BLOCKER + 25 MAJOR + 5 MINOR = 35 findings. With
    severity-min=MAJOR the MINORs drop, leaving 30 candidates; cap=25
    trims to 25 (5 BLOCKER + 20 MAJOR). Every emitted body must pass the
    public-artefact guard.
    """
    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_widen_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MAJOR",
            "--max-per-day",
            "25",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    files = sorted(out_dir.glob("2026-05-21-*.md"))
    assert len(files) == 25
    parsed = [yaml.safe_load(p.read_text(encoding="utf-8").split("---")[1]) for p in files]
    assert sum(1 for p in parsed if p["sonar_severity"] == "BLOCKER") == 5
    assert sum(1 for p in parsed if p["sonar_severity"] == "MAJOR") == 20
    assert not any(p["sonar_severity"] == "MINOR" for p in parsed)
    # Every emitted ticket body passes the forbidden-substring guard.
    for path in files:
        _assert_no_forbidden(path.read_text(encoding="utf-8"), context=str(path))


def test_widen_fixture_major_rules_use_vetted_blurbs(
    sweep_workspace: Path,
    issues_search_widen_fixture_path: Path,
) -> None:
    """No emitted MAJOR ticket falls back to DEFAULT_BLURB for the top rules."""
    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(issues_search_widen_fixture_path),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MAJOR",
            "--max-per-day",
            "100",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    files = sorted(out_dir.glob("2026-05-21-*.md"))
    fallback_marker = "Static-analysis finding flagged under rule key"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert fallback_marker not in text, f"{path} used DEFAULT_BLURB fallback"


def test_major_with_create_gh_issues_invokes_runner(
    sweep_workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAJOR finding + --create-gh-issues -> gh CLI is invoked once.

    The widening means MAJOR (P1) now opts in to GH-issue creation.
    """
    issues = [
        {
            "key": "MAJOR-K-GH",
            "rule": "python:S1481",
            "severity": "MAJOR",
            "type": "CODE_SMELL",
            "component": "bernstein:src/foo.py",
            "line": 12,
            "creationDate": "2026-05-20T08:00:00+0000",
        }
    ]
    payload = {
        "paging": {"pageIndex": 1, "pageSize": 500, "total": 1},
        "issues": issues,
    }
    fixture = tmp_path / "issues_gh.json"
    fixture.write_text(__import__("json").dumps(payload), encoding="utf-8")

    captured: dict[str, Any] = {"calls": 0, "cmds": []}

    class _R:
        def __init__(self, rc: int, out: str) -> None:
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_runner(cmd: list[str], **kwargs: Any) -> _R:
        captured["calls"] = captured["calls"] + 1
        captured["cmds"].append(cmd)
        return _R(0, "https://github.com/org/repo/issues/123\n")

    import sweep_sonar_findings as sweeper  # type: ignore[import-not-found]

    monkeypatch.setattr(sweeper.subprocess, "run", fake_runner)

    out_dir = sweep_workspace / "open"
    rc = main(
        argv=[
            "--fixture",
            str(fixture),
            "--out-dir",
            str(out_dir),
            "--backlog-root",
            str(sweep_workspace),
            "--severity-min",
            "MAJOR",
            "--max-per-day",
            "25",
            "--create-gh-issues",
            "--day",
            "2026-05-21",
        ]
    )
    assert rc == 0
    assert captured["calls"] == 1
    assert captured["cmds"][0][:3] == ["gh", "issue", "create"]
    # The ticket file should have the GH trailer appended.
    files = list(out_dir.glob("2026-05-21-*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "gh-issue-created:" in text
