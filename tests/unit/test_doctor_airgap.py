"""Unit tests for ``bernstein doctor airgap`` and the underlying check battery.

The checks are pure functions that read the process environment + the
filesystem. We isolate them with monkeypatch + tmp_path so the asserts
are deterministic regardless of the developer's local config.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.advanced_cmd import doctor as doctor_group
from bernstein.cli.commands.doctor_airgap_cmd import run_doctor_airgap
from bernstein.core.distribution.doctor_airgap import (
    AirgapReport,
    Check,
    CheckStatus,
    check_audit_chain_hmac,
    check_mcp_catalog_all_off,
    check_memo_store_local,
    check_network_policy_deny_all,
    check_no_external_hostnames,
    check_policy_blocks_known_endpoints,
    check_profile_active,
    run_airgap_checks,
)
from bernstein.core.security.network_policy import (
    ENV_NETWORK_POLICY,
    ENV_PROFILE_MODE,
    PROFILE_AIRGAP,
)
from bernstein.core.security.socket_guard import (
    install_runtime_socket_guard,
    uninstall_runtime_socket_guard,
)


@pytest.fixture
def airgap_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set the airgap env vars and install the runtime socket guard.

    Production ``bernstein run --profile airgap`` does both: env vars +
    guard install. The fixture mirrors that so the doctor battery sees
    the same state in tests as it would in a real airgap run.
    """
    monkeypatch.setenv(ENV_PROFILE_MODE, PROFILE_AIRGAP)
    monkeypatch.setenv(ENV_NETWORK_POLICY, "none")
    install_runtime_socket_guard(force=True)
    try:
        yield
    finally:
        uninstall_runtime_socket_guard()


@pytest.fixture
def lax_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(ENV_PROFILE_MODE, raising=False)
    monkeypatch.delenv(ENV_NETWORK_POLICY, raising=False)
    uninstall_runtime_socket_guard()
    try:
        yield
    finally:
        uninstall_runtime_socket_guard()


def test_check_profile_active_pass(airgap_env: None) -> None:
    row = check_profile_active()
    assert row.status is CheckStatus.PASS


def test_check_profile_active_fail(lax_env: None) -> None:
    row = check_profile_active()
    assert row.status is CheckStatus.FAIL
    assert "rerun" in row.fix


def test_check_network_policy_pass(airgap_env: None) -> None:
    row = check_network_policy_deny_all()
    assert row.status is CheckStatus.PASS


def test_check_network_policy_fail_when_unset(lax_env: None) -> None:
    row = check_network_policy_deny_all()
    assert row.status is CheckStatus.FAIL


def test_check_network_policy_warn_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NETWORK_POLICY, "127.0.0.1")
    row = check_network_policy_deny_all()
    assert row.status is CheckStatus.WARN
    assert "127.0.0.1" in row.detail


def test_check_policy_blocks_known_endpoints_pass(airgap_env: None) -> None:
    row = check_policy_blocks_known_endpoints()
    assert row.status in (CheckStatus.PASS, CheckStatus.WARN)


def test_check_policy_blocks_known_endpoints_fail_when_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NETWORK_POLICY, "any")
    row = check_policy_blocks_known_endpoints()
    assert row.status is CheckStatus.FAIL
    assert "api." in row.detail


def test_check_mcp_catalog_all_off_when_no_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    row = check_mcp_catalog_all_off()
    assert row.status is CheckStatus.PASS


def test_check_mcp_catalog_all_off_with_installed_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = tmp_path / "bernstein" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "bernstein-managed": {
                    "mcpServers": {
                        "evil": {
                            "id": "evil",
                            "name": "evil",
                            "version_pin": "1.0",
                            "installed_at": "2026-01-01T00:00:00+00:00",
                        }
                    }
                }
            }
        )
    )
    row = check_mcp_catalog_all_off()
    assert row.status is CheckStatus.FAIL
    assert "evil" in row.detail


def test_check_memo_store_local_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    row = check_memo_store_local(workdir=tmp_path)
    assert row.status is CheckStatus.PASS


def test_check_memo_store_local_warn_when_cache_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = tmp_path / ".cache" / "bernstein"
    cache.mkdir(parents=True)
    (cache / "stale").write_text("x")
    row = check_memo_store_local(workdir=tmp_path)
    assert row.status is CheckStatus.WARN
    assert "rm -rf" in row.fix


def test_check_audit_chain_hmac_warn_when_no_audit_dir(tmp_path: Path) -> None:
    row = check_audit_chain_hmac(workdir=tmp_path)
    assert row.status is CheckStatus.WARN


def test_check_no_external_hostnames_pass_when_no_runtime(tmp_path: Path) -> None:
    row = check_no_external_hostnames(workdir=tmp_path)
    assert row.status is CheckStatus.WARN


def test_check_no_external_hostnames_fail_on_leak(tmp_path: Path) -> None:
    runtime = tmp_path / ".sdd" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "trace.json").write_text('{"endpoint":"https://api.openai.com/v1/foo"}')
    row = check_no_external_hostnames(workdir=tmp_path)
    assert row.status is CheckStatus.FAIL
    assert "api.openai.com" in row.detail


def test_check_no_external_hostnames_clean(tmp_path: Path) -> None:
    runtime = tmp_path / ".sdd" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "trace.json").write_text('{"endpoint":"http://127.0.0.1:11434/api"}')
    row = check_no_external_hostnames(workdir=tmp_path)
    assert row.status is CheckStatus.PASS


def test_run_airgap_checks_all_pass_in_clean_environment(
    airgap_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    report = run_airgap_checks(workdir=tmp_path)
    assert isinstance(report, AirgapReport)
    fails = [c for c in report.checks if c.status is CheckStatus.FAIL]
    assert fails == [], f"unexpected failures: {fails}"
    assert report.ok is True


def test_run_airgap_checks_fails_when_profile_unset(
    lax_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    report = run_airgap_checks(workdir=tmp_path)
    assert report.ok is False
    names = {c.name for c in report.checks if c.status is CheckStatus.FAIL}
    assert "airgap profile active" in names


def test_airgap_report_from_checks_aggregates() -> None:
    rows = [
        Check(name="a", status=CheckStatus.PASS, detail=""),
        Check(name="b", status=CheckStatus.WARN, detail=""),
    ]
    assert AirgapReport.from_checks(rows).ok is True
    rows.append(Check(name="c", status=CheckStatus.FAIL, detail=""))
    assert AirgapReport.from_checks(rows).ok is False


def test_run_doctor_airgap_returns_zero_on_pass(
    airgap_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    rc = run_doctor_airgap(workdir=tmp_path, as_json=True)
    assert rc == 0


def test_run_doctor_airgap_returns_one_on_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The renderer returns non-zero when a FAIL is present.

    The bughunt 2026-05-15 standalone fix makes the doctor self-activate
    airgap defaults when env vars are absent, so to trigger a FAIL we
    set ``BERNSTEIN_NETWORK_POLICY=any`` explicitly — that is a real
    "the operator overrode the airgap default with allow-all" condition
    the doctor must surface.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(ENV_NETWORK_POLICY, "any")
    rc = run_doctor_airgap(workdir=tmp_path, as_json=False)
    assert rc == 1


def test_doctor_airgap_cli_invokes_subcommand(
    airgap_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["airgap"])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output


def test_doctor_airgap_cli_passes_standalone_without_env(
    lax_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bughunt 2026-05-15: standalone pre-flight must succeed without pre-set env vars.

    Before the fix, invoking ``bernstein doctor airgap`` outside a live
    ``bernstein run --profile airgap`` reported FAIL on profile-active,
    deny-all and (originally) socket-guard rows even when the host was
    clean — the four spec-mandated green checks could not be produced.

    Post-fix, the renderer activates the airgap env vars for the
    duration of the battery (option A extended to all three checks),
    so a clean host reports PASSED and exits 0. The user-facing notice
    in the output tells the operator the activation was simulated.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["airgap"])
    assert result.exit_code == 0, result.output
    assert "PASSED" in result.output
    assert "simulated" in result.output.lower()


def test_doctor_airgap_cli_fails_when_operator_opts_into_allow_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the operator has already chosen ``--allow-network any`` we must NOT silently fix it.

    The doctor's simulated-airgap context manager only fills in env
    vars when they are absent. When the operator has explicitly opted
    out of airgap (``BERNSTEIN_NETWORK_POLICY=any``) the doctor must
    keep their choice and report the real FAIL state.
    """
    monkeypatch.delenv(ENV_PROFILE_MODE, raising=False)
    monkeypatch.setenv(ENV_NETWORK_POLICY, "any")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    uninstall_runtime_socket_guard()
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["airgap"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_doctor_airgap_cli_json_output(airgap_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["--json", "airgap"])
    assert result.exit_code == 0, result.output
    assert '"ok"' in result.output
    assert '"checks"' in result.output
