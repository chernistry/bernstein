"""Unit tests for scripts/trunk_andon_gate_decision.py.

Regression guard for the trunk-andon merge-gate override logic.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "trunk_andon_gate_decision.py"


def _load_module():
    """Load trunk_andon_gate_decision as a module. Registered in
    sys.modules so dataclasses can find its module by name."""
    if "trunk_andon_gate_decision" in sys.modules:
        return sys.modules["trunk_andon_gate_decision"]
    spec = importlib.util.spec_from_file_location("trunk_andon_gate_decision", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["trunk_andon_gate_decision"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_trunk_healthy_passes_always() -> None:
    mod = _load_module()
    inp = mod.Inputs(unstable=False, labels=(), pr_body="", head_commit_msg="")
    assert mod.decide(inp) == ("pass", "trunk_healthy")


def test_trunk_unstable_no_override_fails() -> None:
    mod = _load_module()
    inp = mod.Inputs(unstable=True, labels=(), pr_body="", head_commit_msg="")
    assert mod.decide(inp) == ("fail", "trunk_unstable_no_override")


def test_hotfix_cleared_label_passes_during_unstable() -> None:
    mod = _load_module()
    inp = mod.Inputs(
        unstable=True,
        labels=("hotfix-cleared",),
        pr_body="",
        head_commit_msg="",
    )
    assert mod.decide(inp) == ("pass", "label_hotfix_cleared")


def test_force_merge_label_passes_during_unstable() -> None:
    """EDGE-5 escalation: force-merge above hotfix-cleared."""
    mod = _load_module()
    inp = mod.Inputs(
        unstable=True,
        labels=("force-merge",),
        pr_body="",
        head_commit_msg="",
    )
    assert mod.decide(inp) == ("pass", "label_force_merge")


def test_commit_override_token_in_body_passes() -> None:
    """EDGE-5 self-attestation: [trunk-andon-override] in PR body."""
    mod = _load_module()
    body = "Operator override: [trunk-andon-override]\nReason: bypass for emergency"
    inp = mod.Inputs(unstable=True, labels=(), pr_body=body, head_commit_msg="")
    assert mod.decide(inp) == ("pass", "commit_override")


def test_commit_override_token_in_head_commit_msg_passes() -> None:
    """Token in the head commit message body works too."""
    mod = _load_module()
    msg = "fix(bug): patch race\n\n[trunk-andon-override] override Andon for hotfix"
    inp = mod.Inputs(unstable=True, labels=(), pr_body="", head_commit_msg=msg)
    assert mod.decide(inp) == ("pass", "commit_override")


def test_irrelevant_label_does_not_override() -> None:
    mod = _load_module()
    inp = mod.Inputs(
        unstable=True,
        labels=("documentation", "wip"),
        pr_body="some body",
        head_commit_msg="",
    )
    assert mod.decide(inp) == ("fail", "trunk_unstable_no_override")


def test_parse_labels_from_json_array() -> None:
    mod = _load_module()
    assert mod._parse_labels('["foo","bar","baz"]') == ("foo", "bar", "baz")


def test_parse_labels_from_csv() -> None:
    mod = _load_module()
    assert mod._parse_labels("foo, bar baz") == ("foo", "bar", "baz")


def test_parse_labels_empty_returns_empty_tuple() -> None:
    mod = _load_module()
    assert mod._parse_labels("") == ()
    assert mod._parse_labels("[]") == ()


def test_script_subprocess_emits_decision_reason(tmp_path: Path) -> None:
    """End-to-end via subprocess: env in, stdout out."""
    out_file = tmp_path / "github_output"
    env = {
        "PATH": os.environ["PATH"],
        "TRUNK_UNSTABLE": "true",
        "PR_LABELS": json.dumps(["force-merge"]),
        "PR_BODY": "",
        "PR_HEAD_COMMIT_MSG": "",
        "GITHUB_OUTPUT": str(out_file),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "decision=pass" in result.stdout
    assert "reason=label_force_merge" in result.stdout
    # Also written to GITHUB_OUTPUT.
    assert "decision=pass" in out_file.read_text(encoding="utf-8")


def test_script_subprocess_fail_when_no_override() -> None:
    env = {
        "PATH": os.environ["PATH"],
        "TRUNK_UNSTABLE": "true",
        "PR_LABELS": "[]",
        "PR_BODY": "",
        "PR_HEAD_COMMIT_MSG": "",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    # Note: exit is always 0 per the script contract (caller decides
    # how to act). The verdict goes via stdout.
    assert result.returncode == 0
    assert "decision=fail" in result.stdout
    assert "reason=trunk_unstable_no_override" in result.stdout
