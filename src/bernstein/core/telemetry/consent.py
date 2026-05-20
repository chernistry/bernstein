"""Maintainer-share consent flag (foundation for RFC #1719).

This module implements the consent surface for an additive, opt-in path
to a community-shared maintainer endpoint. It is intentionally separate
from :mod:`bernstein.core.telemetry.config`: the existing ``enabled``
flag governs operator-controlled backends and stays unchanged. The
``share_with_maintainer`` flag here gates a different, additive code
path that may be wired up in a follow-up PR after RFC #1719 settles.

Invariants (tested):

* Default state is off (flag missing).
* The flag persists at
  ``$XDG_CONFIG_HOME/bernstein/telemetry.toml`` (falling back to
  ``~/.config/bernstein/telemetry.toml`` when ``XDG_CONFIG_HOME`` is
  unset, per the XDG Base Directory spec).
* Precedence (highest first): ``DO_NOT_TRACK=1`` > ``BERNSTEIN_TELEMETRY_SHARE``
  env > TOML file > default-off. ``BERNSTEIN_TELEMETRY_SHARE=0`` always wins
  over the TOML.
* No maintainer endpoint URL is baked into the package. This module
  reads and writes the flag only; consumers decide what to do with it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

_DO_NOT_TRACK: Final[str] = "DO_NOT_TRACK"
_SHARE_ENV: Final[str] = "BERNSTEIN_TELEMETRY_SHARE"
_XDG_CONFIG_HOME: Final[str] = "XDG_CONFIG_HOME"

_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", ""})
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


class ShareSource(StrEnum):
    """Which precedence layer decided the current share flag value."""

    DO_NOT_TRACK = "do_not_track"
    ENV = "env"
    FILE = "file"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ShareState:
    """Resolved share flag plus the signal that determined it."""

    enabled: bool
    source: ShareSource


def _xdg_config_home(env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """Return ``$XDG_CONFIG_HOME`` or its XDG-spec fallback."""
    real_env = env if env is not None else os.environ
    raw = real_env.get(_XDG_CONFIG_HOME)
    if raw:
        return Path(raw)
    base = home if home is not None else Path.home()
    return base / ".config"


def consent_file_path(
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the on-disk path of the consent TOML file."""
    return _xdg_config_home(env=env, home=home) / "bernstein" / "telemetry.toml"


def _load_toml(path: Path) -> dict[str, object]:
    """Load the TOML config or return an empty dict on any error."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _file_share(path: Path) -> bool | None:
    """Return the file's ``share_with_maintainer`` field, or ``None``."""
    if not path.exists():
        return None
    value = _load_toml(path).get("share_with_maintainer")
    if isinstance(value, bool):
        return value
    return None


def _parse_env(raw: str) -> bool:
    """Parse a string env-var value to a bool. Unknown values count as ``True``."""
    normalized = raw.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    return True


def resolve_share(
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> ShareState:
    """Resolve the maintainer-share flag with full precedence."""
    real_env = env if env is not None else os.environ.copy()

    if real_env.get(_DO_NOT_TRACK) == "1":
        return ShareState(enabled=False, source=ShareSource.DO_NOT_TRACK)

    raw = real_env.get(_SHARE_ENV)
    if raw is not None:
        return ShareState(enabled=_parse_env(raw), source=ShareSource.ENV)

    path = consent_file_path(env=real_env, home=home)
    file_choice = _file_share(path)
    if file_choice is not None:
        return ShareState(enabled=file_choice, source=ShareSource.FILE)

    return ShareState(enabled=False, source=ShareSource.DEFAULT)


def is_sharing_with_maintainer(
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> bool:
    """Shortcut for ``resolve_share(...).enabled``."""
    return resolve_share(env=env, home=home).enabled


def _serialize_toml(flag: bool) -> str:
    """Render a minimal TOML body. Avoids a tomli-w dependency."""
    value = "true" if flag else "false"
    return (
        f"# Bernstein telemetry consent. See docs/observability/telemetry-share.md.\nshare_with_maintainer = {value}\n"
    )


def write_share_flag(
    enabled: bool,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Persist ``share_with_maintainer`` to the consent TOML file."""
    path = consent_file_path(env=env, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_toml(enabled), encoding="utf-8")
    return path


def explain_share_source(source: ShareSource) -> str:
    """Return an operator-facing one-line description of ``source``."""
    if source is ShareSource.DO_NOT_TRACK:
        return "DO_NOT_TRACK env var (universal opt-out)"
    if source is ShareSource.ENV:
        return f"{_SHARE_ENV} env var"
    if source is ShareSource.FILE:
        return "consent file ($XDG_CONFIG_HOME/bernstein/telemetry.toml)"
    return "default (off)"


__all__ = [
    "ShareSource",
    "ShareState",
    "consent_file_path",
    "explain_share_source",
    "is_sharing_with_maintainer",
    "resolve_share",
    "write_share_flag",
]
