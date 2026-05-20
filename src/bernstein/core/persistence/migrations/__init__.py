"""Versioned migrations for on-disk Bernstein state.

This package versions everything Bernstein persists under ``.sdd/`` -- the
``.sdd/runtime/`` tree, SQLite stores, and JSON state files -- behind a
single ``.sdd/.schema_version`` stamp.

Why a migrations package instead of inline compat branches:

Compat code historically lived inline in the modules that read the data.
Each call site that touched a session record, a backlog entry, or a SQLite
table carried a small "if this looks like an old shape, upgrade it" branch.
Those branches accumulate, never get deleted, and silently break when shapes
change again. A single versioned migration module per shape change -- run
once at startup, ordered, idempotent -- removes the per-call compat branches
and gives operators one answer to "what version is this install at".

Authoring a migration:

Add a module ``vNNN_<description>.py`` next to this file. Each module exposes
a module-level :class:`Migration` instance named ``MIGRATION`` (or, for
convenience, top-level ``VERSION``, ``DESCRIPTION``, ``apply`` and ``down``
callables that the loader wraps). ``apply(state_dir)`` performs the forward
upgrade and must be idempotent; ``down(state_dir)`` rolls it back and may be
a stub for forward-only migrations.

The first migration (:mod:`v001_baseline`) encodes the current shape: its
``apply`` is a no-op that simply records the baseline. Subsequent shape
changes ship as new modules with higher version numbers.

Public surface:

- :func:`discover_migrations` -- ordered list of registered migrations.
- :func:`read_schema_version` / :func:`write_schema_version` -- stamp I/O.
- :func:`pending_migrations` / :func:`applied_migrations` -- doctor surface.
- :func:`migrate` -- apply all unrun migrations in order; returns a
  :class:`MigrationReport`.
- :data:`EXIT_OK`, :data:`EXIT_APPLIED`, :data:`EXIT_NOOP`,
  :data:`EXIT_FUTURE_VERSION` -- documented exit codes for the runner.
"""

from __future__ import annotations

from bernstein.core.persistence.migrations.runner import (
    EXIT_APPLIED,
    EXIT_FUTURE_VERSION,
    EXIT_NOOP,
    EXIT_OK,
    SCHEMA_VERSION_FILENAME,
    FutureSchemaVersionError,
    Migration,
    MigrationReport,
    applied_migrations,
    discover_migrations,
    latest_version,
    migrate,
    pending_migrations,
    read_schema_version,
    write_schema_version,
)

__all__ = [
    "EXIT_APPLIED",
    "EXIT_FUTURE_VERSION",
    "EXIT_NOOP",
    "EXIT_OK",
    "SCHEMA_VERSION_FILENAME",
    "FutureSchemaVersionError",
    "Migration",
    "MigrationReport",
    "applied_migrations",
    "discover_migrations",
    "latest_version",
    "migrate",
    "pending_migrations",
    "read_schema_version",
    "write_schema_version",
]
