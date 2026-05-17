"""Unit tests for ``scripts/auto_heal_apply_typos.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SPEC = importlib.util.spec_from_file_location("auto_heal_apply_typos", _SCRIPTS / "auto_heal_apply_typos.py")
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

apply = _MOD.apply
existing_keys = _MOD.existing_keys
render_additions = _MOD.render_additions
main = _MOD.main


def test_existing_keys_parses_basic_assignments() -> None:
    text = """
[default.extend-words]
foo = "foo"
bar = "bar"
noteable = "noteable"
"""
    assert existing_keys(text) == {"foo", "bar", "noteable"}


def test_existing_keys_ignores_hyphenated_section_keys() -> None:
    # The matcher only cares about identifier-shaped keys (no hyphen).
    # Hyphenated TOML keys like ``extend-ignore-re`` are skipped, which
    # is the correct behaviour: we never auto-allowlist a token whose
    # name contains a hyphen.
    text = """
[default]
extend-ignore-re = [
    "Truncat",
]
[default.extend-words]
foo = "foo"
"""
    keys = existing_keys(text)
    assert "foo" in keys
    assert "extend-ignore-re" not in keys


def test_render_additions_skips_existing_tokens() -> None:
    additions = render_additions(["foo", "bar", "baz"], existing={"bar"})
    assert len(additions) == 2
    assert any("foo " in a for a in additions)
    assert any("baz " in a for a in additions)


def test_render_additions_drops_empty_token() -> None:
    additions = render_additions(["", "foo"], existing=set())
    assert len(additions) == 1
    assert "foo" in additions[0]


def test_apply_inserts_after_marker(tmp_path: Path) -> None:
    path = tmp_path / "typos.toml"
    path.write_text('[default.extend-words]\nfoo = "foo"\n')
    changed = apply(path, ["bar"])
    assert changed is True
    text = path.read_text()
    assert 'bar = "bar"' in text
    # Marker still present
    assert "[default.extend-words]" in text


def test_apply_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "typos.toml"
    path.write_text('[default.extend-words]\nfoo = "foo"\n')
    first = apply(path, ["bar"])
    second = apply(path, ["bar"])
    assert first is True
    assert second is False
    # Only one bar line.
    assert path.read_text().count('bar = "bar"') == 1


def test_apply_creates_marker_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "typos.toml"
    path.write_text("# comment\n")
    changed = apply(path, ["foo"])
    assert changed is True
    text = path.read_text()
    assert "[default.extend-words]" in text
    assert 'foo = "foo"' in text


def test_apply_handles_empty_tokens(tmp_path: Path) -> None:
    path = tmp_path / "typos.toml"
    path.write_text("[default.extend-words]\n")
    changed = apply(path, [])
    assert changed is False


def test_apply_preserves_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "typos.toml"
    original = '[default]\nlocale = "en"\n\n[default.extend-words]\nfoo = "foo"\n'
    path.write_text(original)
    apply(path, ["bar"])
    text = path.read_text()
    assert 'locale = "en"' in text
    assert 'foo = "foo"' in text
    assert 'bar = "bar"' in text


def test_main_returns_zero_on_success(tmp_path: Path) -> None:
    cfg = tmp_path / "typos.toml"
    cfg.write_text("[default.extend-words]\n")
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("noteable\nnoteable_id\n")
    rc = main(["--config", str(cfg), "--tokens", str(tokens)])
    assert rc == 0
    text = cfg.read_text()
    assert 'noteable = "noteable"' in text
    assert 'noteable_id = "noteable_id"' in text


def test_main_reports_missing_config(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("foo\n")
    rc = main(["--config", str(tmp_path / "missing.toml"), "--tokens", str(tokens)])
    assert rc == 1


def test_main_reports_missing_tokens(tmp_path: Path) -> None:
    cfg = tmp_path / "typos.toml"
    cfg.write_text("[default.extend-words]\n")
    rc = main(["--config", str(cfg), "--tokens", str(tmp_path / "missing.txt")])
    assert rc == 1


def test_main_no_op_on_empty_tokens_file(tmp_path: Path) -> None:
    cfg = tmp_path / "typos.toml"
    cfg.write_text('[default.extend-words]\nfoo = "foo"\n')
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("")
    original = cfg.read_text()
    rc = main(["--config", str(cfg), "--tokens", str(tokens)])
    assert rc == 0
    assert cfg.read_text() == original
