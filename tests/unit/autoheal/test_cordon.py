"""Unit tests for ``bernstein.core.autoheal.cordon``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.autoheal.cordon import (
    CORDON_EXACT,
    CORDON_GLOBS,
    ENV_CORDON_EXTRA,
    WHITESPACE_OK_GLOBS,
    evaluate,
    evaluate_many,
)


@pytest.mark.parametrize("path", sorted(CORDON_EXACT))
def test_exact_paths_allowed(path: str) -> None:
    d = evaluate(path)
    assert d.allowed is True
    assert d.rule == "cordon_exact"


def test_cursor_rules_glob_allowed() -> None:
    d = evaluate(".cursor/rules/python.mdc")
    assert d.allowed is True
    assert "cordon_glob" in d.rule


def test_src_path_with_whitespace_only_allowed() -> None:
    d = evaluate("src/bernstein/foo.py", whitespace_only=True)
    assert d.allowed is True
    assert d.rule.startswith("whitespace_only:")


def test_src_path_without_whitespace_only_rejected() -> None:
    d = evaluate("src/bernstein/foo.py", whitespace_only=False)
    assert d.allowed is False
    assert d.rule.startswith("non_whitespace_in_protected:")


def test_random_path_rejected() -> None:
    d = evaluate("docs/random.md")
    assert d.allowed is False
    assert d.rule == "not_in_cordon"


def test_tests_path_requires_whitespace_only() -> None:
    d = evaluate("tests/unit/test_foo.py", whitespace_only=False)
    assert d.allowed is False
    d2 = evaluate("tests/unit/test_foo.py", whitespace_only=True)
    assert d2.allowed is True


def test_scripts_path_requires_whitespace_only() -> None:
    d = evaluate("scripts/foo.py", whitespace_only=False)
    assert d.allowed is False


def test_evaluate_many_mixed_paths() -> None:
    results = evaluate_many(
        ["typos.toml", "src/bernstein/foo.py", "docs/random.md"],
        whitespace_only_paths={"src/bernstein/foo.py"},
    )
    assert results[0].allowed is True
    assert results[1].allowed is True
    assert results[2].allowed is False


def test_globs_are_stable() -> None:
    # Sanity: the configured globs cover the expected categories.
    assert any("cursor" in g for g in CORDON_GLOBS)
    assert any("src/bernstein" in g for g in WHITESPACE_OK_GLOBS)


def test_glob_translation_uses_single_separator_path() -> None:
    source = Path("src/bernstein/core/autoheal/cordon.py").read_text(encoding="utf-8")

    assert 'if tokens[i + 1] == "**"' not in source


def test_path_normalization_via_purelib() -> None:
    # Forward slashes; PurePosixPath normalises duplicates.
    d = evaluate("./typos.toml")
    assert d.allowed is True


def test_env_extra_paths_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can extend the exact allowlist via env without forking."""
    monkeypatch.setenv(ENV_CORDON_EXTRA, "extra/typos.lst:CODEOWNERS")
    d1 = evaluate("extra/typos.lst")
    assert d1.allowed is True
    assert d1.rule == "cordon_exact_env"
    d2 = evaluate("CODEOWNERS")
    assert d2.allowed is True
    # A path NOT listed must still reject.
    d3 = evaluate("random.toml")
    assert d3.allowed is False


def test_env_extra_paths_empty_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CORDON_EXTRA, "")
    d = evaluate("extra/typos.lst")
    assert d.allowed is False
