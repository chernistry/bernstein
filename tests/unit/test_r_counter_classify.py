"""Unit tests for scripts/r_counter_classify.py and the allow-list file.

Regression guard for the hotfix R-counter classifier.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "r_counter_classify.py"
ALLOWLIST = REPO_ROOT / ".github" / "r-counter-allowlist.txt"


def _load_module():
    """Load the script as a module so we can call classify() directly."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("r_counter_classify", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_allowlist_file_exists() -> None:
    assert ALLOWLIST.exists(), (
        ".github/r-counter-allowlist.txt is the contract between EDGE-4 hardening "
        "and the META hotfix-r-tracker.yml workflow. It must exist."
    )


def test_allowlist_patterns_compile() -> None:
    mod = _load_module()
    patterns = mod.load_patterns(ALLOWLIST)
    assert len(patterns) >= 4, (
        "allow-list must cover at least the four documented benign-drift classes: "
        "agents-md drift, post-hotfix lint drift, generic lint drift, recursive lint drift"
    )


def test_classify_benign_agents_md_drift() -> None:
    mod = _load_module()
    patterns = mod.load_patterns(ALLOWLIST)
    subjects = [
        "fix(ci): repair main 5e0be90f6 (post-hotfix lint drift)",
        "fix(ci): repair main 42bd1846f (lint drift after knowledge synthesis merge)",
    ]
    assert mod.classify(subjects, patterns) == "benign"


def test_classify_investigate_mixed_class() -> None:
    """Chain mixes a benign drift commit with a real bug-fix commit.
    R-counter SHOULD signal here -- the real fix indicates a genuine
    regression sequence."""
    mod = _load_module()
    patterns = mod.load_patterns(ALLOWLIST)
    subjects = [
        "fix(ci): repair main 5e0be90f6 (post-hotfix lint drift)",
        "fix(ci): patch null-pointer in adapter manager",
    ]
    assert mod.classify(subjects, patterns) == "investigate"


def test_classify_empty_input_defaults_to_investigate() -> None:
    """Empty chain means we cannot prove benignness; default to surfacing."""
    mod = _load_module()
    patterns = mod.load_patterns(ALLOWLIST)
    assert mod.classify([], patterns) == "investigate"


def test_classify_unmatched_subject_investigate() -> None:
    mod = _load_module()
    patterns = mod.load_patterns(ALLOWLIST)
    subjects = ["fix(ci): something completely different"]
    assert mod.classify(subjects, patterns) == "investigate"


def test_script_exits_zero_on_benign(tmp_path: Path) -> None:
    """End-to-end: pipe subjects to the script, check exit code + stdout."""
    chain = "\n".join(
        [
            "fix(ci): repair main abc1234 (post-hotfix lint drift)",
            "fix(ci): repair main def5678 (agents-md drift after feat foo)",
        ]
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=chain,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "benign"


def test_script_exits_zero_with_investigate_on_real_bug(tmp_path: Path) -> None:
    chain = "\n".join(
        [
            "fix(ci): repair main abc1234 (post-hotfix lint drift)",
            "fix(ci): patch race in pwa websocket",
        ]
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=chain,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "investigate"


def test_r_counter_workflow_calls_classifier() -> None:
    """The hotfix-r-tracker workflow must invoke r_counter_classify.py
    so the benign-drift allow-list actually suppresses false positives.

    Without this wire-up, the classifier ships but never runs.
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "hotfix-r-tracker.yml"
    assert workflow.exists(), (
        "hotfix-r-tracker.yml must exist (landed via PR #1455). If this fails, "
        "the META R-counter workflow has been moved or deleted."
    )
    text = workflow.read_text(encoding="utf-8")
    assert "r_counter_classify.py" in text, (
        "EDGE-4 wire-up missing: hotfix-r-tracker.yml does not invoke "
        "scripts/r_counter_classify.py. The benign-drift allow-list ships but "
        "never runs, so R-counter will still false-positive on every Python "
        "feature-merge agents-md+ruff drift sequence."
    )
    assert "VERDICT" in text and "benign" in text, (
        "EDGE-4 wire-up incomplete: workflow must check classifier verdict and short-circuit on 'benign'"
    )
