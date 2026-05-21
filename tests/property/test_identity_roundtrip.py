"""Property-based roundtrip test for the install-rev fingerprint.

Generates random (seed, nonce, version_major) triples and asserts that
the operator's verify path reproduces the user's emitted token exactly.
This is the load-bearing property: if it ever fails, the operator
cannot reliably attribute a discovered token back to a real install.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    NONCE_BYTES,
    TOKEN_LEN,
    _compute_token,
    verify_with_nonce,
)

# The seed is always 32 bytes / 256 bits (project-wide invariant).
_SEED_BYTES = 32

# Hypothesis strategies - bytes() of fixed length, integer for the
# major-version cohort byte (clamped to 1..255 by ``_version_byte``).
_seed = st.binary(min_size=_SEED_BYTES, max_size=_SEED_BYTES)
_nonce = st.binary(min_size=NONCE_BYTES, max_size=NONCE_BYTES)
_version = st.integers(min_value=1, max_value=255)


@given(seed=_seed, nonce=_nonce, version_major=_version)
@settings(max_examples=200, deadline=None)
def test_emit_then_decode_is_identity(
    monkeypatch: pytest.MonkeyPatch,
    seed: bytes,
    nonce: bytes,
    version_major: int,
) -> None:
    """For every (seed, nonce, version), emit then verify_with_nonce returns True.

    This is the cryptographic roundtrip: the user's emit and the
    operator's verify use the same HMAC inputs, so the truncated tag
    must compare equal under ``hmac.compare_digest``.
    """
    token = _compute_token(seed, nonce, version_major)

    # Token shape invariant - every produced token is exactly 16 chars
    # of lowercase base32, never the disabled sentinel for any real
    # (seed, nonce) combo because all-zero output is unreachable for
    # HMAC-SHA256 in practice.
    assert len(token) == TOKEN_LEN
    assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in token)
    assert token != DISABLED_SENTINEL

    # Operator-side verification reproduces the exact bytes.
    monkeypatch.setenv("BERNSTEIN_IDENTITY_SEED", seed.hex())
    assert verify_with_nonce(token, nonce, version_major=version_major) is True


@given(
    seed=_seed,
    nonce_a=_nonce,
    nonce_b=_nonce,
    version=_version,
)
@settings(max_examples=200, deadline=None)
def test_different_nonces_produce_different_tokens(
    seed: bytes,
    nonce_a: bytes,
    nonce_b: bytes,
    version: int,
) -> None:
    """Distinct nonces under the same seed must yield distinct tokens.

    Property: HMAC-SHA256 is collision-resistant for distinct payloads;
    the truncation to 80 bits leaves ~1 in 2^80 chance of a coincidence
    which Hypothesis's 200-example budget will not realistically hit.
    """
    if nonce_a == nonce_b:
        return  # Same input, same output is the wrong question to ask.
    assert _compute_token(seed, nonce_a, version) != _compute_token(seed, nonce_b, version)


@given(
    seed_a=_seed,
    seed_b=_seed,
    nonce=_nonce,
    version=_version,
)
@settings(max_examples=200, deadline=None)
def test_different_seeds_produce_different_tokens(
    seed_a: bytes,
    seed_b: bytes,
    nonce: bytes,
    version: int,
) -> None:
    """Distinct seeds under the same nonce must yield distinct tokens.

    Property: the operator's seed is the entire trust anchor - two
    operators with different seeds emitting from the same nonce must
    produce different tokens.  A failure here would let one operator
    forge tokens for another's discovery query.
    """
    if seed_a == seed_b:
        return
    assert _compute_token(seed_a, nonce, version) != _compute_token(seed_b, nonce, version)


@given(seed=_seed, nonce=_nonce, version=_version)
@settings(max_examples=100, deadline=None)
def test_wrong_seed_fails_verify(
    monkeypatch: pytest.MonkeyPatch,
    seed: bytes,
    nonce: bytes,
    version: int,
) -> None:
    """An attacker without the seed cannot mint tokens that verify.

    Generate a token under one seed, then ask ``verify_with_nonce`` to
    confirm it under a *different* seed.  Must return False.
    """
    token = _compute_token(seed, nonce, version)
    # Flip a single bit to land in a different seed.  XOR-with-1 of
    # the first byte is a deterministic single-bit divergence.
    other_seed = bytes([seed[0] ^ 0x01]) + seed[1:]
    monkeypatch.setenv("BERNSTEIN_IDENTITY_SEED", other_seed.hex())

    assert verify_with_nonce(token, nonce, version_major=version) is False
