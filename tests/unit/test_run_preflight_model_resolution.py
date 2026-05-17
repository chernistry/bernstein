"""Regression tests for ``run_preflight._resolve_model_and_cli``.

The preflight cost preview reads the seed's ``role_model_policy`` to pick
which model price the estimate is computed against. Previously the loop
took the first dict entry and ``break``-ed, which under-reported cost
when a cheap role (e.g. qa on gemini) shadowed an expensive role (e.g.
backend on opus) in the same seed.

These tests pin the new behaviour: the most expensive role wins, so the
preflight estimate is an upper bound on actual spend.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.cli.run_preflight import _resolve_model_and_cli


def _write_seed(seed_file: Path, body: str) -> Path:
    seed_file.write_text(body, encoding="utf-8")
    return seed_file


@pytest.fixture
def seed_file(tmp_path: Path) -> Path:
    return tmp_path / "bernstein.yaml"


def test_picks_most_expensive_role_not_first(seed_file: Path) -> None:
    """Insertion order must not determine which role's cost is reported."""
    # ``qa`` is the *first* role (would have won under the old buggy
    # loop) but uses cheap gemini; ``backend`` uses opus and must win.
    _write_seed(
        seed_file,
        'goal: "ship"\n'
        "role_model_policy:\n"
        "  qa:\n"
        "    cli: gemini\n"
        "    model: gemini-3-flash\n"
        "  backend:\n"
        "    cli: claude\n"
        "    model: opus\n",
    )
    model, cli, role = _resolve_model_and_cli(str(seed_file), None)
    assert model == "opus"
    assert cli == "claude"
    assert role == "backend"


def test_first_role_wins_when_it_is_the_most_expensive(seed_file: Path) -> None:
    """The first role still wins when nothing else is more expensive."""
    _write_seed(
        seed_file,
        'goal: "ship"\n'
        "role_model_policy:\n"
        "  backend:\n"
        "    cli: claude\n"
        "    model: opus\n"
        "  qa:\n"
        "    cli: gemini\n"
        "    model: gemini-3-flash\n",
    )
    model, cli, role = _resolve_model_and_cli(str(seed_file), None)
    assert model == "opus"
    assert cli == "claude"
    assert role == "backend"


def test_model_override_short_circuits(seed_file: Path) -> None:
    """An explicit ``--model`` override bypasses seed inspection entirely."""
    _write_seed(
        seed_file,
        'goal: "ship"\nrole_model_policy:\n  backend: {cli: claude, model: opus}\n',
    )
    model, cli, _role = _resolve_model_and_cli(str(seed_file), "haiku")
    assert model == "haiku"
    assert cli == "claude"


def test_missing_seed_returns_defaults(tmp_path: Path) -> None:
    model, cli, role = _resolve_model_and_cli(str(tmp_path / "nope.yaml"), None)
    assert model == "sonnet"
    assert cli == "claude"
    assert role == "backend"
