"""Migration test for adapter-output fixtures under the strict contract.

Every fixture under ``tests/fixtures/adapter_outputs/`` must parse cleanly
under strict mode. Fixtures that carried previously-tolerated extras live
under ``legacy/`` and must be rejected, proving the strict contract is what
moved them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bernstein.adapters.strict_schema import SchemaViolation
from bernstein.core.orchestration.phase_schemas import validate_phase_output
from bernstein.core.orchestration.refinement_schemas import Critique

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "adapter_outputs"


def _strict_fixtures() -> list[Path]:
    return sorted(p for p in _FIXTURE_ROOT.glob("*.json"))


def _legacy_fixtures() -> list[Path]:
    return sorted(p for p in (_FIXTURE_ROOT / "legacy").glob("*.json"))


def _parse_strict(payload: dict[str, Any]) -> None:
    """Parse *payload* under the strict contract, raising on any violation.

    Phase fixtures wrap their artefact under ``{"schema": ..., "payload":
    ...}``; critique fixtures are the bare payload.
    """
    schema = payload.get("schema")
    if isinstance(schema, str):
        errors = validate_phase_output(schema, payload["payload"])
        if errors:
            raise SchemaViolation(
                f"phase {schema} fixture rejected: {errors[0].field_path}: {errors[0].message}",
                fields=(errors[0].field_path,),
            )
        return
    # Bare critique payload.
    Critique.from_dict_strict(payload)


def test_fixture_root_is_present() -> None:
    assert _FIXTURE_ROOT.is_dir()
    assert _strict_fixtures(), "expected at least one strict-clean fixture"
    assert _legacy_fixtures(), "expected at least one legacy regression fixture"


@pytest.mark.parametrize("fixture", _strict_fixtures(), ids=lambda p: p.name)
def test_strict_fixture_parses_cleanly(fixture: Path) -> None:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    _parse_strict(payload)  # must not raise


@pytest.mark.parametrize("fixture", _legacy_fixtures(), ids=lambda p: p.name)
def test_legacy_fixture_rejected_under_strict_mode(fixture: Path) -> None:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    with pytest.raises(SchemaViolation):
        _parse_strict(payload)
