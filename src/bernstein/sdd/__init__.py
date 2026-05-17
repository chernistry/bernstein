"""SDD (Spec-Driven Development) tooling.

Public surface:

- :class:`bernstein.sdd.validator.ValidationReport`
- :func:`bernstein.sdd.validator.validate_ticket`
- :func:`bernstein.sdd.validator.load_schema`
"""

from __future__ import annotations

from bernstein.sdd.validator import (
    SchemaNotFoundError,
    ValidationIssue,
    ValidationReport,
    list_recommended_keys,
    load_schema,
    validate_ticket,
    validate_ticket_metadata,
)

__all__ = [
    "SchemaNotFoundError",
    "ValidationIssue",
    "ValidationReport",
    "list_recommended_keys",
    "load_schema",
    "validate_ticket",
    "validate_ticket_metadata",
]
