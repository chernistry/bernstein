"""CFG-010: Config validation for adapter-specific settings.

Validates adapter-specific configuration fields like claude max_turns,
codex flags, gemini parameters, etc.  Each adapter declares its own
validation rules, and the config validator applies them when the
corresponding CLI adapter is selected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdapterValidationError:
    """A single adapter-specific validation error.

    Attributes:
        adapter: Adapter name (e.g. "claude", "codex").
        field: Config field that failed validation.
        message: Human-readable error description.
        severity: "error" for blocking issues, "warning" for suggestions.
    """

    adapter: str
    field: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        """Serialize to a dict."""
        return {
            "adapter": self.adapter,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class AdapterFieldSpec:
    """Specification for one adapter config field.

    Attributes:
        name: Field name (e.g. "max_turns").
        expected_type: Python type the value should be.
        min_value: Minimum value for numeric fields (inclusive).
        max_value: Maximum value for numeric fields (inclusive).
        allowed_values: Set of allowed values (for enum-like fields).
        description: Human-readable field description.
    """

    name: str
    expected_type: type
    min_value: int | float | None = None
    max_value: int | float | None = None
    allowed_values: frozenset[Any] | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Adapter validation specs
# ---------------------------------------------------------------------------

_CLAUDE_FIELDS: tuple[AdapterFieldSpec, ...] = (
    AdapterFieldSpec(
        name="max_turns",
        expected_type=int,
        min_value=1,
        max_value=500,
        description="Maximum conversation turns before agent exits.",
    ),
    AdapterFieldSpec(
        name="model",
        expected_type=str,
        allowed_values=frozenset(
            {"opus", "sonnet", "haiku", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"}
        ),
        description="Model name or alias.",
    ),
    AdapterFieldSpec(
        name="output_format",
        expected_type=str,
        allowed_values=frozenset({"json", "text", "stream-json"}),
        description="Output format for Claude Code CLI.",
    ),
    AdapterFieldSpec(
        name="permission_mode",
        expected_type=str,
        allowed_values=frozenset({"default", "plan", "bypasstool"}),
        description="Permission approval mode.",
    ),
)

_CODEX_FIELDS: tuple[AdapterFieldSpec, ...] = (
    AdapterFieldSpec(
        name="approval_mode",
        expected_type=str,
        allowed_values=frozenset({"auto-edit", "suggest", "full-auto"}),
        description="Codex approval mode.",
    ),
    AdapterFieldSpec(
        name="model",
        expected_type=str,
        description="OpenAI model name.",
    ),
)

_GEMINI_FIELDS: tuple[AdapterFieldSpec, ...] = (
    AdapterFieldSpec(
        name="model",
        expected_type=str,
        description="Gemini model name.",
    ),
    AdapterFieldSpec(
        name="sandbox",
        expected_type=str,
        allowed_values=frozenset({"docker", "none"}),
        description="Sandbox mode for Gemini CLI.",
    ),
)

# Registry mapping adapter name -> field specs.
_ADAPTER_SPECS: dict[str, tuple[AdapterFieldSpec, ...]] = {
    "claude": _CLAUDE_FIELDS,
    "codex": _CODEX_FIELDS,
    "gemini": _GEMINI_FIELDS,
}


def _validate_field(
    adapter: str,
    spec: AdapterFieldSpec,
    value: Any,
) -> list[AdapterValidationError]:
    """Validate a single field against its spec.

    Args:
        adapter: Adapter name.
        spec: Field specification.
        value: Actual value to validate.

    Returns:
        List of validation errors (empty if valid).
    """
    errors: list[AdapterValidationError] = []

    # Type check
    if not isinstance(value, spec.expected_type):
        errors.append(
            AdapterValidationError(
                adapter=adapter,
                field=spec.name,
                message=(f"Expected {spec.expected_type.__name__}, got {type(value).__name__}: {value!r}"),
            )
        )
        return errors  # Skip further checks if type is wrong.

    # Range check for numeric types
    if spec.min_value is not None and isinstance(value, (int, float)) and value < spec.min_value:
        errors.append(
            AdapterValidationError(
                adapter=adapter,
                field=spec.name,
                message=f"Value {value} is below minimum {spec.min_value}",
            )
        )

    if spec.max_value is not None and isinstance(value, (int, float)) and value > spec.max_value:
        errors.append(
            AdapterValidationError(
                adapter=adapter,
                field=spec.name,
                message=f"Value {value} is above maximum {spec.max_value}",
            )
        )

    # Allowed values check
    if spec.allowed_values is not None and value not in spec.allowed_values:
        errors.append(
            AdapterValidationError(
                adapter=adapter,
                field=spec.name,
                message=(f"Value {value!r} not in allowed values: {sorted(spec.allowed_values)}"),
                severity="warning",
            )
        )

    return errors


@dataclass
class AdapterConfigValidator:
    """Validates adapter-specific configuration settings.

    Attributes:
        specs: Registry of per-adapter field specifications.
    """

    specs: dict[str, tuple[AdapterFieldSpec, ...]] = field(default_factory=lambda: dict(_ADAPTER_SPECS))

    def validate(
        self,
        adapter: str,
        config: dict[str, Any],
    ) -> list[AdapterValidationError]:
        """Validate adapter-specific config fields.

        Args:
            adapter: Adapter name (e.g. "claude", "codex").
            config: Config dict with adapter-specific fields.

        Returns:
            List of validation errors (empty if all valid).
        """
        adapter_specs = self.specs.get(adapter)
        if adapter_specs is None:
            return []

        errors: list[AdapterValidationError] = []
        for spec in adapter_specs:
            if spec.name in config:
                errors.extend(_validate_field(adapter, spec, config[spec.name]))
        return errors

    def validate_all(
        self,
        config: dict[str, Any],
    ) -> list[AdapterValidationError]:
        """Validate config for all known adapters.

        Checks role_config entries and top-level adapter fields.

        Args:
            config: Full bernstein.yaml config dict.

        Returns:
            Combined list of validation errors.
        """
        errors: list[AdapterValidationError] = []

        # Top-level CLI setting
        cli = config.get("cli", "auto")
        if cli != "auto" and cli in self.specs:
            errors.extend(self.validate(cli, config))

        # Per-role adapter config
        role_config = config.get("role_config", {})
        if isinstance(role_config, dict):
            for _role, role_settings in role_config.items():
                if isinstance(role_settings, dict):
                    role_cli = role_settings.get("cli")
                    if role_cli and role_cli in self.specs:
                        errors.extend(self.validate(role_cli, role_settings))

        return errors

    def supported_adapters(self) -> list[str]:
        """Return list of adapter names with validation specs.

        Returns:
            Sorted list of adapter names.
        """
        return sorted(self.specs.keys())

    def fields_for_adapter(self, adapter: str) -> list[dict[str, Any]]:
        """Return field documentation for an adapter.

        Args:
            adapter: Adapter name.

        Returns:
            List of field info dicts.
        """
        specs = self.specs.get(adapter, ())
        return [
            {
                "name": s.name,
                "type": s.expected_type.__name__,
                "min": s.min_value,
                "max": s.max_value,
                "allowed": sorted(s.allowed_values) if s.allowed_values else None,
                "description": s.description,
            }
            for s in specs
        ]
