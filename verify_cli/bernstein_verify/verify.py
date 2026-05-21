"""Standalone re-implementation of Bernstein lineage v1 verification.

This module is the heart of `bernstein-verify`. It MUST NOT import
anything from `bernstein.*`. Three primitives are re-implemented here:

  * `jcs_canonicalise` - RFC 8785 JSON Canonicalisation Scheme, byte-for-byte
    identical to `bernstein.core.lineage.entry.canonicalise` on the flat
    dict shapes used by lineage v1. Cross-tested under tests/test_verify.py.
  * `verify_jws_detached` - RFC 7515 detached JWS with EdDSA / Ed25519
    (RFC 8037) and the unencoded-payload extension (RFC 7797, `b64=false`).
    Matches `bernstein.core.lineage.identity.verify_detached` exactly.
  * `walk_chain` - parent-hash DAG walk; surfaces orphans + duplicates.

`verify_pack` wires the three primitives against a compliance-pack ZIP.

Air-gap guarantee: no network calls. No imports of httpx/requests/urllib*.
Only stdlib + `cryptography`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Names of the files we expect inside a compliance pack.
_LOG_NAME = "lineage-log.jsonl"
_SIG_DIR = "signatures/"
_CARD_DIR = "agent-cards/"


# ---------- RFC 8785 JCS ----------


def jcs_canonicalise(d: dict[str, Any]) -> bytes:
    """RFC 8785 JSON Canonicalisation Scheme (the subset used by lineage v1).

    LineageEntry is a flat dataclass of (str, int, list[str]); none of the
    full-blown ES6-number / nested-object corner cases of RFC 8785 apply.
    The subset reduces to: sort_keys=True, minimal separators, UTF-8 bytes.

    Cross-tested for byte-equality with bernstein's `canonicalise` in
    tests/test_verify.py. If bernstein ever extends the schema, this MUST
    be updated and the byte-equality test will fail loudly.
    """
    return json.dumps(
        d,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


# ---------- RFC 7515 detached JWS ----------


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_jws_detached(
    payload: bytes,
    jws: str,
    public_key_pem: str,
    *,
    expected_kid: str | None = None,
) -> bool:
    """Verify a detached Ed25519 JWS against a PEM-encoded public key.

    Matches `bernstein.core.lineage.identity.verify_detached`. Returns
    False on ANY malformed input, mismatched kid, wrong key, invalid
    signature, or non-EdDSA algorithm. Never raises on bad input - the
    auditor invokes this on attacker-controlled bytes.

    `expected_kid` is enforced when supplied. Pass `None` to skip the
    kid check (rare; usually you have a card to bind against).
    """
    try:
        protected_b64, empty, sig_b64 = jws.split(".", maxsplit=2)
    except ValueError:
        return False
    if empty != "":
        return False
    if "." in sig_b64:
        return False  # 4+ segments

    try:
        header = json.loads(_b64url_decode(protected_b64))
    except (ValueError, json.JSONDecodeError):
        return False
    if not isinstance(header, dict):
        return False
    if header.get("alg") != "EdDSA":
        return False
    if expected_kid is not None and header.get("kid") != expected_kid:
        return False

    try:
        pub = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return False
    if not isinstance(pub, Ed25519PublicKey):
        return False

    signing_input = protected_b64.encode("ascii") + b"." + payload
    try:
        sig_bytes = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return False

    try:
        pub.verify(sig_bytes, signing_input)
    except InvalidSignature:
        return False
    return True


# ---------- chain walking ----------


def _entry_hash(entry: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(jcs_canonicalise(entry)).hexdigest()


def walk_chain(entries: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Validate the parent-hash DAG.

    Reports:
      * duplicate entries (same entry_hash appears >1 time)
      * orphan parents (entry references a parent_hash not present in the log)

    Order-independent: parents may appear after children in `entries`.
    Returns (ok, errors). `errors` is a list of human-readable diagnostics.

    NOTE: This does NOT verify signatures - that's `verify_jws_detached`'s
    job. `verify_pack` composes both. Splitting them keeps each unit
    testable in isolation and lets the caller decide whether to skip
    signature checks (e.g. fast fork-detection on CI).
    """
    errors: list[str] = []
    by_hash: dict[str, dict[str, Any]] = {}

    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append(f"entry #{idx}: not a JSON object")
            continue
        h = _entry_hash(e)
        if h in by_hash:
            errors.append(f"duplicate entry {h}")
            continue
        by_hash[h] = e

    for h, e in by_hash.items():
        parents = e.get("parent_hashes", [])
        if not isinstance(parents, list):
            errors.append(f"entry {h}: parent_hashes is not a list")
            continue
        for p in parents:
            if not isinstance(p, str):
                errors.append(f"entry {h}: parent hash not a string")
                continue
            if p not in by_hash:
                errors.append(f"entry {h}: orphan parent (unknown parent {p})")

    return (not errors, errors)


# ---------- pack verification ----------


@dataclass
class VerifyResult:
    """Outcome of a verify_pack call.

    Surfaces in CLI JSON output (stderr). `ok` is the boolean exit signal.
    `errors` is human-readable; `stats` is structured for machine consumers.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def _read_text_member(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        with zf.open(name) as f:
            return f.read().decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return None


def verify_pack(zip_path: Path | str) -> VerifyResult:
    """Verify a compliance-pack ZIP end-to-end.

    Expected layout (per ADR-009 §8.2):

        lineage-log.jsonl
        signatures/<entry_hash>.jws       (one file per entry)
        agent-cards/<agent_id>.json       (one file per agent seen)

    Steps:
      1. Open the zip (defensive: never extractall - read members in memory).
      2. Parse log.jsonl into a list of entries.
      3. Walk the parent-hash chain (orphans, dupes).
      4. For every entry: compute entry_hash, find sidecar JWS, find Agent
         Card by agent_id, verify Ed25519 JWS using card's public key + kid.

    Returns a VerifyResult with ok=False on the first ZIP-level failure
    (missing log, unreadable archive) so the CLI can short-circuit.
    All per-entry failures are collected into `errors`.
    """
    path = Path(zip_path)
    if not path.exists():
        return VerifyResult(ok=False, errors=[f"pack not found: {path}"])

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return VerifyResult(ok=False, errors=[f"not a valid zip archive: {path}"])

    with zf:
        log_raw = _read_text_member(zf, _LOG_NAME)
        if log_raw is None:
            return VerifyResult(ok=False, errors=[f"missing {_LOG_NAME} in pack"])

        entries: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        for lineno, line in enumerate(log_raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parse_errors.append(f"{_LOG_NAME}:{lineno}: invalid JSON ({exc.msg})")

        result_errors: list[str] = list(parse_errors)

        chain_ok, chain_errors = walk_chain(entries)
        result_errors.extend(chain_errors)

        # Pre-load agent cards (one per agent_id).
        cards: dict[str, dict[str, Any]] = {}
        for info in zf.infolist():
            # Defence-in-depth: ignore zip-slip paths. We never write
            # files anyway, but skip suspicious names so we don't try to
            # parse `../../etc/passwd` as a card.
            if ".." in Path(info.filename).parts:
                continue
            if not info.filename.startswith(_CARD_DIR) or info.filename.endswith("/"):
                continue
            card_raw = _read_text_member(zf, info.filename)
            if card_raw is None:
                continue
            try:
                card = json.loads(card_raw)
            except json.JSONDecodeError:
                result_errors.append(f"{info.filename}: invalid JSON")
                continue
            aid = card.get("agent_id")
            if isinstance(aid, str):
                cards[aid] = card

        # Per-entry signature verification.
        sig_failures = 0
        for e in entries:
            entry_hash = _entry_hash(e)
            agent_id = e.get("agent_id", "")
            expected_kid = e.get("agent_card_kid", "")
            card = cards.get(agent_id)
            if card is None:
                result_errors.append(f"entry {entry_hash}: no Agent Card for {agent_id}")
                sig_failures += 1
                continue
            if card.get("kid") != expected_kid:
                result_errors.append(
                    f"entry {entry_hash}: kid mismatch (card={card.get('kid')!r}, "
                    f"entry={expected_kid!r})"
                )
                sig_failures += 1
                continue
            sig_member = f"{_SIG_DIR}{entry_hash}.jws"
            jws = _read_text_member(zf, sig_member)
            if jws is None:
                result_errors.append(f"entry {entry_hash}: missing signature {sig_member}")
                sig_failures += 1
                continue
            payload = jcs_canonicalise(e)
            pub_pem = card.get("public_key_pem", "")
            if not isinstance(pub_pem, str) or not verify_jws_detached(
                payload, jws, pub_pem, expected_kid=expected_kid
            ):
                result_errors.append(f"entry {entry_hash}: signature verification failed")
                sig_failures += 1

        stats = {
            "entries": len(entries),
            "agents": len(cards),
            "chain_ok": chain_ok,
            "signature_failures": sig_failures,
        }
        ok = not parse_errors and chain_ok and sig_failures == 0
        return VerifyResult(ok=ok, errors=result_errors, stats=stats)
