"""{{ cookiecutter.gate_name }} quality gate for Bernstein."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bernstein.core.gate_runner import GateCheckResult, GateReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class {{ cookiecutter.gate_class }}Config:
    """Configuration for {{ cookiecutter.gate_name }} gate.

    Attributes:
        enabled: Whether the gate is enabled.
        threshold: Threshold for passing (e.g., max errors allowed).
        fail_on: Severity level that triggers failure.
    """
    enabled: bool = True
    threshold: int = 0
    fail_on: str = "error"  # error, warning, info


def run_{{ cookiecutter.gate_name }}(
    workdir: Path,
    task_id: str,
    config: {{ cookiecutter.gate_class }}Config | None = None,
) -> GateReport:
    """Run {{ cookiecutter.gate_name }} quality gate.

    {{ cookiecutter.description }}

    Args:
        workdir: Project working directory.
        task_id: ID of the task being validated.
        config: Gate configuration. Defaults to enabled with threshold 0.

    Returns:
        GateReport with pass/fail status and details.
    """
    if config is None:
        config = {{ cookiecutter.gate_class }}Config()

    if not config.enabled:
        return GateReport(
            gate="{{ cookiecutter.gate_name }}",
            passed=True,
            blocked=False,
            results=[
                GateCheckResult(
                    name="{{ cookiecutter.gate_name }}",
                    status="skipped",
                    detail="Gate disabled in configuration",
                )
            ],
        )

    logger.info("Running {{ cookiecutter.gate_name }} gate for task %s", task_id)

    # Template: Implement your quality gate logic here
    # Example structure:
    results: list[GateCheckResult] = []
    passed = True
    blocked = False

    try:
        # TODO: Implement your gate logic
        # Example:
        # 1. Run validation command/tool
        # 2. Parse output
        # 3. Determine pass/fail based on config.threshold

        # Placeholder implementation
        errors_found = 0
        warnings_found = 0

        if errors_found > config.threshold:
            passed = False
            blocked = config.fail_on == "error"
            results.append(
                GateCheckResult(
                    name="{{ cookiecutter.gate_name }}",
                    status="fail",
                    detail=f"Found {errors_found} errors (threshold: {config.threshold})",
                )
            )
        elif warnings_found > 0 and config.fail_on == "warning":
            passed = False
            blocked = True
            results.append(
                GateCheckResult(
                    name="{{ cookiecutter.gate_name }}",
                    status="fail",
                    detail=f"Found {warnings_found} warnings",
                )
            )
        else:
            results.append(
                GateCheckResult(
                    name="{{ cookiecutter.gate_name }}",
                    status="pass",
                    detail="No issues found",
                )
            )

    except Exception as exc:
        logger.error("{{ cookiecutter.gate_name }} gate failed: %s", exc)
        passed = False
        results.append(
            GateCheckResult(
                name="{{ cookiecutter.gate_name }}",
                status="error",
                detail=str(exc),
            )
        )

    return GateReport(
        gate="{{ cookiecutter.gate_name }}",
        passed=passed,
        blocked=blocked,
        results=results,
    )
