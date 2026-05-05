"""Distribution utilities — air-gap wheelhouse build, verify, signing.

The verifier is a pluggable :class:`WheelhouseVerifier` protocol with
two implementations: :class:`CosignVerifier` (sigstore detached
signatures, the default) and :class:`GpgVerifier` (detached GPG
signatures, preferred by some sovereign customer compliance teams).

The verify flow walks every wheel in the bundle, recomputes sha256s
against ``MANIFEST.json``, and runs the chosen verifier on each
``<wheel>.sig`` (and ``MANIFEST.sig`` when present). Following the
threat-model framing in the ticket the verify routine **enumerates
every offending wheel** rather than short-circuiting on the first
failure.
"""

from __future__ import annotations

from bernstein.core.distribution.verifier import (
    CosignVerifier,
    GpgVerifier,
    PythonCryptoVerifier,
    VerifierKind,
    VerifyOutcome,
    VerifyReport,
    WheelhouseVerifier,
    select_verifier,
    verify_wheelhouse,
)

__all__ = [
    "CosignVerifier",
    "GpgVerifier",
    "PythonCryptoVerifier",
    "VerifierKind",
    "VerifyOutcome",
    "VerifyReport",
    "WheelhouseVerifier",
    "select_verifier",
    "verify_wheelhouse",
]
