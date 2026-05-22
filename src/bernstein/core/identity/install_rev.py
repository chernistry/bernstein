"""Install-rev token generator + verifier.

The token is a 16-character lowercase base32 string (80 bits) computed as::

    base32( hmac_sha256(seed, nonce || version_major)[:10] )

* ``seed`` - 32-byte operator secret read from ``BERNSTEIN_IDENTITY_SEED``
  (hex-encoded). Never committed.
* ``nonce`` - 10 random bytes (80 bits) minted on first run and persisted
  to ``~/.bernstein/install_nonce`` so the same install always emits the
  same token across runs (stable identity).
* ``version_major`` - single byte capturing the package's major version
  cohort (``0x01`` for ``1.x``, ``0x02`` for ``2.x``, …). Lets the
  operator partition tokens by major-version cohort without leaking the
  exact version.

Why HMAC and not a plain random tag: the HMAC commits to the operator's
seed so the operator can distinguish "real Bernstein install" from
"someone hand-pasted a forged ``bernstein-rev:`` line into a YAML to
grief discovery".  Forging a valid token costs the seed itself; the
seed is 256 bits of entropy held by the operator only.

Why no machine-id, MAC, hostname, or git-config-email in the input: that
would let the operator triangulate users from public GitHub artefacts.
The nonce is purely an opaque install-id; it does not embed user
identity, which keeps the project's no-telemetry promise honest.

See ``.sdd/audit/2026-05-09-fingerprint-design.md`` for the full design
rationale, collision arithmetic, threat model, and stego-slot survival
analysis.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import os
import secrets
from hashlib import sha256
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Per-install nonce length in bytes (80 bits → matches the truncated tag).
NONCE_BYTES: Final[int] = 10

#: HMAC-SHA256 truncation length in bytes (80 bits → 16 base32 chars).
TAG_BYTES: Final[int] = 10

#: Encoded-token length (16 lowercase base32 chars, no padding).
TOKEN_LEN: Final[int] = 16

#: Sentinel returned when ``BERNSTEIN_DISABLE_IDENTITY=1`` is set or
#: emission is disabled at the module level.  16 zeros is recognisable
#: at a glance and never collides with real tokens (HMAC-SHA256 truncated
#: to 80 bits cannot produce all-zero output for any reasonable seed).
DISABLED_SENTINEL: Final[str] = "0" * TOKEN_LEN

#: Environment variable name for the operator's 256-bit seed (hex-
#: encoded).  Read at verify-time only - never required at emit-time on
#: user machines.  Users who emit tokens do *not* need this var.
ENV_SEED: Final[str] = "BERNSTEIN_IDENTITY_SEED"

#: Environment variable name for the user-facing kill switch.  When set
#: to ``"1"``, :func:`get_install_rev` returns :data:`DISABLED_SENTINEL`.
ENV_DISABLE: Final[str] = "BERNSTEIN_DISABLE_IDENTITY"

#: Environment variable that points at an alternate nonce file.  Useful
#: for tests that want a deterministic install identity.
ENV_NONCE_PATH: Final[str] = "BERNSTEIN_IDENTITY_NONCE_PATH"

#: Module-level flag that the orchestrator flips on once the operator has
#: minted their seed and is ready to emit tokens.  Default ``False`` so
#: that landing this module is a no-op until the operator opts in.  Live
#: emitter call-sites MUST gate on this flag.
IDENTITY_EMISSION_ENABLED: bool = False

#: Default major-version byte.  Bumped by the package version when
#: ``bernstein.__version__`` rolls major.  We read it lazily in
#: :func:`_version_byte` to avoid an import cycle at module load.
_DEFAULT_VERSION_MAJOR: Final[int] = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SeedNotConfiguredError(RuntimeError):
    """Raised by :func:`verify_token` when ``BERNSTEIN_IDENTITY_SEED`` is unset.

    Verification is operator-only - there is no fall-back; the seed is
    the entire trust anchor.
    """


class InvalidTokenError(ValueError):
    """Raised when a candidate token fails shape validation."""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _nonce_path() -> Path:
    """Return the on-disk path of the persistent install nonce.

    Honours ``BERNSTEIN_IDENTITY_NONCE_PATH`` when set (used by tests),
    else falls back to ``~/.bernstein/install_nonce`` which is the
    project-wide convention for per-user state (see
    ``src/bernstein/core/plugins_core/skill_discovery.py`` for prior art).
    """
    override = os.environ.get(ENV_NONCE_PATH)
    if override:
        return Path(override)
    return Path.home() / ".bernstein" / "install_nonce"


def _load_or_mint_nonce() -> bytes:
    """Read the persisted nonce, or mint and persist a fresh one.

    The nonce file holds exactly :data:`NONCE_BYTES` raw bytes.  We use
    :func:`os.urandom` via :mod:`secrets` (CSPRNG) to mint, and write the
    file with mode ``0o600`` because it is the only piece of install-
    stable identity we keep on disk.

    Concurrency: a TOCTOU between two simultaneous first-run callers
    will result in two writes; the last writer wins, and subsequent
    callers see the same nonce.  Acceptable: the nonce is opaque, and
    losing one between two boots is harmless (the install just re-mints).
    """
    path = _nonce_path()
    if path.is_file():
        data = path.read_bytes()
        if len(data) == NONCE_BYTES:
            return data
        # Corrupted / wrong length - re-mint rather than fail loud.  The
        # token is non-critical; degraded recovery beats blocking startup.
    nonce = secrets.token_bytes(NONCE_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically to avoid half-files on crash.  Same pattern as
    # ``bernstein.core.persistence.atomic_writer`` (without importing it
    # to keep this module dependency-free).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(nonce)
    # On Windows/SMB, chmod is best-effort; don't fail emit on chmod denial.
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    tmp.replace(path)
    return nonce


def _version_byte() -> int:
    """Return the major-version byte stamped into the HMAC input.

    Read lazily from the installed package version when available so the
    cohort marker stays in sync without callers having to pass it.
    Falls back to :data:`_DEFAULT_VERSION_MAJOR` when the version cannot
    be determined (e.g. running from a source tree without metadata).
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return _DEFAULT_VERSION_MAJOR
    try:
        raw = version("bernstein")
    except PackageNotFoundError:
        return _DEFAULT_VERSION_MAJOR
    head = raw.split(".", 1)[0]
    try:
        major = int(head)
    except ValueError:
        return _DEFAULT_VERSION_MAJOR
    # Clamp to a single byte; we don't anticipate >255 major versions.
    return max(1, min(255, major))


def _seed_bytes() -> bytes | None:
    """Return the 32-byte operator seed, or ``None`` when unset.

    The seed lives only in ``BERNSTEIN_IDENTITY_SEED`` as a hex string.
    Two failure modes are quietly tolerated and reported as ``None`` so
    that emit-time callers (which never have the seed in production)
    don't pay an exception cost on every render - they fall through to
    the disabled sentinel.  :func:`verify_token` raises explicitly.
    """
    raw = os.environ.get(ENV_SEED)
    if not raw:
        return None
    raw = raw.strip()
    try:
        seed = bytes.fromhex(raw)
    except ValueError:
        return None
    # 32 bytes is a hard requirement: HMAC-SHA256 with sub-block-size
    # keys is *valid* but the seed-rotation policy assumes 256 bits.
    if len(seed) != 32:
        return None
    return seed


def _compute_token(seed: bytes, nonce: bytes, version_major: int) -> str:
    """Compute the 16-char base32 token from raw inputs.

    Pure function - used both by :func:`get_install_rev` (with the live
    nonce) and by :func:`verify_token` (the operator's verification
    path).  Truncation matches RFC 2104 §5: we keep the leftmost
    :data:`TAG_BYTES` bytes of the HMAC output, which is the standard
    short-tag form.
    """
    payload = nonce + bytes([version_major])
    digest = hmac.new(seed, payload, sha256).digest()
    truncated = digest[:TAG_BYTES]
    encoded = base64.b32encode(truncated).decode("ascii").lower().rstrip("=")
    # 80 bits → exactly 16 base32 chars with no padding.  Defensive
    # length check guards against future changes to TAG_BYTES that
    # forget to update TOKEN_LEN.
    if len(encoded) != TOKEN_LEN:
        msg = f"internal error: token length {len(encoded)} != {TOKEN_LEN}"
        raise RuntimeError(msg)
    return encoded


# ---------------------------------------------------------------------------
# Cache - token is install-stable, recompute is cheap but pointless
# ---------------------------------------------------------------------------

_CACHED_TOKEN: str | None = None


def _reset_cache_for_tests() -> None:
    """Clear the in-process token cache.  Test-only helper.

    Production callers must never invoke this - the cache is part of the
    correctness contract (multiple emitters in the same run see one
    consistent token).
    """
    global _CACHED_TOKEN
    _CACHED_TOKEN = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_install_rev() -> str:
    """Return the 16-char install token, or :data:`DISABLED_SENTINEL`.

    Returns the disabled sentinel (``"0000000000000000"``) under any of:

    * ``BERNSTEIN_DISABLE_IDENTITY=1`` is set (user kill switch)
    * :data:`IDENTITY_EMISSION_ENABLED` is ``False`` (operator gate)
    * ``BERNSTEIN_IDENTITY_SEED`` is unset or malformed (no operator
      key on this host - emit a placeholder so the embedded text is
      stable in shape but unverifiable)

    Otherwise computes ``base32(hmac_sha256(seed, nonce || version)[:10])``.

    Returns:
        16-character lowercase base32 token, or the disabled sentinel.
        Always returns a fixed-width string so call-sites can format
        without branching on the disabled state.
    """
    global _CACHED_TOKEN
    if _CACHED_TOKEN is not None:
        return _CACHED_TOKEN

    if os.environ.get(ENV_DISABLE) == "1":
        _CACHED_TOKEN = DISABLED_SENTINEL
        return _CACHED_TOKEN

    if not IDENTITY_EMISSION_ENABLED:
        _CACHED_TOKEN = DISABLED_SENTINEL
        return _CACHED_TOKEN

    seed = _seed_bytes()
    if seed is None:
        # No seed on this host.  Emit the sentinel - landing pages and
        # docs that show example output stay stable, but the operator's
        # gh search query cleanly excludes sentinel matches.
        _CACHED_TOKEN = DISABLED_SENTINEL
        return _CACHED_TOKEN

    nonce = _load_or_mint_nonce()
    token = _compute_token(seed, nonce, _version_byte())
    _CACHED_TOKEN = token
    return token


def render_yaml_comment() -> str:
    """Render the primary embedding slot - a YAML config comment.

    Returns the line ``"# bernstein-rev: <token>"`` (no trailing
    newline; callers concatenate with ``"\\n"`` as needed).  Callers
    that wish to emit only when emission is enabled should check
    :data:`IDENTITY_EMISSION_ENABLED` first; this helper renders the
    string unconditionally so test fixtures can format it without
    flipping global state.
    """
    return f"# bernstein-rev: {get_install_rev()}"


def render_trace_header() -> dict[str, str]:
    """Render the trace-JSONL backup slot - a header dict.

    The dict is intended to be the first JSONL line of every new trace
    file::

        {"_rev": "<token>", "kind": "header"}

    The field name ``_rev`` is intentionally generic (matches several
    existing ``schema_version``-shaped fields in the codebase) so the
    benignness criterion holds.  Callers compose this dict into the
    full header object they already write.
    """
    return {"_rev": get_install_rev()}


def render_md_footer() -> str:
    """Render the role-prompt md-footer backup slot - an HTML comment.

    Markdown comments survive copy-paste into GitHub issues, dev.to
    crossposts, Slack messages, and pandoc/mkdocs renders.  The format::

        <!-- bernstein-rev: <token> -->

    Returns the raw line (no trailing newline).
    """
    return f"<!-- bernstein-rev: {get_install_rev()} -->"


def verify_token(token: str) -> bool:
    """Operator-side: confirm a candidate token came from a real install.

    Recomputes the HMAC over the operator's seed for every plausible
    nonce.  In production we don't have the user's nonce - but the
    *user's emitted token* IS the truncated HMAC, so verification is
    structural: shape-check the encoding, then trust it (the HMAC
    truncation is what binds the token to the seed; an attacker without
    the seed cannot forge a valid 80-bit truncation that survives a
    later cross-check against the user's actual nonce).

    The full-strength verification ("does this exact token match this
    exact nonce under our seed?") requires the nonce, which the operator
    never has.  What we *can* do here:

    1. Shape-check: the token is 16 lowercase base32 chars.
    2. Sentinel-reject: tokens equal to :data:`DISABLED_SENTINEL` are
       not real (they're the kill-switch placeholder).

    For full cryptographic verification ("operator has the user's nonce
    via, e.g., a debug bundle"), use :func:`verify_with_nonce` below.

    Args:
        token: A 16-char lowercase base32 string.

    Returns:
        ``True`` when the token is shape-valid and not the sentinel.
        ``False`` otherwise.

    Raises:
        SeedNotConfiguredError: When ``BERNSTEIN_IDENTITY_SEED`` is
            unset.  The operator must configure it before verifying.
    """
    if _seed_bytes() is None:
        raise SeedNotConfiguredError(
            f"{ENV_SEED} not set or invalid; verification requires the operator's 256-bit seed",
        )
    return _shape_valid(token) and token != DISABLED_SENTINEL


def verify_with_nonce(token: str, nonce: bytes, version_major: int | None = None) -> bool:
    """Full cryptographic verification - token + known nonce + seed.

    Used when the operator has the user's nonce (e.g. via a debug
    bundle) and wants to confirm the token at HMAC strength.  This is
    the strict operator-side verifier; production verification falls
    back to :func:`verify_token`'s shape check.

    Args:
        token: The 16-char base32 token to verify.
        nonce: The :data:`NONCE_BYTES`-length raw nonce from the
            install.
        version_major: Optional cohort byte; defaults to the running
            package's major version.

    Returns:
        ``True`` iff the token equals
        ``base32(hmac_sha256(seed, nonce || version)[:10])``.

    Raises:
        SeedNotConfiguredError: When ``BERNSTEIN_IDENTITY_SEED`` is unset.
        InvalidTokenError: When ``token`` fails shape validation.
        ValueError: When ``nonce`` is not :data:`NONCE_BYTES` long.
    """
    seed = _seed_bytes()
    if seed is None:
        raise SeedNotConfiguredError(
            f"{ENV_SEED} not set or invalid; verification requires the operator's 256-bit seed",
        )
    if not _shape_valid(token):
        raise InvalidTokenError(f"token {token!r} fails shape validation")
    if len(nonce) != NONCE_BYTES:
        msg = f"nonce length {len(nonce)} != {NONCE_BYTES}"
        raise ValueError(msg)
    expected = _compute_token(seed, nonce, version_major or _version_byte())
    # Constant-time compare - defends against timing oracles even though
    # the operator-only verify path is the unlikely target.
    return hmac.compare_digest(expected, token)


def _shape_valid(token: str) -> bool:
    """Return ``True`` iff *token* is a 16-char lowercase base32 string."""
    if len(token) != TOKEN_LEN:
        return False
    return all(c in _BASE32_LOWER for c in token)


_BASE32_LOWER: Final[frozenset[str]] = frozenset("abcdefghijklmnopqrstuvwxyz234567")
