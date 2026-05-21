"""Shared pytest fixtures for the Sonar findings sweeper tests."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make the script importable as a module without touching pyproject.toml.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def issues_search_fixture_path() -> Path:
    """Path to the canonical issues_search.json fixture."""
    return _FIXTURE_DIR / "issues_search.json"


@pytest.fixture
def issues_search_widen_fixture_path() -> Path:
    """Path to the widen fixture: 5 BLOCKER + 25 MAJOR + 5 MINOR."""
    return _FIXTURE_DIR / "issues_search_widen.json"


@pytest.fixture
def issues_search_payload(issues_search_fixture_path: Path) -> dict:
    """Parsed payload of the canonical issues_search.json fixture."""
    return json.loads(issues_search_fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def sweep_workspace(tmp_path: Path) -> Iterator[Path]:
    """A temp directory with empty open/claimed/closed/done/deferred subdirs."""
    for sub in ("open", "claimed", "closed", "done", "deferred"):
        (tmp_path / sub).mkdir()
    yield tmp_path
