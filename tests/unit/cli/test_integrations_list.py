"""Tests for ``bernstein integrations list``.

The command surfaces the adapter registry to end users so they do not
need to grep ``src/bernstein/adapters/`` to find out which CLI tools
are wired in. These tests cover the default summary view, ``--details``,
``--json``, and ``--installed`` filtering.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bernstein.adapters.use_cases import USE_CASES, AdapterUseCase
from bernstein.cli.commands.integrations_cmd import (
    CONFIG_KNOB,
    DOCS_INDEX,
    _enumerate_rows,
    _fallback_headline,
    _filter_installed,
)
from bernstein.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_integrations_group_registered_on_main_cli(runner: CliRunner) -> None:
    """The ``integrations`` group is reachable from the top-level CLI."""
    result = runner.invoke(cli, ["integrations", "--help"])
    assert result.exit_code == 0, result.output
    assert "list" in result.output


def test_integrations_list_outputs_summary_table(runner: CliRunner) -> None:
    """Default view prints a table with the registry adapters."""
    result = runner.invoke(cli, ["integrations", "list"])
    assert result.exit_code == 0, result.output
    assert "Bernstein integrations" in result.output
    # Spot-check a handful of well-known adapter names.
    for expected in ("claude", "codex", "gemini", "aider", "cursor", "generic"):
        assert expected in result.output, f"adapter {expected!r} missing from output\n{result.output}"


def test_integrations_list_details_shows_metadata_blocks(runner: CliRunner) -> None:
    """``--details`` prints the per-adapter block with config knob + docs."""
    result = runner.invoke(cli, ["integrations", "list", "--details"])
    assert result.exit_code == 0, result.output
    assert "headline" in result.output
    assert "config knob" in result.output
    assert f"{CONFIG_KNOB}: claude" in result.output
    assert DOCS_INDEX in result.output


def test_integrations_list_json_is_valid_and_has_min_40_entries(runner: CliRunner) -> None:
    """``--json`` emits a parseable object with >=40 adapters."""
    result = runner.invoke(cli, ["integrations", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    assert payload["count"] == len(payload["adapters"])
    assert payload["count"] >= 40, f"expected >=40 adapters, got {payload['count']}"
    expected_keys = {
        "name",
        "headline",
        "binary",
        "installed",
        "config_knob",
        "docs",
        "details",
    }
    for row in payload["adapters"]:
        assert expected_keys.issubset(row.keys()), row
        assert isinstance(row["installed"], bool)

    names = {row["name"] for row in payload["adapters"]}
    assert {"claude", "codex", "gemini", "aider", "generic"}.issubset(names)


def test_integrations_list_installed_filter_drops_missing(
    runner: CliRunner,
) -> None:
    """``--installed`` filters to adapters whose binary is on $PATH.

    We stub ``shutil.which`` so the test is deterministic across hosts.
    """

    def _fake_which(binary: str) -> str | None:
        return "/usr/local/bin/claude" if binary == "claude" else None

    with patch(
        "bernstein.cli.commands.integrations_cmd.shutil.which",
        side_effect=_fake_which,
    ):
        result = runner.invoke(cli, ["integrations", "list", "--installed", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = {row["name"] for row in payload["adapters"]}
    assert names == {"claude"}, names
    assert payload["count"] == 1


def test_integrations_list_unknown_subcommand_returns_nonzero(
    runner: CliRunner,
) -> None:
    """Click rejects unknown subcommands under the ``integrations`` group."""
    result = runner.invoke(cli, ["integrations", "definitely-not-a-cmd"])
    assert result.exit_code != 0
    assert "No such command" in result.output or "Error" in result.output


def test_enumerate_rows_includes_generic_adapter() -> None:
    """The synthetic ``generic`` adapter must appear in the listing."""
    rows = _enumerate_rows()
    names = {row["name"] for row in rows}
    assert "generic" in names


def test_filter_installed_predicate() -> None:
    """``_filter_installed`` keeps only rows with ``installed=True``."""
    rows: list[dict[str, Any]] = [
        {"name": "a", "installed": True, "binary": "a"},
        {"name": "b", "installed": False, "binary": "b"},
        {"name": "c", "installed": True, "binary": "c"},
    ]
    kept = {row["name"] for row in _filter_installed(rows)}
    assert kept == {"a", "c"}


def test_use_case_entries_have_clean_text() -> None:
    """Curated copy must not contain em-dashes (style rule)."""
    for name, entry in USE_CASES.items():
        assert isinstance(entry, AdapterUseCase), name
        assert "\u2014" not in entry.headline, (name, entry.headline)
        assert "\u2014" not in entry.details, (name, entry.details)
        # Headlines stay compact so they fit a terminal column.
        assert len(entry.headline) <= 120, (name, entry.headline)


def test_fallback_headline_strips_adapter_suffix() -> None:
    """The docstring fallback removes ``CLI adapter`` boilerplate.

    The fallback reads the *module* docstring of the adapter's defining
    module. We synthesise a stub module with a known docstring and feed
    a class declared inside it.
    """
    import types

    fake_mod = types.ModuleType("fake_adapter_module")
    fake_mod.__doc__ = "Sample CLI adapter."

    class _Stub: ...

    _Stub.__module__ = fake_mod.__name__
    import sys as _sys

    _sys.modules[fake_mod.__name__] = fake_mod
    try:
        headline = _fallback_headline(_Stub)
    finally:
        _sys.modules.pop(fake_mod.__name__, None)
    assert headline == "Sample"
