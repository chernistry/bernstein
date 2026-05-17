"""Shared fixtures for telemetry unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bernstein.core.telemetry import client as client_mod


@pytest.fixture()
def tmp_home(tmp_path: Path) -> Iterator[Path]:
    """A temporary operator home directory."""
    (tmp_path / ".bernstein").mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture(autouse=True)
def _reset_default_client() -> Iterator[None]:
    """Ensure each test starts with a fresh default client."""
    client_mod.reset_default_client()
    yield
    client_mod.reset_default_client()
