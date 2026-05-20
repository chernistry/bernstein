"""Discovery and execution engine for on-disk state migrations.

The runner discovers ``vNNN_*`` modules in this package, orders them by their
integer version, and applies any whose version is greater than the currently
stamped ``.sdd/.schema_version``. Each forward step is run inside a
try/except so a failing migration leaves the stamp at the last cleanly
applied version rather than half-advancing it.

Idempotency is a property of each migration's ``apply`` implementation: the
runner additionally guarantees it never re-runs a migration whose version is
already at or below the stamp, so ``migrate`` on an up-to-date install is a
pure no-op observable via :data:`EXIT_NOOP`.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bernstein.core.persistence.atomic_write import write_atomic_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType

logger = logging.getLogger(__name__)

# File, relative to the ``.sdd`` state directory, that records the highest
# applied migration version. Plain text, single integer, no trailing newline
# required (we tolerate whitespace on read).
SCHEMA_VERSION_FILENAME = ".schema_version"

# Documented exit codes for the migration runner / doctor surface.
EXIT_OK = 0
"""Up to date and at least one migration exists; nothing to do (alias of NOOP at runtime)."""
EXIT_APPLIED = 0
"""One or more migrations were applied successfully."""
EXIT_NOOP = 0
"""Re-run with nothing pending; idempotent no-op."""
EXIT_FUTURE_VERSION = 3
"""The stamp records a version newer than any migration this build knows about."""

# Module-name pattern: ``v001_baseline``, ``v012_split_sessions`` ...
_MODULE_RE = re.compile(r"^v(\d+)_[a-z0-9_]+$")


class FutureSchemaVersionError(RuntimeError):
    """Raised when the on-disk stamp is newer than this build understands.

    Downgrading the binary below the version that wrote the state is
    unsupported: the older build cannot know how to read shapes a newer
    build produced. The runner refuses to touch such a state directory.
    """

    def __init__(self, stamped: int, known_latest: int) -> None:
        self.stamped = stamped
        self.known_latest = known_latest
        super().__init__(
            f"on-disk schema version {stamped} is newer than this build "
            f"supports (latest known migration is {known_latest}); "
            "upgrade Bernstein to read this state directory"
        )


@dataclass(frozen=True)
class Migration:
    """A single ordered, idempotent on-disk state migration.

    Attributes:
        version: Positive integer, unique across the package. Ordering key.
        description: Short human-readable summary (snake_case in the module
            name, free text here).
        apply: Forward upgrade. Must be idempotent: running it twice on the
            same state must equal running it once.
        down: Rollback. May be a no-op stub for forward-only migrations.
        module: Dotted module name the migration was loaded from.
    """

    version: int
    description: str
    apply: Callable[[Path], None]
    down: Callable[[Path], None]
    module: str = ""


@dataclass
class MigrationReport:
    """Outcome of a :func:`migrate` run.

    Attributes:
        from_version: Stamp value before the run.
        to_version: Stamp value after the run.
        applied: Versions applied during this run, in order.
        pending: Versions still pending after the run (always empty on
            success; populated only if a step raised).
        exit_code: One of the documented ``EXIT_*`` constants.
        error: Stringified failure if a migration raised, else ``None``.
    """

    from_version: int
    to_version: int
    applied: list[int] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)
    exit_code: int = EXIT_NOOP
    error: str | None = None


def _wrap_module(module: ModuleType, version: int, name: str) -> Migration:
    """Build a :class:`Migration` from a discovered module.

    Supports two authoring styles:

    1. A module-level ``MIGRATION`` instance (preferred for complex steps).
    2. Top-level ``apply`` / ``down`` callables plus optional ``DESCRIPTION``
       (concise for simple steps; ``down`` defaults to a no-op stub).
    """
    existing = getattr(module, "MIGRATION", None)
    if isinstance(existing, Migration):
        # Trust the module's own version/description but stamp the source.
        return Migration(
            version=existing.version,
            description=existing.description,
            apply=existing.apply,
            down=existing.down,
            module=module.__name__,
        )

    apply_fn = getattr(module, "apply", None)
    if not callable(apply_fn):
        raise RuntimeError(
            f"migration module {module.__name__!r} exposes neither a MIGRATION instance nor a callable apply()"
        )

    def _noop_down(_state_dir: Path) -> None:
        """Forward-only migration: rollback is a no-op."""

    down_fn = getattr(module, "down", None)
    if not callable(down_fn):
        down_fn = _noop_down

    declared_version = getattr(module, "VERSION", version)
    description = getattr(module, "DESCRIPTION", name.split("_", 1)[-1])
    return Migration(
        version=int(declared_version),
        description=str(description),
        apply=apply_fn,
        down=down_fn,
        module=module.__name__,
    )


def discover_migrations() -> list[Migration]:
    """Return all registered migrations ordered by ascending version.

    Raises:
        RuntimeError: if two modules declare the same version, or a module
            named like a migration cannot be loaded into a valid one.
    """
    package = importlib.import_module(__package__ or "bernstein.core.persistence.migrations")
    found: dict[int, Migration] = {}
    for info in pkgutil.iter_modules(package.__path__):
        match = _MODULE_RE.match(info.name)
        if not match:
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        migration = _wrap_module(module, int(match.group(1)), info.name)
        if migration.version in found:
            other = found[migration.version].module
            raise RuntimeError(
                f"duplicate migration version {migration.version}: {migration.module} collides with {other}"
            )
        found[migration.version] = migration
    return [found[v] for v in sorted(found)]


def latest_version(migrations: list[Migration] | None = None) -> int:
    """Return the highest known migration version, or ``0`` if none exist."""
    migs = migrations if migrations is not None else discover_migrations()
    return migs[-1].version if migs else 0


def _schema_version_path(state_dir: Path) -> Path:
    """Return the stamp file path under *state_dir*."""
    return state_dir / SCHEMA_VERSION_FILENAME


def read_schema_version(state_dir: Path) -> int:
    """Read the stamped schema version under *state_dir*.

    A missing stamp means a fresh (unmigrated) install and reads as ``0``.
    A present-but-unparseable stamp also reads as ``0`` so a corrupt stamp
    re-runs forward migrations rather than wedging startup; migrations are
    idempotent so re-running them is safe.
    """
    path = _schema_version_path(state_dir)
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (ValueError, OSError):
        logger.warning("unparseable schema version stamp at %s; treating as 0", path)
        return 0


def write_schema_version(state_dir: Path, version: int) -> None:
    """Atomically stamp *version* under *state_dir*.

    Creates *state_dir* if needed. Uses the crash-safe atomic write so a
    reader never observes a torn stamp.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    write_atomic_text(_schema_version_path(state_dir), f"{version}\n")


def pending_migrations(state_dir: Path) -> list[Migration]:
    """Return migrations whose version exceeds the current stamp, in order."""
    current = read_schema_version(state_dir)
    return [m for m in discover_migrations() if m.version > current]


def applied_migrations(state_dir: Path) -> list[Migration]:
    """Return migrations whose version is at or below the current stamp."""
    current = read_schema_version(state_dir)
    return [m for m in discover_migrations() if m.version <= current]


def migrate(state_dir: Path, *, target: int | None = None) -> MigrationReport:
    """Apply every unrun migration up to *target* in ascending order.

    On a fresh install (no stamp, no state) this still walks the migrations
    so the baseline ``apply`` runs and the stamp lands at the latest version.

    Args:
        state_dir: The ``.sdd`` state directory to migrate.
        target: Highest version to migrate to. Defaults to the latest known
            migration. Useful in tests to migrate to an intermediate version.

    Returns:
        A :class:`MigrationReport`. ``exit_code`` is ``EXIT_FUTURE_VERSION``
        and no work is done when the stamp is newer than this build knows.

    Raises:
        FutureSchemaVersionError: if the stamp is newer than the latest known
            migration. (Also reflected in the report for callers that prefer
            to branch on ``exit_code`` rather than catch.)
    """
    migrations = discover_migrations()
    known_latest = latest_version(migrations)
    current = read_schema_version(state_dir)

    if current > known_latest:
        raise FutureSchemaVersionError(current, known_latest)

    ceiling = known_latest if target is None else target
    todo = [m for m in migrations if current < m.version <= ceiling]

    report = MigrationReport(from_version=current, to_version=current)
    if not todo:
        report.exit_code = EXIT_NOOP
        return report

    for migration in todo:
        try:
            migration.apply(state_dir)
        except Exception as exc:
            report.error = f"{migration.module}: {exc}"
            report.pending = [m.version for m in todo if m.version > report.to_version]
            report.exit_code = 1
            logger.error("migration v%03d failed: %s", migration.version, exc)
            raise
        write_schema_version(state_dir, migration.version)
        report.to_version = migration.version
        report.applied.append(migration.version)
        logger.info("applied migration v%03d (%s)", migration.version, migration.description)

    report.exit_code = EXIT_APPLIED
    return report
