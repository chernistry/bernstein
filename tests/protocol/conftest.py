"""Pytest fixtures for protocol compatibility tests."""

import json
from pathlib import Path


def pytest_collection_modifyitems(session, config, items):
    """Mark protocol tests to run in isolation per workflow spec."""
    protocol_test_dir = Path(__file__).parent
    for item in items:
        if protocol_test_dir in Path(item.fspath).parents:
            item.add_marker("protocol")


def load_protocol_versions() -> dict:
    """Load protocol version matrix from versions.json."""
    versions_file = Path(__file__).parent / "versions.json"
    if not versions_file.exists():
        raise FileNotFoundError(f"Protocol versions file not found: {versions_file}")

    with open(versions_file) as f:
        return json.load(f)
