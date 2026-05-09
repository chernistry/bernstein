"""Convention loader for a team-hub repository (KF-7 first slice).

A team hub on disk is just a directory tree::

    <hub-root>/
        team-hub.yaml          # required manifest (see team_hub_manifest.py)
        team/
            agents/<name>/      # role / agent templates
            skills/<name>/      # skill packs (SKILL.md inside)
            rules/<name>.md     # plain-text rules consumed by the planner

The loader reads the manifest, resolves every entry it ``ships`` against
the on-disk layout, and surfaces the resolved entries to downstream
resolvers. It is intentionally read-only and side-effect-free: clone /
pull and resolution-path merging live in later slices, so this module can
be unit-tested against a fixture directory without touching git.

Failure modes:

- A missing hub root, missing ``team-hub.yaml``, or missing ``team/`` dir
  is treated as "no hub installed" and yields ``None``. This is the
  no-op behaviour the parent ticket calls out for "graceful degradation
  when the network is down".
- A malformed manifest, a bucket entry that escapes the hub root, or an
  entry that points at a non-existent path raises a hard error so the
  operator knows the hub is broken before it silently disappears from
  the resolved graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bernstein.core.plugins_core.team_hub_manifest import (
    TeamHubManifest,
    TeamHubManifestError,
    parse_team_hub_yaml,
)

if TYPE_CHECKING:
    from pathlib import Path

# Convention path inside a hub repo. Keep these in one place so the loader,
# the future ``bernstein hub init`` CLI, and any docs/examples agree.
_TEAM_DIR_NAME = "team"
_MANIFEST_FILENAME = "team-hub.yaml"


@dataclass(frozen=True)
class TeamHubEntry:
    """A single resolved entry inside a hub.

    Attributes:
        bucket: ``"agents"``, ``"skills"``, or ``"rules"``.
        relative: POSIX-style path as written in the manifest.
        absolute: Resolved :class:`pathlib.Path` on the local filesystem.
        is_directory: ``True`` for agent / skill packs (directories),
            ``False`` for single-file rules.
    """

    bucket: str
    relative: str
    absolute: Path
    is_directory: bool


@dataclass(frozen=True)
class LoadedTeamHub:
    """Aggregate the loader hands back to callers.

    Attributes:
        root: The hub directory.
        manifest: The parsed, validated :class:`TeamHubManifest`.
        entries: Every resolved entry, ordered ``agents → skills → rules``
            and stable within each bucket so callers can build
            deterministic indexes.
    """

    root: Path
    manifest: TeamHubManifest
    entries: tuple[TeamHubEntry, ...]

    def by_bucket(self, bucket: str) -> tuple[TeamHubEntry, ...]:
        """Return only the entries published under ``bucket``."""
        return tuple(entry for entry in self.entries if entry.bucket == bucket)


class TeamHubLoaderError(RuntimeError):
    """Raised when a manifest entry cannot be resolved on the filesystem."""

    def __init__(self, root: Path, detail: str) -> None:
        super().__init__(f"{root}: {detail}")
        self.root = root
        self.detail = detail


def load_team_hub(root: Path) -> LoadedTeamHub | None:
    """Load a hub from ``root`` if one is present.

    A hub is "present" when ``root`` exists, contains a readable
    ``team-hub.yaml``, and has a ``team/`` directory. When any of those
    are missing the function returns ``None`` so callers can compose this
    with "no hub installed" without forcing them into try/except chains.

    Args:
        root: Local path to the hub checkout.

    Returns:
        A :class:`LoadedTeamHub` when the hub is present and well-formed,
        otherwise ``None``.

    Raises:
        TeamHubManifestError: When ``team-hub.yaml`` is present but
            unreadable, malformed, or fails schema validation.
        TeamHubLoaderError: When the manifest is valid but a ``ships``
            entry escapes the hub root or points at a non-existent path.
    """
    if not root.is_dir():
        return None

    manifest_path = root / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None

    team_dir = root / _TEAM_DIR_NAME
    if not team_dir.is_dir():
        # The convention is explicit about *where* shipped artefacts live.
        # Treat the manifest-only case as "hub not yet populated" rather
        # than as a hard error — a freshly-initialised hub repo will pass
        # through this branch on its first commit.
        return None

    manifest = parse_team_hub_yaml(manifest_path)
    entries = _resolve_entries(root, manifest)
    return LoadedTeamHub(root=root, manifest=manifest, entries=entries)


def _resolve_entries(root: Path, manifest: TeamHubManifest) -> tuple[TeamHubEntry, ...]:
    """Resolve every ``ships`` entry against the hub root.

    Args:
        root: Hub root directory.
        manifest: The validated manifest.

    Returns:
        Tuple of entries in canonical ``agents → skills → rules`` order;
        within each bucket, in the order the manifest declared them.

    Raises:
        TeamHubLoaderError: When an entry would resolve outside ``root``
            (defence in depth — :class:`TeamHubShips` already filters
            ``..`` in the schema) or when the target path does not exist.
    """
    real_root = root.resolve()
    out: list[TeamHubEntry] = []

    # Order is fixed (not alphabetical on the bucket name) so callers can
    # rely on a stable iteration order without sorting again. Agents come
    # first because role resolution happens before skill resolution.
    for bucket, declared in (
        ("agents", manifest.ships.agents),
        ("skills", manifest.ships.skills),
        ("rules", manifest.ships.rules),
    ):
        for relative in declared:
            absolute = (root / relative).resolve()
            try:
                absolute.relative_to(real_root)
            except ValueError as exc:
                raise TeamHubLoaderError(
                    root,
                    f"entry {relative!r} resolves outside hub root: {absolute}",
                ) from exc
            if not absolute.exists():
                raise TeamHubLoaderError(
                    root,
                    f"entry {relative!r} points at missing path: {absolute}",
                )
            out.append(
                TeamHubEntry(
                    bucket=bucket,
                    relative=relative,
                    absolute=absolute,
                    is_directory=absolute.is_dir(),
                )
            )

    return tuple(out)


__all__ = [
    "LoadedTeamHub",
    "TeamHubEntry",
    "TeamHubLoaderError",
    "TeamHubManifestError",
    "load_team_hub",
]
