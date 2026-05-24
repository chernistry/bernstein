"""Unit tests for ``bernstein.core.identity.install_rev``.

Covers:

* token shape (16 lowercase base32 chars, no padding)
* deterministic output for fixed (seed, nonce, version) - the install-
  stability promise
* kill-switch behaviours (env var, module flag, missing seed)
* verifier shape-check + sentinel rejection
* full nonce-aware verifier with constant-time compare
* nonce persistence - same install → same token across calls
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from bernstein.core.identity import install_rev as ir
from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    ENV_DISABLE,
    ENV_NONCE_PATH,
    ENV_SEED,
    NONCE_BYTES,
    TOKEN_LEN,
    InvalidTokenError,
    SeedNotConfiguredError,
    _compute_token,
    get_install_rev,
    render_md_footer,
    render_trace_header,
    render_yaml_comment,
    verify_token,
    verify_with_nonce,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# Fixed 32-byte seed for deterministic tests.  Pseudo-random hex; not a
# real operator seed, never use this in production.
TEST_SEED_HEX = "01" * 32

# A reproducible nonce for cryptographic-vector tests.
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


def _exception_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for item in node.elts:
            names.update(_exception_names(item))
        return names
    return set()


_KNOWN_EXCEPTION_TYPES = {
    "ImportError": ImportError,
    "ModuleNotFoundError": ModuleNotFoundError,
    "PackageNotFoundError": PackageNotFoundError,
}


@pytest.fixture(autouse=True)
def _reset_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Isolate every test from on-disk nonce + global cache + env vars."""
    nonce_path = tmp_path / "install_nonce"
    monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
    monkeypatch.delenv(ENV_DISABLE, raising=False)
    monkeypatch.delenv(ENV_SEED, raising=False)
    # Disable emission by default; tests that exercise live emit flip it
    # back to True via monkeypatch on ``IDENTITY_EMISSION_ENABLED``.
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", False)
    ir._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Token shape
# ---------------------------------------------------------------------------


class TestTokenShape:
    """Token-shape invariants - must hold for every code path."""

    def test_disabled_sentinel_is_sixteen_zeros(self) -> None:
        assert DISABLED_SENTINEL == "0" * TOKEN_LEN
        assert len(DISABLED_SENTINEL) == 16

    def test_get_install_rev_returns_sentinel_when_emission_disabled(self) -> None:
        assert get_install_rev() == DISABLED_SENTINEL

    def test_get_install_rev_returns_sentinel_when_kill_switch_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        assert get_install_rev() == DISABLED_SENTINEL

    def test_get_install_rev_returns_sentinel_when_seed_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        # Emission flag is on, but no seed env var.  Should still be safe.
        assert get_install_rev() == DISABLED_SENTINEL

    def test_get_install_rev_returns_sentinel_when_seed_malformed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, "not-hex")
        ir._reset_cache_for_tests()

        assert get_install_rev() == DISABLED_SENTINEL

    def test_get_install_rev_returns_sentinel_when_seed_wrong_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, "01" * 16)  # 16 bytes, not 32
        ir._reset_cache_for_tests()

        assert get_install_rev() == DISABLED_SENTINEL

    def test_live_token_is_sixteen_lowercase_base32_chars(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        token = get_install_rev()

        assert len(token) == 16
        assert token.islower()
        assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in token)
        assert token != DISABLED_SENTINEL


# ---------------------------------------------------------------------------
# Determinism + persistence
# ---------------------------------------------------------------------------


class TestDeterminism:
    """The install-stability promise: same install → same token."""

    def test_version_byte_handles_package_not_found_before_module_not_found(self) -> None:
        """PackageNotFoundError must not be shadowed by its base class."""
        assert issubclass(PackageNotFoundError, ModuleNotFoundError)

        source = textwrap.dedent(inspect.getsource(ir._version_byte))
        tree = ast.parse(source)
        function = tree.body[0]
        assert isinstance(function, ast.FunctionDef)

        for try_node in (node for node in ast.walk(function) if isinstance(node, ast.Try)):
            earlier: list[type[BaseException]] = []
            for handler in try_node.handlers:
                current = [
                    exc_type
                    for name in _exception_names(handler.type)
                    if (exc_type := _KNOWN_EXCEPTION_TYPES.get(name)) is not None
                ]
                for exc_type in current:
                    assert not any(issubclass(exc_type, previous) for previous in earlier)
                earlier.extend(current)

    def test_compute_token_is_deterministic(self) -> None:
        seed = bytes.fromhex(TEST_SEED_HEX)
        a = _compute_token(seed, TEST_NONCE, 1)
        b = _compute_token(seed, TEST_NONCE, 1)
        assert a == b

    def test_compute_token_changes_with_seed(self) -> None:
        a = _compute_token(bytes.fromhex("01" * 32), TEST_NONCE, 1)
        b = _compute_token(bytes.fromhex("02" * 32), TEST_NONCE, 1)
        assert a != b

    def test_compute_token_changes_with_nonce(self) -> None:
        seed = bytes.fromhex(TEST_SEED_HEX)
        a = _compute_token(seed, b"\x00" * NONCE_BYTES, 1)
        b = _compute_token(seed, b"\x01" * NONCE_BYTES, 1)
        assert a != b

    def test_compute_token_changes_with_version(self) -> None:
        seed = bytes.fromhex(TEST_SEED_HEX)
        a = _compute_token(seed, TEST_NONCE, 1)
        b = _compute_token(seed, TEST_NONCE, 2)
        assert a != b

    def test_nonce_persisted_across_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        ir._reset_cache_for_tests()

        token_a = get_install_rev()

        # Drop the in-process cache so the next call re-reads the disk
        # nonce - which is the hot path on cold-start of a new process.
        ir._reset_cache_for_tests()
        token_b = get_install_rev()

        assert token_a == token_b
        assert nonce_path.is_file()
        assert len(nonce_path.read_bytes()) == NONCE_BYTES

    def test_nonce_replaced_when_corrupted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        nonce_path = tmp_path / "install_nonce"
        nonce_path.parent.mkdir(parents=True, exist_ok=True)
        # Wrong length on disk - should be silently re-minted.
        nonce_path.write_bytes(b"too-short")
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        ir._reset_cache_for_tests()

        token = get_install_rev()

        assert len(token) == 16
        assert len(nonce_path.read_bytes()) == NONCE_BYTES


# ---------------------------------------------------------------------------
# Render helpers - embedding slots
# ---------------------------------------------------------------------------


class TestRenderHelpers:
    """The three embedding-slot formatters must round-trip the token."""

    def _live_setup(self, monkeypatch: pytest.MonkeyPatch) -> str:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()
        return get_install_rev()

    def test_yaml_comment_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = self._live_setup(monkeypatch)
        line = render_yaml_comment()
        assert line == f"# bernstein-rev: {token}"
        # YAML comments do not embed newlines; callers append.
        assert "\n" not in line

    def test_trace_header_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = self._live_setup(monkeypatch)
        header = render_trace_header()
        assert header == {"_rev": token}

    def test_md_footer_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = self._live_setup(monkeypatch)
        footer = render_md_footer()
        assert footer == f"<!-- bernstein-rev: {token} -->"

    def test_disabled_render_uses_sentinel(self) -> None:
        # Default fixture: emission disabled.
        assert render_yaml_comment() == f"# bernstein-rev: {DISABLED_SENTINEL}"
        assert render_trace_header() == {"_rev": DISABLED_SENTINEL}
        assert render_md_footer() == f"<!-- bernstein-rev: {DISABLED_SENTINEL} -->"


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class TestVerifier:
    """Operator-side verifiers - shape, sentinel, full HMAC compare."""

    def test_verify_token_requires_seed(self) -> None:
        with pytest.raises(SeedNotConfiguredError):
            verify_token("aaaaaaaaaaaaaaaa")

    def test_verify_token_accepts_shape_valid(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        assert verify_token("a2c4e6g2a2c4e6g2") is True

    def test_verify_token_rejects_sentinel(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        assert verify_token(DISABLED_SENTINEL) is False

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "tooshort",
            "WAY-TOO-LONG-TOKEN-FOR-VERIFICATION",
            "AAAAAAAAAAAAAAAA",  # uppercase - base32 lower only
            "abcdefghijklmno1",  # contains '1' which is not in base32 alphabet
            "abcdefghijklmno0",  # contains '0' which is not in base32 alphabet
        ],
    )
    def test_verify_token_rejects_malformed(
        self,
        bad: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        assert verify_token(bad) is False

    def test_verify_with_nonce_round_trip(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        seed = bytes.fromhex(TEST_SEED_HEX)
        token = _compute_token(seed, TEST_NONCE, 1)

        assert verify_with_nonce(token, TEST_NONCE, version_major=1) is True

    def test_verify_with_nonce_rejects_wrong_nonce(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        seed = bytes.fromhex(TEST_SEED_HEX)
        token = _compute_token(seed, TEST_NONCE, 1)
        wrong_nonce = bytes(NONCE_BYTES)

        assert verify_with_nonce(token, wrong_nonce, version_major=1) is False

    def test_verify_with_nonce_rejects_wrong_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        seed = bytes.fromhex(TEST_SEED_HEX)
        token = _compute_token(seed, TEST_NONCE, 1)

        assert verify_with_nonce(token, TEST_NONCE, version_major=2) is False

    def test_verify_with_nonce_raises_on_bad_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        with pytest.raises(InvalidTokenError):
            verify_with_nonce("not-a-token", TEST_NONCE, version_major=1)

    def test_verify_with_nonce_raises_on_bad_nonce_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        seed = bytes.fromhex(TEST_SEED_HEX)
        token = _compute_token(seed, TEST_NONCE, 1)

        with pytest.raises(ValueError):
            verify_with_nonce(token, b"too-short", version_major=1)


# ---------------------------------------------------------------------------
# Cache + boundary
# ---------------------------------------------------------------------------


class TestCache:
    """In-process cache must not leak token state across kill-switch flips."""

    def test_cache_isolates_per_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        first = get_install_rev()

        # Flip kill switch but don't reset cache: cached value wins.
        monkeypatch.setenv(ENV_DISABLE, "1")
        cached_after_flip = get_install_rev()

        assert first == cached_after_flip

        # Reset cache: kill switch now wins.
        ir._reset_cache_for_tests()
        post_reset = get_install_rev()
        assert post_reset == DISABLED_SENTINEL
