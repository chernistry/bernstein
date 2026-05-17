"""Opt-in resolution for operator observability.

Precedence (highest first):

1. ``DO_NOT_TRACK=1``                 - universal W3C opt-out signal.
2. ``BERNSTEIN_TELEMETRY={0|false|no|off|""}``
                                      - explicit Bernstein opt-out.
3. ``~/.bernstein/telemetry.yaml`` ``enabled: <bool>``
                                      - persisted operator choice.
4. Default                            - off.

The default is and must remain off in every code path.  No event may be
emitted, and no install id may be generated, until ``is_enabled`` returns
``True``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import yaml

# Sentinels for env var values that count as "off".  Comparison is done
# in lowercase, so callers may set BERNSTEIN_TELEMETRY=Off or =FALSE.
_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})

_DO_NOT_TRACK: Final[str] = "DO_NOT_TRACK"
_BERNSTEIN_TELEMETRY: Final[str] = "BERNSTEIN_TELEMETRY"


class OptInSource(StrEnum):
    """Which precedence layer determined the current state."""

    DO_NOT_TRACK = "do_not_track"
    ENV = "env"
    FILE = "file"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class OptInState:
    """The resolved state plus the signal that determined it."""

    enabled: bool
    source: OptInSource


def _config_dir(home: Path | None = None) -> Path:
    """Return ``~/.bernstein``.  ``home`` may be injected for tests."""
    base = home if home is not None else Path.home()
    return base / ".bernstein"


def config_file_path(home: Path | None = None) -> Path:
    """Return ``~/.bernstein/telemetry.yaml``."""
    return _config_dir(home) / "telemetry.yaml"


def first_run_marker_path(home: Path | None = None) -> Path:
    """Return ``~/.bernstein/first-run-acknowledged``."""
    return _config_dir(home) / "first-run-acknowledged"


def install_id_path(home: Path | None = None) -> Path:
    """Return ``~/.bernstein/install-id``."""
    return _config_dir(home) / "install-id"


def queue_path(home: Path | None = None) -> Path:
    """Return ``~/.bernstein/telemetry-queue.jsonl``."""
    return _config_dir(home) / "telemetry-queue.jsonl"


def _load_yaml_config(path: Path) -> dict[str, object]:
    """Load the config file or return an empty dict on any error."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data  # pyright: ignore[reportUnknownVariableType]


def _file_enabled(home: Path | None) -> bool | None:
    """Return the file's ``enabled`` field, or ``None`` if it is absent."""
    path = config_file_path(home)
    if not path.exists():
        return None
    cfg = _load_yaml_config(path)
    value = cfg.get("enabled")
    if isinstance(value, bool):
        return value
    return None


def resolve(
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> OptInState:
    """Resolve the opt-in state.  ``env``/``home`` may be injected for tests."""
    real_env = env if env is not None else dict(os.environ)

    if real_env.get(_DO_NOT_TRACK) == "1":
        return OptInState(enabled=False, source=OptInSource.DO_NOT_TRACK)

    raw = real_env.get(_BERNSTEIN_TELEMETRY)
    if raw is not None:
        normalized = raw.strip().lower()
        return OptInState(
            enabled=normalized not in _FALSE_VALUES,
            source=OptInSource.ENV,
        )

    file_choice = _file_enabled(home)
    if file_choice is not None:
        return OptInState(enabled=file_choice, source=OptInSource.FILE)

    return OptInState(enabled=False, source=OptInSource.DEFAULT)


def is_enabled(
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> bool:
    """Shortcut for ``resolve(...).enabled``."""
    return resolve(env=env, home=home).enabled


def write_enabled(
    enabled: bool,
    home: Path | None = None,
) -> Path:
    """Persist ``enabled`` to the config file.  Creates the dir if needed."""
    path = config_file_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"enabled": enabled}, fh, default_flow_style=False)
    return path


def mark_first_run_acknowledged(home: Path | None = None) -> Path:
    """Write the first-run-acknowledged marker."""
    path = first_run_marker_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("acknowledged\n", encoding="utf-8")
    return path


def is_first_run_acknowledged(home: Path | None = None) -> bool:
    """Return True if the operator has already seen the first-run notice."""
    return first_run_marker_path(home).exists()


__all__ = [
    "OptInSource",
    "OptInState",
    "config_file_path",
    "first_run_marker_path",
    "install_id_path",
    "is_enabled",
    "is_first_run_acknowledged",
    "mark_first_run_acknowledged",
    "queue_path",
    "resolve",
    "write_enabled",
]
