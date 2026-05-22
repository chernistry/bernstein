"""Workflow YAML smoke test.

The Sonar findings sweep workflow ships with the cron trigger present
but gated behind ``ENABLE_CRON='0'``. This test pins the gate so
operators cannot accidentally enable the daily cron without flipping
the well-known env var inside the workflow file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "sweep-sonar-findings.yml"


def _load_workflow() -> dict:
    text = _WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), "workflow must parse as a YAML mapping"
    return parsed


def test_workflow_has_cron_and_dispatch() -> None:
    parsed = _load_workflow()
    # PyYAML parses the literal `on:` key as the boolean True.
    on = parsed.get(True) if True in parsed else parsed["on"]
    assert isinstance(on, dict)
    assert "schedule" in on
    schedule = on["schedule"]
    assert isinstance(schedule, list) and schedule
    crons = [item.get("cron") for item in schedule]
    assert "17 6 * * *" in crons
    assert "workflow_dispatch" in on


def test_workflow_cron_is_gated_off_by_default() -> None:
    parsed = _load_workflow()
    env = parsed.get("env") or {}
    # The gate env var must default to '0' (string). Operators flip it
    # to '1' in a follow-up PR after a clean smoke run.
    assert env.get("ENABLE_CRON") == "0"


def test_workflow_has_concurrency_group() -> None:
    parsed = _load_workflow()
    concurrency = parsed.get("concurrency") or {}
    assert concurrency.get("group") == "sweep-sonar-findings"
    assert concurrency.get("cancel-in-progress") is False


def test_workflow_pins_action_shas() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    # Every external `uses:` line must use a 40-char SHA pin, never a
    # floating tag. Repo-local composite actions are versioned with this
    # workflow and therefore do not have an `@<ref>` suffix.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("uses:"):
            continue
        ref = stripped.removeprefix("uses:").strip()
        if ref.startswith("./"):
            continue
        # actions/checkout@<sha> # vN.N.N -- split off the SHA.
        if "@" not in ref:
            raise AssertionError(f"uses line missing '@<sha>': {line!r}")
        _name, _, after = ref.partition("@")
        sha = after.split()[0]
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
            f"uses line not pinned to a 40-char SHA: {line!r}"
        )


def test_workflow_has_harden_runner() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "step-security/harden-runner@" in text


def test_workflow_top_level_permissions_are_narrow() -> None:
    parsed = _load_workflow()
    perms = parsed.get("permissions")
    assert perms == {"contents": "read"}, (
        "Top-level permissions must default to read-only; the sweep job grants its own write scopes."
    )


def test_workflow_dispatch_default_severity_is_major() -> None:
    """Default severity input must be MAJOR after the widening change."""
    parsed = _load_workflow()
    on = parsed.get(True) if True in parsed else parsed["on"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["severity_min"]["default"] == "MAJOR"


def test_workflow_dispatch_default_max_per_day_is_25() -> None:
    """Default per-run cap raised so the MAJOR queue drains in a sane timeline."""
    parsed = _load_workflow()
    on = parsed.get(True) if True in parsed else parsed["on"]
    inputs = on["workflow_dispatch"]["inputs"]
    # YAML may parse '25' as the string '25' or the int 25; accept either.
    assert str(inputs["max_per_day"]["default"]) == "25"
