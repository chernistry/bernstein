"""Catalog manifest schema for skill packs with signed manifests.

Mirrors the structure of :mod:`bernstein.core.protocols.mcp_catalog.manifest`
but for skill packs. Each catalog entry references an installable source
(github, git, npm, file, or directory variant of
:class:`PluginSource`) along with a content digest and an optional
detached Ed25519 signature.

The schema is intentionally strict: any unknown field rejects the whole
fetch so a poisoned upstream cannot widen the trust boundary by sneaking
in extra keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CATALOG_REQUIRED: frozenset[str] = frozenset({"version", "generated_at", "entries"})
_CATALOG_OPTIONAL: frozenset[str] = frozenset({"signer_pubkey"})
_CATALOG_ALLOWED: frozenset[str] = _CATALOG_REQUIRED | _CATALOG_OPTIONAL

_ENTRY_REQUIRED: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "version",
        "description",
        "source",
        "content_digest",
    }
)
_ENTRY_OPTIONAL: frozenset[str] = frozenset(
    {
        "signature",
        "homepage",
        "tags",
        "verified",
    }
)
_ENTRY_ALLOWED: frozenset[str] = _ENTRY_REQUIRED | _ENTRY_OPTIONAL

_SOURCE_REQUIRED_BY_KIND: dict[str, frozenset[str]] = {
    "github": frozenset({"repo"}),
    "git": frozenset({"url"}),
    "npm": frozenset({"package"}),
    "file": frozenset({"path"}),
    "directory": frozenset({"path"}),
}
_SOURCE_OPTIONAL_BY_KIND: dict[str, frozenset[str]] = {
    "github": frozenset({"tag", "asset"}),
    "git": frozenset({"ref"}),
    "npm": frozenset({"version"}),
    "file": frozenset(),
    "directory": frozenset(),
}
_VALID_SOURCE_KINDS: frozenset[str] = frozenset(_SOURCE_REQUIRED_BY_KIND)

_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_SUPPORTED_SCHEMA_VERSION = 1


class SkillCatalogValidationError(ValueError):
    """Raised when a fetched skill catalog payload fails strict validation.

    Callers should treat this as a hard failure and preserve any
    previously cached catalog instead of overwriting it.
    """


@dataclass(frozen=True)
class SkillSourceSpec:
    """Declarative source descriptor for a catalog entry.

    Resolves to a :class:`bernstein.core.plugins_core.plugin_installer.PluginSource`
    at install time. Storing the spec as a plain dataclass (rather than a
    union) keeps the JSON wire format flat and the validator strict.
    """

    kind: str
    repo: str = ""
    url: str = ""
    package: str = ""
    path: str = ""
    tag: str = "latest"
    ref: str = "HEAD"
    version: str = "latest"
    asset: str | None = None

    def url_for_audit(self) -> str:
        """Stable URL-shaped string used for audit-chain entries.

        The MCP catalog audit emits the manifest URL verbatim; for skills
        we synthesise a URL-style locator from the source variant so the
        replay check has a comparable string handle.
        """
        if self.kind == "github":
            tag = self.tag or "latest"
            return f"github://{self.repo}@{tag}"
        if self.kind == "git":
            return f"git+{self.url}@{self.ref or 'HEAD'}"
        if self.kind == "npm":
            return f"npm:{self.package}@{self.version or 'latest'}"
        if self.kind == "file":
            return f"file://{self.path}"
        if self.kind == "directory":
            return f"directory://{self.path}"
        return f"{self.kind}://unknown"

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the wire format dict."""
        out: dict[str, Any] = {"kind": self.kind}
        if self.kind == "github":
            out["repo"] = self.repo
            out["tag"] = self.tag
            if self.asset is not None:
                out["asset"] = self.asset
        elif self.kind == "git":
            out["url"] = self.url
            out["ref"] = self.ref
        elif self.kind == "npm":
            out["package"] = self.package
            out["version"] = self.version
        elif self.kind in {"file", "directory"}:
            out["path"] = self.path
        return out


@dataclass(frozen=True)
class SkillCatalogEntry:
    """A single installable skill catalog entry.

    Attributes:
        id: Stable slug used as the ``install <id>`` argument.
        name: Human-readable display name.
        version: Pinned version string used for upgrade detection.
        description: One-paragraph summary.
        source: Source variant (github / git / npm / file / directory).
        content_digest: Hex SHA-256 of the installed skill content. The
            installer recomputes this after install and refuses to
            register the skill if the on-disk digest disagrees.
        signature: Optional detached Ed25519 signature over the
            canonicalised entry payload. When present, the install path
            verifies it against the catalog-level ``signer_pubkey``
            unless ``--allow-unverified`` is set.
        homepage: Optional homepage URL.
        tags: Optional discovery tags.
        verified: Whether the upstream catalog operator vouches for
            the entry. Defaults to ``False``; unverified entries surface
            a warning at install time.
    """

    id: str
    name: str
    version: str
    description: str
    source: SkillSourceSpec
    content_digest: str
    signature: str | None = None
    homepage: str = ""
    tags: tuple[str, ...] = ()
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the JSON wire format."""
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "source": self.source.to_dict(),
            "content_digest": self.content_digest,
            "verified": self.verified,
        }
        if self.signature is not None:
            out["signature"] = self.signature
        if self.homepage:
            out["homepage"] = self.homepage
        if self.tags:
            out["tags"] = list(self.tags)
        return out


@dataclass(frozen=True)
class SkillCatalog:
    """A validated skill catalog payload."""

    version: int
    generated_at: str
    entries: tuple[SkillCatalogEntry, ...]
    signer_pubkey: str | None = field(default=None)

    def find(self, entry_id: str) -> SkillCatalogEntry | None:
        """Return the entry with the given ``id`` or ``None``."""
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def search(self, query: str) -> list[SkillCatalogEntry]:
        """Substring match on id / name / description / tags."""
        q = query.lower().strip()
        if not q:
            return list(self.entries)
        results: list[SkillCatalogEntry] = []
        for entry in self.entries:
            haystack_parts = [entry.id, entry.name, entry.description, *entry.tags]
            haystack = " ".join(haystack_parts).lower()
            if q in haystack:
                results.append(entry)
        return results

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        out: dict[str, Any] = {
            "version": self.version,
            "generated_at": self.generated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }
        if self.signer_pubkey is not None:
            out["signer_pubkey"] = self.signer_pubkey
        return out


def _ensure_str(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    """Validate that ``value`` is a string (optionally non-empty)."""
    if not isinstance(value, str):
        raise SkillCatalogValidationError(
            f"field {field_name!r} must be a string, got {type(value).__name__}",
        )
    if not allow_empty and not value:
        raise SkillCatalogValidationError(f"field {field_name!r} must be non-empty")
    return value


def _ensure_bool(value: Any, field_name: str) -> bool:
    """Validate that ``value`` is a bool."""
    if not isinstance(value, bool):
        raise SkillCatalogValidationError(
            f"field {field_name!r} must be a bool, got {type(value).__name__}",
        )
    return value


def _ensure_str_list(value: Any, field_name: str) -> tuple[str, ...]:
    """Validate a list of strings (empty list allowed)."""
    if not isinstance(value, list):
        raise SkillCatalogValidationError(
            f"field {field_name!r} must be a list, got {type(value).__name__}",
        )
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise SkillCatalogValidationError(
                f"field {field_name!r}[{index}] must be a non-empty string",
            )
        out.append(item)
    return tuple(out)


def _validate_source(raw: Any, field_path: str) -> SkillSourceSpec:
    """Validate a source descriptor dict."""
    if not isinstance(raw, dict):
        raise SkillCatalogValidationError(
            f"{field_path} must be an object, got {type(raw).__name__}",
        )

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _VALID_SOURCE_KINDS:
        raise SkillCatalogValidationError(
            f"{field_path}.kind must be one of {sorted(_VALID_SOURCE_KINDS)}, got {kind!r}",
        )

    required = _SOURCE_REQUIRED_BY_KIND[kind]
    optional = _SOURCE_OPTIONAL_BY_KIND[kind]
    allowed = required | optional | {"kind"}

    keys = set(raw.keys())
    missing = required - keys
    if missing:
        raise SkillCatalogValidationError(
            f"{field_path} missing required field(s) for kind={kind!r}: {sorted(missing)}",
        )
    unknown = keys - allowed
    if unknown:
        raise SkillCatalogValidationError(
            f"{field_path} has unknown field(s) for kind={kind!r}: {sorted(unknown)}",
        )

    if kind == "github":
        return SkillSourceSpec(
            kind=kind,
            repo=_ensure_str(raw["repo"], f"{field_path}.repo"),
            tag=_ensure_str(raw.get("tag", "latest"), f"{field_path}.tag", allow_empty=False),
            asset=_ensure_str(raw["asset"], f"{field_path}.asset") if "asset" in raw else None,
        )
    if kind == "git":
        return SkillSourceSpec(
            kind=kind,
            url=_ensure_str(raw["url"], f"{field_path}.url"),
            ref=_ensure_str(raw.get("ref", "HEAD"), f"{field_path}.ref"),
        )
    if kind == "npm":
        return SkillSourceSpec(
            kind=kind,
            package=_ensure_str(raw["package"], f"{field_path}.package"),
            version=_ensure_str(raw.get("version", "latest"), f"{field_path}.version"),
        )
    # file / directory
    return SkillSourceSpec(
        kind=kind,
        path=_ensure_str(raw["path"], f"{field_path}.path"),
    )


def _validate_entry(raw: Any, index: int) -> SkillCatalogEntry:
    """Validate a single entry dict."""
    if not isinstance(raw, dict):
        raise SkillCatalogValidationError(
            f"entries[{index}] must be an object, got {type(raw).__name__}",
        )

    keys = set(raw.keys())
    missing = _ENTRY_REQUIRED - keys
    if missing:
        raise SkillCatalogValidationError(
            f"entries[{index}] missing required field(s): {sorted(missing)}",
        )
    unknown = keys - _ENTRY_ALLOWED
    if unknown:
        raise SkillCatalogValidationError(
            f"entries[{index}] has unknown field(s): {sorted(unknown)}",
        )

    entry_id = _ensure_str(raw["id"], f"entries[{index}].id")
    if not _ID_PATTERN.match(entry_id):
        raise SkillCatalogValidationError(
            f"entries[{index}].id {entry_id!r} does not match pattern {_ID_PATTERN.pattern!r}",
        )

    content_digest = _ensure_str(raw["content_digest"], f"entries[{index}].content_digest")
    if not re.fullmatch(r"[0-9a-f]{64}", content_digest):
        raise SkillCatalogValidationError(
            f"entries[{index}].content_digest must be a 64-char lowercase hex SHA-256",
        )

    signature = None
    if "signature" in raw:
        signature = _ensure_str(raw["signature"], f"entries[{index}].signature")

    tags: tuple[str, ...] = ()
    if "tags" in raw:
        tags = _ensure_str_list(raw["tags"], f"entries[{index}].tags")

    homepage = _ensure_str(raw.get("homepage", ""), f"entries[{index}].homepage", allow_empty=True)
    verified = _ensure_bool(raw.get("verified", False), f"entries[{index}].verified")

    return SkillCatalogEntry(
        id=entry_id,
        name=_ensure_str(raw["name"], f"entries[{index}].name"),
        version=_ensure_str(raw["version"], f"entries[{index}].version"),
        description=_ensure_str(raw["description"], f"entries[{index}].description"),
        source=_validate_source(raw["source"], f"entries[{index}].source"),
        content_digest=content_digest,
        signature=signature,
        homepage=homepage,
        tags=tags,
        verified=verified,
    )


def validate_catalog(payload: Any) -> SkillCatalog:
    """Validate a parsed JSON payload and return a :class:`SkillCatalog`.

    Args:
        payload: Parsed JSON object.

    Returns:
        A :class:`SkillCatalog` value object.

    Raises:
        SkillCatalogValidationError: If any required field is missing,
            any unknown field is present, or any value is the wrong type.
    """
    if not isinstance(payload, dict):
        raise SkillCatalogValidationError(
            f"top-level payload must be an object, got {type(payload).__name__}",
        )

    keys = set(payload.keys())
    missing = _CATALOG_REQUIRED - keys
    if missing:
        raise SkillCatalogValidationError(
            f"catalog missing required field(s): {sorted(missing)}",
        )
    unknown = keys - _CATALOG_ALLOWED
    if unknown:
        raise SkillCatalogValidationError(
            f"catalog has unknown field(s): {sorted(unknown)}",
        )

    version_raw = payload["version"]
    if not isinstance(version_raw, int) or isinstance(version_raw, bool):
        raise SkillCatalogValidationError(
            f"field 'version' must be an integer, got {type(version_raw).__name__}",
        )
    if version_raw != _SUPPORTED_SCHEMA_VERSION:
        raise SkillCatalogValidationError(
            f"unsupported skills catalog schema version {version_raw!r}; "
            f"this client expects {_SUPPORTED_SCHEMA_VERSION}",
        )

    generated_at = _ensure_str(payload["generated_at"], "generated_at")

    signer_pubkey: str | None = None
    if "signer_pubkey" in payload:
        signer_pubkey = _ensure_str(payload["signer_pubkey"], "signer_pubkey")

    entries_raw = payload["entries"]
    if not isinstance(entries_raw, list):
        raise SkillCatalogValidationError(
            f"field 'entries' must be a list, got {type(entries_raw).__name__}",
        )

    entries: list[SkillCatalogEntry] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(entries_raw):
        entry = _validate_entry(raw, index)
        if entry.id in seen_ids:
            raise SkillCatalogValidationError(
                f"entries[{index}] duplicates id {entry.id!r}",
            )
        seen_ids.add(entry.id)
        entries.append(entry)

    return SkillCatalog(
        version=version_raw,
        generated_at=generated_at,
        entries=tuple(entries),
        signer_pubkey=signer_pubkey,
    )


__all__ = [
    "SkillCatalog",
    "SkillCatalogEntry",
    "SkillCatalogValidationError",
    "SkillSourceSpec",
    "validate_catalog",
]
