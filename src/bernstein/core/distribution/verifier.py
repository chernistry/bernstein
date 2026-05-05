"""Pluggable wheelhouse verifier protocol + concrete verifiers.

Three implementations are bundled:

* :class:`PythonCryptoVerifier` — pure-Python verification via the
  ``cryptography`` library (Ed25519 / ECDSA / RSA-PSS PEM keys). Used
  when the operator passes a PEM ``--ca-pubkey`` and the bundle was
  signed with a raw key blob.
* :class:`CosignVerifier` — shells out to the ``cosign`` CLI. Default
  for sigstore-style bundles produced by
  ``scripts/sign_airgap_wheelhouse.sh``.
* :class:`GpgVerifier` — shells out to ``gpg`` (or ``gpg2``) for
  customers whose compliance teams prefer GPG to sigstore.

The verify routine enumerates every offending wheel; it does not
short-circuit on the first failure (matches the SAST-style framing
in the ticket).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from pathlib import Path


class VerifierKind(StrEnum):
    """Selectable verifier backends for the CLI ``--verifier`` flag."""

    AUTO = "auto"
    CRYPTO = "crypto"
    COSIGN = "cosign"
    GPG = "gpg"


@dataclass(frozen=True)
class VerifyOutcome:
    """Single-wheel outcome row in :class:`VerifyReport`.

    Attributes:
        name: Wheel filename inside the bundle.
        sha256_ok: True iff the recomputed sha256 matched the manifest.
        signature_present: True iff ``<wheel>.sig`` exists alongside.
        signature_ok: True iff the verifier accepted the signature.
            ``None`` means "not checked" (no signature, no key).
        error: Human-readable failure reason; empty on success.
    """

    name: str
    sha256_ok: bool
    signature_present: bool
    signature_ok: bool | None
    error: str = ""


@dataclass(frozen=True)
class VerifyReport:
    """Aggregate result returned by :func:`verify_wheelhouse`.

    Attributes:
        ok: True iff every wheel passed every check that was applied.
        verifier: Name of the verifier that ran (``"none"`` when
            checksum-only).
        wheels_total: Wheel count enumerated from the manifest.
        wheels_verified: Number of wheels with passing sha256.
        signatures_present: Number of wheels with a sibling ``.sig``.
        signatures_verified: Number of signatures the verifier accepted.
        manifest_signature_ok: True/False/None for ``MANIFEST.sig``;
            ``None`` means the signature file was not present.
        outcomes: Per-wheel rows in deterministic (name-sorted) order.
        failures: Human-readable failure messages naming each offender.
    """

    ok: bool
    verifier: str
    wheels_total: int
    wheels_verified: int
    signatures_present: int
    signatures_verified: int
    manifest_signature_ok: bool | None
    outcomes: tuple[VerifyOutcome, ...] = ()
    failures: tuple[str, ...] = ()


class WheelhouseVerifier(Protocol):
    """Protocol every wheelhouse signature backend implements.

    The protocol is intentionally minimal — verifiers receive the
    blob and the detached signature path and return ``True`` on
    success. Backends that need extra material (a public key, a
    keyring, a cosign bundle) accept it via their constructor.
    """

    name: str

    def verify(self, blob: Path, signature: Path) -> bool:
        """Return ``True`` iff ``signature`` is a valid detached
        signature for the bytes of ``blob``.

        Implementations MUST swallow validation errors and return
        ``False`` so the caller can enumerate every offender. They
        MUST NOT raise on a missing tool — instead expose
        :meth:`available` for capability detection.
        """
        ...

    def available(self) -> bool:
        """Return ``True`` iff this verifier can run on the host."""
        ...


@dataclass
class PythonCryptoVerifier:
    """PEM-key verifier backed by the ``cryptography`` library."""

    pubkey_path: Path
    name: str = "crypto"

    def available(self) -> bool:
        try:
            import cryptography  # noqa: F401
        except ImportError:
            return False
        return self.pubkey_path.exists()

    def verify(self, blob: Path, signature: Path) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

        try:
            pem = self.pubkey_path.read_bytes()
            public_key = serialization.load_pem_public_key(pem)
            sig_bytes = signature.read_bytes()
            blob_bytes = blob.read_bytes()
            if isinstance(public_key, ed25519.Ed25519PublicKey):
                public_key.verify(sig_bytes, blob_bytes)
                return True
            if isinstance(public_key, ec.EllipticCurvePublicKey):
                public_key.verify(sig_bytes, blob_bytes, ec.ECDSA(hashes.SHA256()))
                return True
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(
                    sig_bytes,
                    blob_bytes,
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                    hashes.SHA256(),
                )
                return True
        except (InvalidSignature, ValueError, TypeError, OSError):
            return False
        return False


@dataclass
class CosignVerifier:
    """Verifier that shells out to the ``cosign`` CLI.

    Either ``pubkey_path`` (offline key) or ``identity`` + ``issuer``
    (sigstore keyless) must be set. Both forms are common at sovereign
    customers — the offline key is more common in the air-gap path.
    """

    pubkey_path: Path | None = None
    identity: str | None = None
    issuer: str | None = None
    name: str = "cosign"

    def available(self) -> bool:
        return shutil.which("cosign") is not None

    def verify(self, blob: Path, signature: Path) -> bool:
        cmd: list[str] = ["cosign", "verify-blob", "--signature", str(signature)]
        if self.pubkey_path is not None:
            cmd += ["--key", str(self.pubkey_path)]
        if self.identity:
            cmd += ["--certificate-identity", self.identity]
        if self.issuer:
            cmd += ["--certificate-oidc-issuer", self.issuer]
        cmd.append(str(blob))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0


@dataclass
class GpgVerifier:
    """Verifier that shells out to ``gpg`` (or ``gpg2``) for detached sigs.

    Optional ``keyring_path`` pins verification to a specific keyring
    so customers can ship a single signed keyring alongside the
    bundle without polluting the operator's default GPG home.
    """

    keyring_path: Path | None = None
    name: str = "gpg"

    def _binary(self) -> str | None:
        for candidate in ("gpg", "gpg2"):
            path = shutil.which(candidate)
            if path is not None:
                return path
        return None

    def available(self) -> bool:
        return self._binary() is not None

    def verify(self, blob: Path, signature: Path) -> bool:
        binary = self._binary()
        if binary is None:
            return False
        cmd: list[str] = [binary, "--batch", "--quiet", "--no-tty"]
        if self.keyring_path is not None:
            cmd += ["--no-default-keyring", "--keyring", str(self.keyring_path)]
        cmd += ["--verify", str(signature), str(blob)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0


def select_verifier(
    kind: VerifierKind | str,
    *,
    pubkey_path: Path | None = None,
    keyring_path: Path | None = None,
    cosign_identity: str | None = None,
    cosign_issuer: str | None = None,
) -> WheelhouseVerifier | None:
    """Return a verifier instance for ``kind``.

    ``VerifierKind.AUTO`` picks the best available backend in this
    order: explicit PEM key (crypto), cosign with a key, gpg with a
    keyring. Returns ``None`` when nothing usable is configured —
    callers fall back to checksum-only verification.
    """
    if isinstance(kind, str):
        try:
            kind = VerifierKind(kind.lower())
        except ValueError:
            kind = VerifierKind.AUTO

    if kind == VerifierKind.CRYPTO:
        if pubkey_path is None:
            return None
        verifier = PythonCryptoVerifier(pubkey_path=pubkey_path)
        return verifier if verifier.available() else None

    if kind == VerifierKind.COSIGN:
        verifier_cosign = CosignVerifier(
            pubkey_path=pubkey_path,
            identity=cosign_identity,
            issuer=cosign_issuer,
        )
        return verifier_cosign if verifier_cosign.available() else None

    if kind == VerifierKind.GPG:
        verifier_gpg = GpgVerifier(keyring_path=keyring_path)
        return verifier_gpg if verifier_gpg.available() else None

    if pubkey_path is not None:
        verifier_pem = PythonCryptoVerifier(pubkey_path=pubkey_path)
        if verifier_pem.available():
            return verifier_pem
        verifier_cosign_auto = CosignVerifier(pubkey_path=pubkey_path)
        if verifier_cosign_auto.available():
            return verifier_cosign_auto
    if keyring_path is not None:
        verifier_gpg_auto = GpgVerifier(keyring_path=keyring_path)
        if verifier_gpg_auto.available():
            return verifier_gpg_auto
    return None


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    raw = cast("dict[str, Any]", json.loads(manifest_path.read_text()))
    wheels_any: Any = raw.get("wheels") or []
    if not isinstance(wheels_any, list):
        return []
    return [cast("dict[str, Any]", e) for e in cast("list[Any]", wheels_any) if isinstance(e, dict)]


def verify_wheelhouse(
    wheelhouse_path: Path,
    *,
    verifier: WheelhouseVerifier | None = None,
    require_signatures: bool = False,
) -> VerifyReport:
    """Walk every wheel in the bundle and aggregate the result.

    The function never raises on a tampered or missing wheel — it
    enumerates every offender and surfaces the full damage list via
    :class:`VerifyReport.failures`. Callers translate this into the
    operator-facing CLI output.
    """
    failures: list[str] = []

    if not wheelhouse_path.exists() or not wheelhouse_path.is_dir():
        return VerifyReport(
            ok=False,
            verifier=verifier.name if verifier else "none",
            wheels_total=0,
            wheels_verified=0,
            signatures_present=0,
            signatures_verified=0,
            manifest_signature_ok=None,
            failures=(f"wheelhouse not found: {wheelhouse_path}",),
        )

    manifest_path = wheelhouse_path / "MANIFEST.json"
    if not manifest_path.exists():
        return VerifyReport(
            ok=False,
            verifier=verifier.name if verifier else "none",
            wheels_total=0,
            wheels_verified=0,
            signatures_present=0,
            signatures_verified=0,
            manifest_signature_ok=None,
            failures=(f"missing MANIFEST.json in: {wheelhouse_path}",),
        )

    try:
        wheels = _load_manifest(manifest_path)
    except json.JSONDecodeError as exc:
        return VerifyReport(
            ok=False,
            verifier=verifier.name if verifier else "none",
            wheels_total=0,
            wheels_verified=0,
            signatures_present=0,
            signatures_verified=0,
            manifest_signature_ok=None,
            failures=(f"malformed MANIFEST.json: {exc}",),
        )

    if not wheels:
        return VerifyReport(
            ok=False,
            verifier=verifier.name if verifier else "none",
            wheels_total=0,
            wheels_verified=0,
            signatures_present=0,
            signatures_verified=0,
            manifest_signature_ok=None,
            failures=("MANIFEST.json contains no wheels",),
        )

    outcomes: list[VerifyOutcome] = []
    verified = 0
    sig_present = 0
    sig_verified = 0

    for entry in sorted(wheels, key=lambda e: str(e.get("name", ""))):
        name_raw = entry.get("name")
        sha_raw = entry.get("sha256")
        name = str(name_raw) if isinstance(name_raw, str) else ""
        expected_sha = str(sha_raw) if isinstance(sha_raw, str) else ""
        if not name or not expected_sha:
            failures.append(f"manifest entry malformed: {entry!r}")
            outcomes.append(
                VerifyOutcome(
                    name=name or "<unnamed>",
                    sha256_ok=False,
                    signature_present=False,
                    signature_ok=None,
                    error="malformed manifest entry",
                )
            )
            continue

        wheel_path = wheelhouse_path / name
        if not wheel_path.exists():
            failures.append(f"missing wheel: {name}")
            outcomes.append(
                VerifyOutcome(
                    name=name,
                    sha256_ok=False,
                    signature_present=False,
                    signature_ok=None,
                    error="missing wheel",
                )
            )
            continue

        actual = _hash_file(wheel_path)
        sha_ok = actual == expected_sha
        if not sha_ok:
            failures.append(f"sha256 mismatch: {name} (expected {expected_sha[:12]}..., got {actual[:12]}...)")

        sig_path = wheel_path.with_suffix(wheel_path.suffix + ".sig")
        sig_check: bool | None = None
        sig_present_here = sig_path.exists()
        if sig_present_here:
            sig_present += 1
            if verifier is not None:
                sig_check = verifier.verify(wheel_path, sig_path)
                if sig_check:
                    sig_verified += 1
                else:
                    failures.append(f"signature invalid ({verifier.name}): {name}")
        elif require_signatures:
            failures.append(f"missing signature: {name}")
            sig_check = False

        if sha_ok:
            verified += 1

        outcomes.append(
            VerifyOutcome(
                name=name,
                sha256_ok=sha_ok,
                signature_present=sig_present_here,
                signature_ok=sig_check,
                error="" if sha_ok and (sig_check is not False) else "verification failed",
            )
        )

    manifest_sig_ok: bool | None = None
    manifest_sig_path = wheelhouse_path / "MANIFEST.sig"
    if manifest_sig_path.exists():
        if verifier is not None:
            manifest_sig_ok = verifier.verify(manifest_path, manifest_sig_path)
            if manifest_sig_ok is False:
                failures.append(f"signature invalid ({verifier.name}): MANIFEST.json")
    elif require_signatures:
        manifest_sig_ok = False
        failures.append("missing signature: MANIFEST.json")

    return VerifyReport(
        ok=not failures,
        verifier=verifier.name if verifier else "none",
        wheels_total=len(wheels),
        wheels_verified=verified,
        signatures_present=sig_present,
        signatures_verified=sig_verified,
        manifest_signature_ok=manifest_sig_ok,
        outcomes=tuple(outcomes),
        failures=tuple(failures),
    )
