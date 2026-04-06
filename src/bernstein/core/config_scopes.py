"""CFG-007: Multi-scope config (project, user, workspace, env).

Layer config from multiple sources with clear precedence:
    1. DEFAULTS  -- built-in defaults (lowest)
    2. USER      -- ~/.bernstein/config.yaml
    3. PROJECT   -- <workdir>/bernstein.yaml
    4. WORKSPACE -- <workdir>/.bernstein/config.yaml
    5. ENV       -- BERNSTEIN_* environment variables (highest)

Higher-precedence scopes override lower ones for the same key.
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

logger = logging.getLogger(__name__)


@enum.unique
class ConfigScope(enum.Enum):
    """Configuration scope layers ordered by precedence (low to high)."""

    DEFAULTS = 0
    USER = 1
    PROJECT = 2
    WORKSPACE = 3
    ENV = 4


@dataclass(frozen=True, slots=True)
class ScopedValue:
    """A config value with its originating scope.

    Attributes:
        value: The resolved value.
        scope: Which scope provided this value.
        source_path: File path or "env" for environment variables.
    """

    value: Any
    scope: ConfigScope
    source_path: str


# Default config values (scope DEFAULTS).
_DEFAULTS: dict[str, Any] = {
    "cli": "auto",
    "max_agents": 6,
    "model": None,
    "team": "auto",
    "budget": None,
    "evolution_enabled": True,
    "auto_decompose": True,
    "merge_strategy": "pr",
    "auto_merge": True,
    "log_level": "INFO",
    "timeout": 1800,
}

# Mapping from BERNSTEIN_* env var names to config keys.
_ENV_MAP: dict[str, str] = {
    "BERNSTEIN_CLI": "cli",
    "BERNSTEIN_MAX_AGENTS": "max_agents",
    "BERNSTEIN_MODEL": "model",
    "BERNSTEIN_BUDGET": "budget",
    "BERNSTEIN_TEAM": "team",
    "BERNSTEIN_MERGE_STRATEGY": "merge_strategy",
    "BERNSTEIN_LOG_LEVEL": "log_level",
    "BERNSTEIN_TIMEOUT": "timeout",
}

# Keys that should be coerced to int when read from env.
_INT_KEYS: frozenset[str] = frozenset({"max_agents", "timeout"})

# Keys that should be coerced to bool when read from env.
_BOOL_KEYS: frozenset[str] = frozenset({"evolution_enabled", "auto_decompose", "auto_merge"})


def _coerce_env_value(key: str, raw: str) -> Any:
    """Coerce a raw env-var string to the expected Python type.

    Args:
        key: Config key name.
        raw: Raw string value from the environment.

    Returns:
        Coerced value (int, bool, or str).
    """
    if key in _INT_KEYS:
        return int(raw)
    if key in _BOOL_KEYS:
        return raw.lower() in ("1", "true", "yes")
    return raw


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict.

    Returns an empty dict if the file does not exist or is invalid.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed dict, or empty dict on error.
    """
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return cast("dict[str, Any]", loaded)
        return {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load config from %s: %s", path, exc)
        return {}


def _load_env_scope() -> dict[str, Any]:
    """Read BERNSTEIN_* environment variables and return matching config keys.

    Returns:
        Dict of config keys with values from the environment.
    """
    result: dict[str, Any] = {}
    for env_var, key in _ENV_MAP.items():
        raw = os.environ.get(env_var)
        if raw is not None:
            try:
                result[key] = _coerce_env_value(key, raw)
            except (ValueError, TypeError) as exc:
                logger.warning("Invalid env var %s=%r: %s", env_var, raw, exc)
    return result


@dataclass
class MultiScopeConfig:
    """Layered config resolution with provenance tracking.

    Loads config from defaults, user, project, workspace, and env scopes,
    then merges them with precedence (env > workspace > project > user > defaults).

    Attributes:
        workdir: Project root directory.
        layers: Per-scope raw config dicts.
        provenance: Per-key provenance tracking.
    """

    workdir: Path
    layers: dict[ConfigScope, dict[str, Any]] = field(default_factory=dict)
    provenance: dict[str, ScopedValue] = field(default_factory=dict)

    def load(self) -> None:
        """Load all config scopes and merge them.

        Reads config files from standard locations and environment variables.
        """
        user_path = Path.home() / ".bernstein" / "config.yaml"
        project_path = self.workdir / "bernstein.yaml"
        workspace_path = self.workdir / ".bernstein" / "config.yaml"

        self.layers = {
            ConfigScope.DEFAULTS: dict(_DEFAULTS),
            ConfigScope.USER: _load_yaml_file(user_path),
            ConfigScope.PROJECT: _load_yaml_file(project_path),
            ConfigScope.WORKSPACE: _load_yaml_file(workspace_path),
            ConfigScope.ENV: _load_env_scope(),
        }

        # Build provenance map: last scope to set a key wins.
        self.provenance.clear()
        source_paths = {
            ConfigScope.DEFAULTS: "<defaults>",
            ConfigScope.USER: str(user_path),
            ConfigScope.PROJECT: str(project_path),
            ConfigScope.WORKSPACE: str(workspace_path),
            ConfigScope.ENV: "<env>",
        }

        for scope in ConfigScope:
            layer = self.layers.get(scope, {})
            path = source_paths.get(scope, "<unknown>")
            for key, value in layer.items():
                self.provenance[key] = ScopedValue(
                    value=value,
                    scope=scope,
                    source_path=path,
                )

        logger.debug(
            "Multi-scope config loaded: %d keys from %d scopes",
            len(self.provenance),
            sum(1 for layer in self.layers.values() if layer),
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value with full scope resolution.

        Args:
            key: Config key to look up.
            default: Fallback if the key is not set in any scope.

        Returns:
            The highest-precedence value for the key.
        """
        entry = self.provenance.get(key)
        if entry is not None:
            return entry.value
        return default

    def get_scoped(self, key: str) -> ScopedValue | None:
        """Get a config value with its provenance metadata.

        Args:
            key: Config key to look up.

        Returns:
            ScopedValue with value and originating scope, or None.
        """
        return self.provenance.get(key)

    def effective(self) -> dict[str, Any]:
        """Return the merged effective config as a flat dict.

        Returns:
            Dict with all resolved config key-value pairs.
        """
        return {key: sv.value for key, sv in self.provenance.items()}

    def scope_summary(self) -> list[dict[str, Any]]:
        """Return a summary of all scopes and their key counts.

        Returns:
            List of scope info dicts for status display.
        """
        return [
            {
                "scope": scope.name,
                "precedence": scope.value,
                "key_count": len(self.layers.get(scope, {})),
            }
            for scope in ConfigScope
        ]

    def keys_from_scope(self, scope: ConfigScope) -> list[str]:
        """Return keys that are effectively provided by a specific scope.

        A key is "from" a scope only if that scope is the highest-precedence
        one that set it.

        Args:
            scope: The scope to filter by.

        Returns:
            List of config keys effectively provided by this scope.
        """
        return [key for key, sv in self.provenance.items() if sv.scope == scope]
