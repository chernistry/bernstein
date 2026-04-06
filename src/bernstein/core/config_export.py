"""CFG-011: Config import/export for team sharing.

Provides ``bernstein config export`` and ``bernstein config import``
functionality.  Export produces a portable YAML or JSON file with
secrets redacted.  Import merges or replaces the project config.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_REDACTED_MARKER = "<REDACTED>"
_SECRET_PATTERNS = ("secret", "token", "password", "key", "credential", "cert")

_EXPORT_META_KEY = "__bernstein_export__"


@dataclass(frozen=True, slots=True)
class ExportMeta:
    """Metadata embedded in exported config files.

    Attributes:
        exported_at: ISO 8601 timestamp of the export.
        source_path: Original config file path.
        checksum: SHA-256 of the non-redacted content (for integrity).
        format_version: Export format version for forward compatibility.
    """

    exported_at: str
    source_path: str
    checksum: str
    format_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict."""
        return {
            "exported_at": self.exported_at,
            "source_path": self.source_path,
            "checksum": self.checksum,
            "format_version": self.format_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportMeta:
        """Deserialize from a dict."""
        return cls(
            exported_at=str(data.get("exported_at", "")),
            source_path=str(data.get("source_path", "")),
            checksum=str(data.get("checksum", "")),
            format_version=int(data.get("format_version", 1)),
        )


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Result of a config import operation.

    Attributes:
        success: Whether the import succeeded.
        keys_imported: Number of keys imported.
        keys_skipped: Number of keys skipped (e.g. redacted values).
        warnings: List of warning messages.
        error: Error message if import failed.
    """

    success: bool
    keys_imported: int = 0
    keys_skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict."""
        return {
            "success": self.success,
            "keys_imported": self.keys_imported,
            "keys_skipped": self.keys_skipped,
            "warnings": self.warnings,
            "error": self.error,
        }


def _looks_secret(key: str) -> bool:
    """Check if a key name suggests it holds a secret value.

    Args:
        key: Key name to check.

    Returns:
        True if the key appears to be a secret.
    """
    lowered = key.lower()
    return any(pat in lowered for pat in _SECRET_PATTERNS)


def _redact_value(data: object, *, key_name: str = "") -> object:
    """Recursively redact secret values in a nested structure.

    Args:
        data: Nested dict/list/scalar.
        key_name: Current key name for secret detection.

    Returns:
        Structure with secret values replaced by the redacted marker.
    """
    if _looks_secret(key_name) and isinstance(data, str) and data:
        return _REDACTED_MARKER
    if isinstance(data, dict):
        raw = cast("dict[str, Any]", data)
        return {k: _redact_value(v, key_name=k) for k, v in raw.items()}
    if isinstance(data, list):
        raw_list = cast("list[Any]", data)
        return [_redact_value(item, key_name=key_name) for item in raw_list]
    return data


def _compute_checksum(data: dict[str, Any]) -> str:
    """Compute SHA-256 checksum of a config dict.

    Args:
        data: Config dict to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def export_config(
    config_path: Path,
    output_path: Path,
    *,
    fmt: Literal["yaml", "json"] = "yaml",
    redact_secrets: bool = True,
) -> Path:
    """Export a bernstein.yaml config file for team sharing.

    Args:
        config_path: Path to the source bernstein.yaml.
        output_path: Path to write the exported config.
        fmt: Output format ("yaml" or "json").
        redact_secrets: If True, replace secret values with a marker.

    Returns:
        Path to the written export file.

    Raises:
        FileNotFoundError: If the source config does not exist.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}

    config = cast("dict[str, Any]", raw)
    checksum = _compute_checksum(config)

    if redact_secrets:
        config = cast("dict[str, Any]", _redact_value(config))

    from datetime import UTC, datetime

    meta = ExportMeta(
        exported_at=datetime.now(tz=UTC).isoformat(),
        source_path=str(config_path),
        checksum=checksum,
    )
    config[_EXPORT_META_KEY] = meta.to_dict()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        output_path.write_text(
            json.dumps(config, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        output_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    logger.info("Config exported to %s (format=%s, redacted=%s)", output_path, fmt, redact_secrets)
    return output_path


def import_config(
    import_path: Path,
    target_path: Path,
    *,
    mode: Literal["replace", "merge"] = "merge",
) -> ImportResult:
    """Import a config file into the project.

    Args:
        import_path: Path to the exported config to import.
        target_path: Path to the target bernstein.yaml.
        mode: "replace" overwrites entirely, "merge" merges keys.

    Returns:
        ImportResult with details of what was imported.
    """
    if not import_path.exists():
        return ImportResult(success=False, error=f"Import file not found: {import_path}")

    try:
        raw_text = import_path.read_text(encoding="utf-8")
        # Try JSON first, fall back to YAML.
        try:
            imported = json.loads(raw_text)
        except json.JSONDecodeError:
            imported = yaml.safe_load(raw_text)

        if not isinstance(imported, dict):
            return ImportResult(success=False, error="Imported file must be a YAML/JSON mapping")

        imported_config = cast("dict[str, Any]", imported)
    except Exception as exc:
        return ImportResult(success=False, error=f"Failed to parse import file: {exc}")

    # Strip export metadata.
    imported_config.pop(_EXPORT_META_KEY, None)

    # Count and skip redacted values.
    warnings: list[str] = []
    skipped = 0

    def _count_redacted(data: object, path: str = "") -> object:
        nonlocal skipped
        if isinstance(data, str) and data == _REDACTED_MARKER:
            skipped += 1
            warnings.append(f"Skipped redacted value at '{path}'")
            return None  # Sentinel for removal
        if isinstance(data, dict):
            result: dict[str, Any] = {}
            for k, v in cast("dict[str, Any]", data).items():
                cleaned = _count_redacted(v, f"{path}.{k}" if path else k)
                if cleaned is not None or not isinstance(v, str) or v != _REDACTED_MARKER:
                    result[k] = cleaned if cleaned is not None else v
            return result
        if isinstance(data, list):
            return [_count_redacted(item, f"{path}[{i}]") for i, item in enumerate(cast("list[Any]", data))]
        return data

    cleaned = _count_redacted(imported_config)
    if isinstance(cleaned, dict):
        imported_config = cast("dict[str, Any]", cleaned)

    if mode == "replace":
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            yaml.dump(imported_config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        keys_imported = len(imported_config)
    else:
        # Merge: load existing and overlay imported keys.
        existing: dict[str, Any] = {}
        if target_path.exists():
            loaded = yaml.safe_load(target_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = cast("dict[str, Any]", loaded)

        existing.update(imported_config)
        keys_imported = len(imported_config)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    logger.info(
        "Config imported from %s (mode=%s, keys=%d, skipped=%d)",
        import_path,
        mode,
        keys_imported,
        skipped,
    )

    return ImportResult(
        success=True,
        keys_imported=keys_imported,
        keys_skipped=skipped,
        warnings=warnings,
    )
