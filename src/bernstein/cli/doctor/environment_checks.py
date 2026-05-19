"""CI / sandbox environment detection.

Detects whether bernstein is running inside a common build environment
so users opening issues can include the context automatically. The
detection logic combines environment variables with light-weight file
probes (for example ``/.dockerenv``).

Detection order is significant: more specific environments are reported
first so a Docker-in-GitHub-Actions run is labelled "GitHub Actions"
rather than "Docker".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.cli.doctor.report import DoctorResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


@dataclass(frozen=True)
class EnvironmentProbe:
    """A single environment-detection probe."""

    label: str
    detail_hint: str
    env_var: str | None = None
    env_value: str | None = None  # If set, env_var must equal this string.
    file_path: str | None = None  # If set, file_path must exist.

    def matches(self, env: Mapping[str, str], file_exists: Callable[[str], bool]) -> bool:
        if self.env_var is not None:
            value = env.get(self.env_var, "")
            if self.env_value is None:
                if not value:
                    return False
            elif value != self.env_value:
                return False
        return not (self.file_path is not None and not file_exists(self.file_path))


# Ordered list of probes. The first match wins, but every match is
# reported so the operator can see "GitHub Actions inside Docker" if
# both signals are present.
ENVIRONMENT_PROBES: list[EnvironmentProbe] = [
    EnvironmentProbe(
        label="GitHub Actions",
        detail_hint="GITHUB_ACTIONS=true",
        env_var="GITHUB_ACTIONS",
        env_value="true",
    ),
    EnvironmentProbe(
        label="GitLab CI",
        detail_hint="GITLAB_CI=true",
        env_var="GITLAB_CI",
        env_value="true",
    ),
    EnvironmentProbe(
        label="Buildkite",
        detail_hint="BUILDKITE=true",
        env_var="BUILDKITE",
        env_value="true",
    ),
    EnvironmentProbe(
        label="CircleCI",
        detail_hint="CIRCLECI=true",
        env_var="CIRCLECI",
        env_value="true",
    ),
    EnvironmentProbe(
        label="Jenkins",
        detail_hint="JENKINS_URL set",
        env_var="JENKINS_URL",
    ),
    EnvironmentProbe(
        label="Docker",
        detail_hint="/.dockerenv present",
        file_path="/.dockerenv",
    ),
    EnvironmentProbe(
        label="VS Code devcontainer",
        detail_hint="DEVCONTAINER=true",
        env_var="DEVCONTAINER",
        env_value="true",
    ),
    EnvironmentProbe(
        label="VS Code devcontainer (remote)",
        detail_hint="REMOTE_CONTAINERS=true",
        env_var="REMOTE_CONTAINERS",
        env_value="true",
    ),
    EnvironmentProbe(
        label="systemd-run",
        detail_hint="INVOCATION_ID set",
        env_var="INVOCATION_ID",
    ),
    EnvironmentProbe(
        label="Generic CI",
        detail_hint="CI=true",
        env_var="CI",
        env_value="true",
    ),
]


def detect_environments(
    env: Mapping[str, str] | None = None,
    *,
    file_exists: Callable[[str], bool] | None = None,
    probes: Sequence[EnvironmentProbe] | None = None,
) -> list[EnvironmentProbe]:
    """Return every probe that currently matches.

    The ``Generic CI`` probe is suppressed when a more specific CI
    environment has already matched (it exists as a fallback signal).
    """
    env_map = env if env is not None else os.environ.copy()
    exists = file_exists if file_exists is not None else _default_file_exists
    probe_list = list(probes) if probes is not None else ENVIRONMENT_PROBES

    matches = [probe for probe in probe_list if probe.matches(env_map, exists)]

    if len(matches) > 1:
        specific = [p for p in matches if p.label != "Generic CI"]
        if specific:
            return specific
    return matches


def run_environment_checks(
    env: Mapping[str, str] | None = None,
    *,
    file_exists: Callable[[str], bool] | None = None,
) -> list[DoctorResult]:
    """Return DoctorResult rows describing the detected environment.

    Always returns at least one row. When no probe matches the row reads
    ``status="ok"`` so the absence of any CI/sandbox marker is itself
    visible in the table.
    """
    matches = detect_environments(env=env, file_exists=file_exists)
    if not matches:
        return [
            DoctorResult(
                name="env:host",
                category="environment",
                status="ok",
                detail="local workstation (no CI/sandbox markers detected)",
            )
        ]

    return [
        DoctorResult(
            name=f"env:{_slug(probe.label)}",
            category="environment",
            status="ok",
            detail=f"{probe.label} detected ({probe.detail_hint})",
            remediation="Include this in any bug report",
        )
        for probe in matches
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_file_exists(p: str) -> bool:
    """Wrap :func:`Path.is_file` and swallow OS errors quietly."""
    try:
        return Path(p).exists()
    except OSError:  # pragma: no cover - hardly reachable
        return False


def _slug(label: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in label).strip("-").replace("--", "-")
