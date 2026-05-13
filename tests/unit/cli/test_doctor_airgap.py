"""Regression tests for ``bernstein doctor airgap`` standalone invocation.

Bughunt 2026-05-13: the doctor's ``runtime socket guard active`` row
used to FAIL whenever the command was invoked outside an active
``bernstein run --profile airgap`` process, because the guard is
installed by the run bootstrap, not by the doctor itself. Operators
following the documented pre-flight workflow saw a red row that did
not reflect real misbehaviour.

These tests pin the fixed contract: with the airgap profile set in
the environment and no live run, the doctor reports 4 green PASS
rows for the spec-mandated checks (zero egress / MCP catalog all-off /
memo store local-only / audit chain valid), plus the now-also-green
socket-guard row.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.advanced_cmd import doctor as doctor_group
from bernstein.cli.commands.doctor_airgap_cmd import run_doctor_airgap
from bernstein.core.distribution.doctor_airgap import (
    CheckStatus,
    check_runtime_socket_guard_active,
    run_airgap_checks,
)
from bernstein.core.security.network_policy import (
    ENV_NETWORK_POLICY,
    ENV_PROFILE_MODE,
    PROFILE_AIRGAP,
)
from bernstein.core.security.socket_guard import (
    is_runtime_socket_guard_installed,
    uninstall_runtime_socket_guard,
)


@pytest.fixture
def standalone_airgap_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Mimic an operator who set the airgap env vars but has NOT run ``bernstein run``.

    Matches the documented pre-flight workflow: the operator exports
    ``BERNSTEIN_PROFILE_MODE=airgap`` plus ``BERNSTEIN_NETWORK_POLICY=none``
    in their shell, then invokes ``bernstein doctor airgap`` to verify
    the host is ready *before* spinning up agents. Critically, the
    socket guard is NOT pre-installed in the doctor's process.
    """
    # Ensure no prior test left the guard patched in.
    uninstall_runtime_socket_guard()
    monkeypatch.setenv(ENV_PROFILE_MODE, PROFILE_AIRGAP)
    monkeypatch.setenv(ENV_NETWORK_POLICY, "none")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert not is_runtime_socket_guard_installed(), (
        "fixture precondition: socket guard must NOT be installed before the doctor runs"
    )
    try:
        yield tmp_path
    finally:
        # Doctor's option-A path uninstalls on its own; defensively
        # clean up in case a future regression leaves it installed.
        uninstall_runtime_socket_guard()


def test_socket_guard_check_passes_standalone(standalone_airgap_env: Path) -> None:
    """The bughunt regression: the row must be PASS, not FAIL."""
    row = check_runtime_socket_guard_active()
    assert row.status is CheckStatus.PASS, f"expected PASS, got {row.status.value}: {row.detail}"


def test_socket_guard_check_restores_original_connect(standalone_airgap_env: Path) -> None:
    """Option (A) requires the guard to be UNinstalled after the check returns.

    If the doctor leaks the patch into the operator's process, subsequent
    network calls in the same Python session would silently hit the
    air-gap guard -- a real side effect that would justify falling back
    to option (B). Pin the no-leak contract.
    """
    check_runtime_socket_guard_active()
    assert not is_runtime_socket_guard_installed(), "doctor must restore socket.socket.connect after its assertion"


def test_doctor_airgap_standalone_returns_four_green_checks(standalone_airgap_env: Path) -> None:
    """The headline expected behaviour from MASTER-RU Q21 + airgap profile docs.

    The spec calls out four green checks for a standalone pre-flight:
      1. zero egress       -> network policy deny-all
      2. MCP catalog all-off
      3. memo store local-only
      4. audit chain valid (WARN-acceptable when no audit dir yet)

    Plus the socket-guard row, which post-fix should also be PASS.
    """
    report = run_airgap_checks(workdir=standalone_airgap_env)

    fails = [c for c in report.checks if c.status is CheckStatus.FAIL]
    assert fails == [], f"unexpected FAIL rows: {[(c.name, c.detail) for c in fails]}"
    assert report.ok is True

    by_name = {c.name: c for c in report.checks}
    # The four spec-mandated green rows:
    assert by_name["network policy deny-all"].status is CheckStatus.PASS
    assert by_name["MCP catalog all-off"].status is CheckStatus.PASS
    assert by_name["memo store on local disk"].status is CheckStatus.PASS
    # Audit chain check WARNs when there is no audit dir yet -- that
    # is the spec-correct standalone state (nothing to verify), but
    # it still must not be a FAIL.
    audit = by_name["audit chain HMAC valid"]
    assert audit.status in (CheckStatus.PASS, CheckStatus.WARN), audit.detail
    # The bughunt row itself:
    assert by_name["runtime socket guard active"].status is CheckStatus.PASS


def test_doctor_airgap_cli_exits_zero_standalone(standalone_airgap_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the Click subcommand must return rc=0 in the standalone scenario."""
    monkeypatch.chdir(standalone_airgap_env)
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["airgap"])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    assert "FAILED" not in result.output


def test_run_doctor_airgap_function_returns_zero_standalone(standalone_airgap_env: Path) -> None:
    """Direct call into the renderer -- belt and braces for the CLI test above."""
    rc = run_doctor_airgap(workdir=standalone_airgap_env, as_json=True)
    assert rc == 0
