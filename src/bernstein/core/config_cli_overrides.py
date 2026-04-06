"""CFG-012: Config override via CLI flags for all key settings.

Maps CLI flags like --max-agents, --budget, --model to config overrides
that are applied at the highest precedence layer.  Parses flag strings
into typed config values and validates them against the config schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CLIOverride:
    """A single CLI flag override.

    Attributes:
        flag: The CLI flag name (e.g. "--max-agents").
        config_key: Corresponding config key (e.g. "max_agents").
        value: The parsed typed value.
        raw: The raw string value from the command line.
    """

    flag: str
    config_key: str
    value: Any
    raw: str


@dataclass(frozen=True, slots=True)
class CLIFlagSpec:
    """Specification for a supported CLI override flag.

    Attributes:
        flag: CLI flag name (e.g. "--max-agents").
        config_key: Corresponding config key.
        value_type: Expected Python type (str, int, float, bool).
        description: Help text for the flag.
        short: Short flag alias (e.g. "-n").
    """

    flag: str
    config_key: str
    value_type: type
    description: str = ""
    short: str = ""


# ---------------------------------------------------------------------------
# Supported CLI flags
# ---------------------------------------------------------------------------

SUPPORTED_FLAGS: tuple[CLIFlagSpec, ...] = (
    CLIFlagSpec("--max-agents", "max_agents", int, "Maximum concurrent agents", "-n"),
    CLIFlagSpec("--budget", "budget", str, "Spending cap (e.g. '$20' or '20')", "-b"),
    CLIFlagSpec("--model", "model", str, "Model override (e.g. 'opus', 'sonnet')"),
    CLIFlagSpec("--cli", "cli", str, "CLI agent backend (claude, codex, gemini, auto)"),
    CLIFlagSpec("--team", "team", str, "Role team ('auto' or comma-separated roles)"),
    CLIFlagSpec("--merge-strategy", "merge_strategy", str, "How agent work reaches main (pr or direct)"),
    CLIFlagSpec("--timeout", "timeout", int, "Agent timeout in seconds", "-t"),
    CLIFlagSpec("--log-level", "log_level", str, "Log level (DEBUG, INFO, WARNING, ERROR)"),
    CLIFlagSpec("--no-evolution", "evolution_enabled", bool, "Disable self-evolution"),
    CLIFlagSpec("--no-decompose", "auto_decompose", bool, "Disable auto task decomposition"),
    CLIFlagSpec("--auto-merge", "auto_merge", bool, "Enable auto-merge of PRs"),
    CLIFlagSpec("--max-cost-per-agent", "max_cost_per_agent", float, "Per-agent cost cap in USD"),
    CLIFlagSpec("--internal-llm-provider", "internal_llm_provider", str, "LLM provider for planning"),
    CLIFlagSpec("--internal-llm-model", "internal_llm_model", str, "Model for internal LLM calls"),
)

# Build lookup dicts for fast access.
_FLAG_TO_SPEC: dict[str, CLIFlagSpec] = {}
for _spec in SUPPORTED_FLAGS:
    _FLAG_TO_SPEC[_spec.flag] = _spec
    if _spec.short:
        _FLAG_TO_SPEC[_spec.short] = _spec


def _coerce_value(spec: CLIFlagSpec, raw: str) -> Any:
    """Coerce a raw CLI string value to the expected type.

    Args:
        spec: Flag specification with expected type.
        raw: Raw string from command line.

    Returns:
        Typed value.

    Raises:
        ValueError: If the value cannot be coerced.
    """
    if spec.value_type is bool:
        # Boolean flags: --no-evolution means False, --auto-merge means True.
        if spec.flag.startswith("--no-"):
            return raw.lower() not in ("1", "true", "yes") if raw else False
        return raw.lower() in ("1", "true", "yes") if raw else True

    if spec.value_type is int:
        return int(raw)

    if spec.value_type is float:
        return float(raw)

    return raw


def parse_cli_overrides(flags: dict[str, str]) -> list[CLIOverride]:
    """Parse CLI flag strings into typed config overrides.

    Args:
        flags: Dict mapping flag names to raw string values.
            Example: {"--max-agents": "4", "--budget": "$20"}

    Returns:
        List of parsed CLI overrides.

    Raises:
        ValueError: If a flag is unknown or its value cannot be parsed.
    """
    overrides: list[CLIOverride] = []

    for flag, raw in flags.items():
        spec = _FLAG_TO_SPEC.get(flag)
        if spec is None:
            raise ValueError(f"Unknown CLI flag: {flag}")

        try:
            value = _coerce_value(spec, raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid value for {flag}: {raw!r} ({exc})") from exc

        overrides.append(
            CLIOverride(
                flag=flag,
                config_key=spec.config_key,
                value=value,
                raw=raw,
            )
        )

    return overrides


def apply_overrides(
    config: dict[str, Any],
    overrides: list[CLIOverride],
) -> dict[str, Any]:
    """Apply CLI overrides to a config dict.

    Creates a new dict with overrides applied (does not mutate the input).

    Args:
        config: Base config dict.
        overrides: Parsed CLI overrides to apply.

    Returns:
        New config dict with overrides applied.
    """
    result = dict(config)
    for override in overrides:
        result[override.config_key] = override.value
        logger.debug(
            "CLI override: %s = %r (from %s)",
            override.config_key,
            override.value,
            override.flag,
        )
    return result


@dataclass
class CLIOverrideManager:
    """Manages CLI flag parsing and application.

    Attributes:
        overrides: Parsed CLI overrides.
    """

    overrides: list[CLIOverride] = field(default_factory=list)

    def parse(self, flags: dict[str, str]) -> None:
        """Parse CLI flags and store the overrides.

        Args:
            flags: Dict mapping flag names to raw string values.
        """
        self.overrides = parse_cli_overrides(flags)

    def apply(self, config: dict[str, Any]) -> dict[str, Any]:
        """Apply stored overrides to a config dict.

        Args:
            config: Base config dict.

        Returns:
            New config dict with overrides applied.
        """
        return apply_overrides(config, self.overrides)

    def as_dict(self) -> dict[str, Any]:
        """Return overrides as a flat config dict.

        Returns:
            Dict mapping config keys to override values.
        """
        return {o.config_key: o.value for o in self.overrides}

    @staticmethod
    def supported_flags() -> list[dict[str, str]]:
        """Return documentation for all supported flags.

        Returns:
            List of flag info dicts.
        """
        return [
            {
                "flag": s.flag,
                "short": s.short,
                "config_key": s.config_key,
                "type": s.value_type.__name__,
                "description": s.description,
            }
            for s in SUPPORTED_FLAGS
        ]
