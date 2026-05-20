"""Schema-guard for the telemetry event taxonomy (RFC #1719 foundation).

Every field on every payload dataclass in
:mod:`bernstein.core.telemetry.events` must have an explicit redaction
decision recorded somewhere reviewable. This test enforces that by
introspecting the dataclass field sets and requiring each field to be
either:

* listed in ``SAFE_PRIMITIVE_FIELDS`` (a known-safe primitive on which a
  reviewer has signed off), or
* mapped in ``REDACTION_RULES`` to a written rationale describing how the
  field is sanitised at the boundary.

Adding a new field without recording a decision in one of those two places
fails the build, so the schema cannot silently grow a field that no one
has reviewed.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from bernstein.cli.commands.telemetry_cmd import (
    REDACTION_RULES,
    SAFE_PRIMITIVE_FIELDS,
)
from bernstein.core.telemetry import events as events_module

# The closed set of payload dataclasses that travel over the wire. Any new
# event must add its payload class here so the guard sees it.
PAYLOAD_CLASSES: tuple[type, ...] = (
    events_module.InstallCompletedPayload,
    events_module.FirstRunStartedPayload,
    events_module.FirstRunCompletedPayload,
    events_module.CommandInvokedPayload,
    events_module.DailyActivePayload,
)


def _payload_classes_in_module() -> list[type]:
    """Discover every frozen-dataclass payload defined in events.py.

    We deliberately discover by introspection so a new payload class added
    to ``events.py`` without updating ``PAYLOAD_CLASSES`` is caught.
    """
    found: list[type] = []
    for name in dir(events_module):
        obj = getattr(events_module, name)
        if not isinstance(obj, type):
            continue
        if not dataclasses.is_dataclass(obj):
            continue
        if name.endswith("Payload"):
            found.append(obj)
    return found


def test_payload_classes_match_known_set() -> None:
    """The schema-guard's known list must cover every Payload dataclass."""
    discovered = {cls.__name__ for cls in _payload_classes_in_module()}
    known = {cls.__name__ for cls in PAYLOAD_CLASSES}
    missing = discovered - known
    assert not missing, (
        "New event payload classes detected. Add them to PAYLOAD_CLASSES in "
        "tests/unit/telemetry/test_event_schema_guard.py and record a "
        f"redaction decision for their fields. Missing: {sorted(missing)}"
    )


@pytest.mark.parametrize("payload_cls", PAYLOAD_CLASSES, ids=lambda cls: cls.__name__)
def test_every_field_has_redaction_decision(payload_cls: type[Any]) -> None:
    """Every payload field must be listed as safe or carry a redaction entry."""
    field_names = [f.name for f in dataclasses.fields(payload_cls)]
    undecided: list[str] = [
        name for name in field_names if name not in SAFE_PRIMITIVE_FIELDS and name not in REDACTION_RULES
    ]
    assert not undecided, (
        f"Payload {payload_cls.__name__} has fields without a redaction "
        f"decision: {undecided}. Either add the field to "
        "SAFE_PRIMITIVE_FIELDS in src/bernstein/cli/commands/telemetry_cmd.py "
        "(literal allowlist of primitives that are safe to send) or add an "
        "explicit entry in REDACTION_RULES describing how the field is "
        "sanitised. See docs/observability/telemetry-share.md."
    )


def test_safe_fields_are_known_strings() -> None:
    """The allowlist must be a frozenset of non-empty strings."""
    assert isinstance(SAFE_PRIMITIVE_FIELDS, frozenset)
    assert SAFE_PRIMITIVE_FIELDS, "SAFE_PRIMITIVE_FIELDS must not be empty"
    assert all(isinstance(name, str) and name for name in SAFE_PRIMITIVE_FIELDS)


def test_redaction_rules_describe_real_categories() -> None:
    """Each redaction entry must come with a non-empty rationale."""
    assert REDACTION_RULES, "REDACTION_RULES must not be empty"
    for key, value in REDACTION_RULES.items():
        assert isinstance(key, str) and key
        assert isinstance(value, str) and value
