"""Unit tests for CI / sandbox environment detection."""

from __future__ import annotations

from bernstein.cli.doctor.environment_checks import (
    ENVIRONMENT_PROBES,
    EnvironmentProbe,
    detect_environments,
    run_environment_checks,
)


def _no_files(_: str) -> bool:
    return False


def test_no_env_returns_local() -> None:
    results = run_environment_checks(env={}, file_exists=_no_files)
    assert len(results) == 1
    assert results[0].name == "env:host"
    assert "local workstation" in results[0].detail


def test_detects_github_actions() -> None:
    matches = detect_environments(env={"GITHUB_ACTIONS": "true"}, file_exists=_no_files)
    labels = [m.label for m in matches]
    assert "GitHub Actions" in labels


def test_detects_gitlab_ci() -> None:
    results = run_environment_checks(env={"GITLAB_CI": "true"}, file_exists=_no_files)
    assert any("GitLab CI" in r.detail for r in results)


def test_detects_buildkite() -> None:
    results = run_environment_checks(env={"BUILDKITE": "true"}, file_exists=_no_files)
    assert any("Buildkite" in r.detail for r in results)


def test_detects_circleci() -> None:
    matches = detect_environments(env={"CIRCLECI": "true"}, file_exists=_no_files)
    assert any(m.label == "CircleCI" for m in matches)


def test_detects_jenkins_via_jenkins_url() -> None:
    matches = detect_environments(env={"JENKINS_URL": "http://jenkins/"}, file_exists=_no_files)
    assert any(m.label == "Jenkins" for m in matches)


def test_detects_docker_via_dockerenv_file() -> None:
    matches = detect_environments(env={}, file_exists=lambda p: p == "/.dockerenv")
    assert any(m.label == "Docker" for m in matches)


def test_detects_devcontainer_env() -> None:
    matches = detect_environments(env={"DEVCONTAINER": "true"}, file_exists=_no_files)
    assert any(m.label.startswith("VS Code devcontainer") for m in matches)


def test_detects_remote_containers_env() -> None:
    matches = detect_environments(env={"REMOTE_CONTAINERS": "true"}, file_exists=_no_files)
    assert any(m.label.startswith("VS Code devcontainer") for m in matches)


def test_detects_systemd_run() -> None:
    matches = detect_environments(env={"INVOCATION_ID": "abc"}, file_exists=_no_files)
    assert any(m.label == "systemd-run" for m in matches)


def test_specific_environment_hides_generic_ci() -> None:
    # GitHub Actions sets both GITHUB_ACTIONS=true and CI=true. We must
    # collapse the generic CI row when something more specific matched.
    matches = detect_environments(
        env={"GITHUB_ACTIONS": "true", "CI": "true"},
        file_exists=_no_files,
    )
    labels = [m.label for m in matches]
    assert "GitHub Actions" in labels
    assert "Generic CI" not in labels


def test_only_generic_ci_when_no_specific_match() -> None:
    matches = detect_environments(env={"CI": "true"}, file_exists=_no_files)
    labels = [m.label for m in matches]
    assert labels == ["Generic CI"]


def test_env_value_mismatch_skipped() -> None:
    # GITHUB_ACTIONS must equal literal "true"; "1" should not match.
    matches = detect_environments(env={"GITHUB_ACTIONS": "1"}, file_exists=_no_files)
    assert not any(m.label == "GitHub Actions" for m in matches)


def test_probe_dataclass_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    import pytest

    probe = EnvironmentProbe(label="x", detail_hint="x", env_var="X")
    with pytest.raises(FrozenInstanceError):
        probe.label = "y"  # type: ignore[misc]


def test_environment_probes_have_unique_labels() -> None:
    labels = [p.label for p in ENVIRONMENT_PROBES]
    assert len(labels) == len(set(labels))


def test_run_environment_checks_marks_remediation_when_in_ci() -> None:
    results = run_environment_checks(env={"GITHUB_ACTIONS": "true"}, file_exists=_no_files)
    assert all(r.category == "environment" for r in results)
    assert any("bug report" in r.remediation for r in results)


def test_docker_inside_github_actions_reports_both() -> None:
    matches = detect_environments(
        env={"GITHUB_ACTIONS": "true"},
        file_exists=lambda p: p == "/.dockerenv",
    )
    labels = [m.label for m in matches]
    assert "GitHub Actions" in labels
    assert "Docker" in labels


def test_unicode_env_value_does_not_crash() -> None:
    # Pathological input must not raise.
    matches = detect_environments(env={"GITHUB_ACTIONS": "é"}, file_exists=_no_files)
    assert all(isinstance(m, EnvironmentProbe) for m in matches)


def test_run_uses_real_os_environ_when_not_provided() -> None:
    # Smoke test: must not raise even if os.environ has unrelated values.
    results = run_environment_checks()
    assert results, "run_environment_checks must always return at least one row"
