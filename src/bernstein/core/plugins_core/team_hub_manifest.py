"""Pydantic schema for a team-hub manifest (KF-7 first slice).

A *team hub* is a git repository that ships shared agents, skills, and
rules so a whole team works against one source of truth — Continue Hub /
Cursor Teams parity, but using ``git`` as the substrate (no SaaS dial-in,
no second auth surface, normal PR review).

This module defines the smallest contract every hub must satisfy:

- a top-level ``team-hub.yaml`` manifest enumerating what the hub ships
- each ``ships.{agents,skills,rules}`` entry is a relative path inside the
  hub repo, validated against directory traversal
- ``compatibility.bernstein`` is a PEP-440-style requirement specifier so
  the loader can refuse to merge a hub that targets a future Bernstein

Later slices will:

- clone / pull hub repos (``hub_loader.py`` Step 1 in the parent ticket)
- merge resolved entries into the role / skill / rule resolution paths
- expose a ``bernstein hub`` CLI

The schema lives here so those slices can import a single source of truth
instead of re-deriving the manifest shape ad hoc.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

if TYPE_CHECKING:
    from pathlib import Path

# Lowercase slug, identical to ``SkillManifest.name`` so a future "publish
# this skill from a hub" flow can reuse the same identifier without a
# normalisation hop.
_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9-]*$")

# Hard cap on manifest size. A real ``team-hub.yaml`` is well under 4 KiB;
# anything larger is corrupt or hostile. The cap is applied to the raw file
# before YAML parsing so we never feed an unbounded string to PyYAML.
_MAX_MANIFEST_BYTES = 64 * 1024

# Conservative shipping-bucket allowlist. Adding a new bucket is a deliberate
# schema change — silently accepting unknown buckets would let a hub author
# ship arbitrary directories that the loader has no merge policy for.
_ALLOWED_BUCKETS: frozenset[str] = frozenset({"agents", "skills", "rules"})


class TeamHubManifestError(ValueError):
    """Raised when ``team-hub.yaml`` is missing, malformed, or invalid.

    Always carries the originating path so operators can locate the file
    without needing to re-derive it from the traceback.
    """

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


class TeamHubShips(BaseModel):
    """The buckets a hub publishes.

    Each list contains POSIX-style relative paths (``agents/foo/`` etc.)
    that must resolve inside the hub repo. ``..`` and absolute paths are
    rejected at validation time so a hostile manifest cannot escape the
    hub root once the loader starts merging files.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    agents: list[str] = Field(default_factory=list[str])
    skills: list[str] = Field(default_factory=list[str])
    rules: list[str] = Field(default_factory=list[str])

    @field_validator("agents", "skills", "rules")
    @classmethod
    def _validate_paths(cls, value: list[str]) -> list[str]:
        """Reject absolute paths and ``..`` traversal, normalise trailing slash."""
        cleaned: list[str] = []
        for raw in value:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"path entry {raw!r} must be a non-empty string")
            if raw.startswith("/"):
                raise ValueError(f"path {raw!r} must be relative to the hub root")
            posix = PurePosixPath(raw)
            if posix.is_absolute() or any(part == ".." for part in posix.parts):
                raise ValueError(f"path {raw!r} must not escape the hub root")
            # Normalise; strip any trailing slash so equality checks downstream
            # work regardless of how authors spell directory paths.
            cleaned.append(str(posix).rstrip("/"))
        return cleaned


class TeamHubCompatibility(BaseModel):
    """Compatibility constraints declared by the hub.

    ``bernstein`` is a PEP-440 style version-specifier string (``>=1.10``,
    ``~=1.9``, etc.). The loader does not enforce semantics here; it merely
    ensures the field is a non-empty string so a future enforcement slice
    can drop in a single ``packaging.specifiers`` call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bernstein: str = Field(min_length=1, max_length=64)


class TeamHubManifest(BaseModel):
    """Strict-validated ``team-hub.yaml`` schema.

    Attributes:
        name: Lowercase slug identifying the hub (``acmecorp-shared``).
        version: Free-form version string published by the hub.
        ships: Buckets of relative paths the hub publishes.
        compatibility: Constraints the hub asserts against Bernstein.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    ships: TeamHubShips = Field(default_factory=TeamHubShips)
    compatibility: TeamHubCompatibility

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        """Match the lowercase-slug regex used by ``SkillManifest``."""
        if not _NAME_PATTERN.match(value):
            raise ValueError(
                f"name {value!r} must match regex ^[a-z][a-z0-9-]*$ "
                "(lowercase letters, digits, hyphens; must start with a letter)"
            )
        return value


def parse_team_hub_yaml(path: Path) -> TeamHubManifest:
    """Parse a ``team-hub.yaml`` file.

    Args:
        path: Path to the manifest file.

    Returns:
        Validated :class:`TeamHubManifest`.

    Raises:
        TeamHubManifestError: When the file is missing, exceeds the size
            cap, contains invalid YAML, has an unsupported top-level
            bucket, or fails Pydantic validation.
    """
    if not path.is_file():
        raise TeamHubManifestError(path, "team-hub.yaml does not exist")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TeamHubManifestError(path, f"cannot read file: {exc}") from exc

    if len(raw.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        raise TeamHubManifestError(
            path,
            f"manifest exceeds {_MAX_MANIFEST_BYTES} bytes — refusing to parse",
        )

    try:
        data: object = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TeamHubManifestError(path, f"invalid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise TeamHubManifestError(path, f"manifest must be a YAML mapping, got {type(data).__name__}")

    raw_data: dict[Any, Any] = cast("dict[Any, Any]", data)
    cleaned: dict[str, Any] = {}
    for key, value in raw_data.items():
        if not isinstance(key, str):
            raise TeamHubManifestError(path, f"key {key!r} must be a string")
        cleaned[key] = value

    # Surface unknown ``ships`` buckets with a clear error before Pydantic's
    # generic ``extra forbidden`` message kicks in.
    ships_value = cleaned.get("ships")
    if isinstance(ships_value, dict):
        ships_dict: dict[Any, Any] = cast("dict[Any, Any]", ships_value)
        bad = [k for k in ships_dict if k not in _ALLOWED_BUCKETS]
        if bad:
            raise TeamHubManifestError(
                path,
                f"unknown ships bucket(s) {bad!r}; allowed: {sorted(_ALLOWED_BUCKETS)}",
            )

    try:
        return TeamHubManifest.model_validate(cleaned)
    except ValidationError as exc:
        raise TeamHubManifestError(path, f"invalid manifest: {exc.errors()}") from exc


def validate_team_hub_dict(data: dict[str, Any]) -> TeamHubManifest:
    """Validate an in-memory mapping against the team-hub schema.

    Convenience entry point for tests and for callers that already have a
    parsed dict (e.g. a CLI wizard collecting fields interactively). The
    file-based :func:`parse_team_hub_yaml` is preferred for hub repos on
    disk because its error messages carry the originating path.

    Args:
        data: Candidate manifest as a plain ``dict``.

    Returns:
        Validated :class:`TeamHubManifest`.

    Raises:
        TeamHubManifestError: With ``path`` set to ``Path("<dict>")`` when
            the dict fails validation. Callers that have a real path should
            use :func:`parse_team_hub_yaml` instead.
    """
    from pathlib import Path as _Path

    placeholder = _Path("<dict>")

    ships_value = data.get("ships")
    if isinstance(ships_value, dict):
        ships_dict: dict[Any, Any] = cast("dict[Any, Any]", ships_value)
        bad = [k for k in ships_dict if k not in _ALLOWED_BUCKETS]
        if bad:
            raise TeamHubManifestError(
                placeholder,
                f"unknown ships bucket(s) {bad!r}; allowed: {sorted(_ALLOWED_BUCKETS)}",
            )

    try:
        return TeamHubManifest.model_validate(data)
    except ValidationError as exc:
        raise TeamHubManifestError(placeholder, f"invalid manifest: {exc.errors()}") from exc
