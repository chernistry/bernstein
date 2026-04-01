"""Tests for {{ cookiecutter.gate_name }} quality gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from {{ cookiecutter.gate_name }} import (
    {{ cookiecutter.gate_class }}Config,
    run_{{ cookiecutter.gate_name }},
)


@pytest.fixture()
def config() -> {{ cookiecutter.gate_class }}Config:
    """Create default gate config for testing."""
    return {{ cookiecutter.gate_class }}Config(
        enabled=True,
        threshold=0,
        fail_on="error",
    )


class Test{{ cookiecutter.gate_class }}:
    """Test suite for {{ cookiecutter.gate_name }} quality gate."""

    def test_gate_disabled(self, tmp_path: Path) -> None:
        """Test gate when disabled."""
        config = {{ cookiecutter.gate_class }}Config(enabled=False)
        report = run_{{ cookiecutter.gate_name }}(
            workdir=tmp_path,
            task_id="test-123",
            config=config,
        )

        assert report.passed is True
        assert report.blocked is False
        assert len(report.results) == 1
        assert report.results[0].status == "skipped"

    def test_gate_pass(self, tmp_path: Path, config: {{ cookiecutter.gate_class }}Config) -> None:
        """Test gate passing scenario."""
        # TODO: Set up test fixtures for passing scenario
        report = run_{{ cookiecutter.gate_name }}(
            workdir=tmp_path,
            task_id="test-123",
            config=config,
        )

        # Template - adjust assertions based on your gate logic
        assert report.passed is True

    def test_gate_fail(self, tmp_path: Path, config: {{ cookiecutter.gate_class }}Config) -> None:
        """Test gate failing scenario."""
        # TODO: Set up test fixtures for failing scenario
        report = run_{{ cookiecutter.gate_name }}(
            workdir=tmp_path,
            task_id="test-123",
            config=config,
        )

        # Template - adjust assertions based on your gate logic
        # assert report.passed is False

    def test_gate_threshold(self, tmp_path: Path) -> None:
        """Test gate with custom threshold."""
        config = {{ cookiecutter.gate_class }}Config(
            enabled=True,
            threshold=5,
            fail_on="error",
        )

        report = run_{{ cookiecutter.gate_name }}(
            workdir=tmp_path,
            task_id="test-123",
            config=config,
        )

        # Template - test threshold behavior
        assert report is not None
