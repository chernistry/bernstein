"""Unit tests for scripts/gh_rate_limit_guard.sh.

Regression guard for the GitHub API rate-limit preflight used by
long-running agent loops.

Tests use a shim PATH that injects a fake `gh` binary returning known
JSON. This isolates the test from the real GitHub API and lets us
exercise both the OK and rate-limit-low branches.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gh_rate_limit_guard.sh"


def _write_fake_gh(tmp_path: Path, remaining: int) -> Path:
    """Write a fake `gh` shim that returns a fixed remaining count.

    The guard calls `gh api rate_limit --jq '.resources.core.remaining'`.
    Our shim ignores --jq and just prints the value directly; the guard
    pipes the JSON through gh's own --jq so we mimic that by just
    printing the number when --jq is present, or the full JSON otherwise.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gh"
    fake.write_text(
        f"""#!/usr/bin/env bash
# Fake gh for EDGE-7 rate-limit-guard tests.
# Args observed in the guard:
#   gh api rate_limit --jq '.resources.core.remaining'
#   gh api rate_limit --jq '.resources.core.reset'
case "$*" in
  *--jq*'.resources.core.remaining'*)
    echo {remaining}
    ;;
  *--jq*'.resources.core.reset'*)
    echo 1700000000
    ;;
  *)
    cat <<EOF
{{
  "resources": {{
    "core": {{ "remaining": {remaining}, "reset": 1700000000 }}
  }}
}}
EOF
    ;;
esac
exit 0
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(threshold: int, fake_bin_dir: Path):
    env = {
        "PATH": f"{fake_bin_dir}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "check", str(threshold)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_guard_passes_when_remaining_above_threshold(tmp_path: Path) -> None:
    bin_dir = _write_fake_gh(tmp_path, remaining=3000)
    result = _run(500, bin_dir)
    assert result.returncode == 0, (
        f"guard should pass when remaining > threshold. stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_guard_fails_when_remaining_below_threshold(tmp_path: Path) -> None:
    bin_dir = _write_fake_gh(tmp_path, remaining=100)
    result = _run(500, bin_dir)
    assert result.returncode == 1, result.stderr
    assert "rate_limit_low" in result.stderr
    assert "remaining=100" in result.stderr
    assert "threshold=500" in result.stderr


def test_guard_fails_when_remaining_equals_threshold_minus_one(tmp_path: Path) -> None:
    """Boundary: threshold of 500 means remaining < 500 fails."""
    bin_dir = _write_fake_gh(tmp_path, remaining=499)
    result = _run(500, bin_dir)
    assert result.returncode == 1


def test_guard_passes_when_remaining_equals_threshold(tmp_path: Path) -> None:
    """Boundary: remaining == threshold is OK (>=)."""
    bin_dir = _write_fake_gh(tmp_path, remaining=500)
    result = _run(500, bin_dir)
    assert result.returncode == 0


def test_guard_returns_2_when_gh_missing(tmp_path: Path) -> None:
    """If gh is not on PATH the guard must NOT silently say OK.

    The fake bin dir contains only `bash` (symlinked to the real one)
    so the guard's `command -v gh` call returns nothing.
    """
    bin_dir = tmp_path / "min_bin"
    bin_dir.mkdir()
    bash_path = shutil.which("bash") or "/bin/bash"
    os.symlink(bash_path, bin_dir / "bash")
    # No `gh` shim here.
    env = {"PATH": str(bin_dir)}
    result = subprocess.run(
        [str(bin_dir / "bash"), str(SCRIPT), "check", "500"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2, (
        f"missing gh must fail-loud with exit 2, not silently pass. stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_script_is_executable() -> None:
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111


def test_script_passes_shellcheck() -> None:
    """If shellcheck is installed, the guard must be lint-clean."""
    if not shutil.which("shellcheck"):
        return  # silent skip; CI has shellcheck and will enforce
    result = subprocess.run(
        ["shellcheck", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"shellcheck flagged the guard:\n{result.stdout}\n{result.stderr}"
