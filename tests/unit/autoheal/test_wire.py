"""Unit tests for ``bernstein.core.autoheal.wire``.

The wire helper is the seam between auto-heal and the four
observability surfaces (decision log, calibration, lineage, audit).
These tests pin the contract:

* Every successful heal action emits exactly one decision-log row of
  kind ``autoheal_strategy`` with the bandit losers as alternatives.
* Every heal action emits exactly one calibration row carrying the
  predicted-prob / observed-outcome pair so the weekly Brier report
  can include autoheal.
* The audit ledger gets one row regardless of decision-log / calibration
  outcome (best-effort sidecars must never block the canonical write).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.autoheal import wire
from bernstein.core.autoheal.audit_log import iter_records
from bernstein.core.observability import decision_log as dl
from bernstein.eval import calibration


def _scope_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    decision_path = tmp_path / "decisions.jsonl"
    calibration_path = tmp_path / "calibration.jsonl"
    audit_path = tmp_path / "history.jsonl"
    monkeypatch.setenv(wire.ENV_DECISION_LOG_PATH, str(decision_path))
    monkeypatch.setenv(wire.ENV_CALIBRATION_LOG_PATH, str(calibration_path))
    monkeypatch.setenv(wire.ENV_AUDIT_LOG_PATH, str(audit_path))
    # Ensure the decision log writer is enabled.
    monkeypatch.setenv(dl.ENV_DISABLE, "1")
    return {
        "decision": decision_path,
        "calibration": calibration_path,
        "audit": audit_path,
    }


def test_record_heal_writes_to_all_three_sidecars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = _scope_paths(monkeypatch, tmp_path)
    result = wire.record_heal(
        run_id="run-1",
        head_sha="abc123",
        strategy="ruff-format",
        cls="safe",
        confidence=0.8,
        outcome="applied",
        cost_usd=0.0,
        llm_calls=0,
        patch_sha="patch-1",
        rationale="ruff-format applied",
        candidates=("ruff-format", "agents-md-sync"),
        sdd_dir=tmp_path,
    )
    assert result.decision_log_written is True
    assert result.calibration_written is True
    assert result.audit_written is True
    assert result.decision_id.startswith("dec-")

    # Decision log row carries the kind, chosen strategy, and the loser.
    dec_rows = list(dl.iter_records(paths["decision"]))
    assert len(dec_rows) == 1
    assert dec_rows[0].kind == "autoheal_strategy"
    assert dec_rows[0].chosen == "ruff-format"
    alt_ids = {a.id for a in dec_rows[0].alternatives}
    assert alt_ids == {"agents-md-sync"}

    # Calibration row carries the predicted prob and observed outcome.
    cal_rows = calibration.load_log(paths["calibration"])
    assert len(cal_rows) == 1
    assert cal_rows[0].decision_kind == "autoheal_strategy"
    assert cal_rows[0].predicted_prob == pytest.approx(0.8)
    assert cal_rows[0].observed_outcome is True
    assert cal_rows[0].decision_id == result.decision_id

    # Audit row has the decision_id as the cross-store join key.
    audit_rows = list(iter_records(paths["audit"]))
    assert len(audit_rows) == 1
    assert audit_rows[0].decision_id == result.decision_id
    assert audit_rows[0].outcome == "applied"


def test_record_heal_observed_outcome_false_when_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A skipped / failed heal yields ``observed_outcome=False`` for Brier."""
    paths = _scope_paths(monkeypatch, tmp_path)
    wire.record_heal(
        run_id="run-2",
        head_sha="abc456",
        strategy="ruff-format",
        cls="safe",
        confidence=0.3,
        outcome="failed_validation",
        candidates=("ruff-format",),
        sdd_dir=tmp_path,
    )
    cal_rows = calibration.load_log(paths["calibration"])
    assert cal_rows[0].observed_outcome is False


def test_record_heal_unknown_outcome_coerced_safely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unknown outcome literal maps to the safe default ``skipped_no_jobs``."""
    paths = _scope_paths(monkeypatch, tmp_path)
    wire.record_heal(
        run_id="run-3",
        head_sha="abc789",
        strategy="ruff-format",
        cls="safe",
        confidence=0.5,
        outcome="garbage_value",
        candidates=("ruff-format",),
        sdd_dir=tmp_path,
    )
    audit_rows = list(iter_records(paths["audit"]))
    assert audit_rows[0].outcome == "skipped_no_jobs"


def test_record_heal_decision_disabled_returns_empty_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the decision log is disabled, calibration + audit still record."""
    paths = _scope_paths(monkeypatch, tmp_path)
    monkeypatch.setenv(dl.ENV_DISABLE, "0")
    result = wire.record_heal(
        run_id="run-4",
        head_sha="abc999",
        strategy="ruff-format",
        cls="safe",
        confidence=0.5,
        outcome="applied",
        candidates=("ruff-format",),
        sdd_dir=tmp_path,
    )
    assert result.decision_log_written is False
    assert result.audit_written is True
    audit_rows = list(iter_records(paths["audit"]))
    assert len(audit_rows) == 1
    # decision_id is the empty string when the decision log was disabled.
    assert audit_rows[0].decision_id == ""


def test_record_heal_confidence_clamped_to_unit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Out-of-range confidence is clamped, not raised, so the row lands."""
    paths = _scope_paths(monkeypatch, tmp_path)
    result = wire.record_heal(
        run_id="run-5",
        head_sha="abc111",
        strategy="ruff-format",
        cls="safe",
        confidence=2.5,
        outcome="applied",
        candidates=("ruff-format",),
        sdd_dir=tmp_path,
    )
    assert result.decision_log_written is True
    cal_rows = calibration.load_log(paths["calibration"])
    assert 0.0 <= cal_rows[0].predicted_prob <= 1.0


def test_audit_path_env_override_isolates_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``BERNSTEIN_AUTOHEAL_LOG_PATH`` redirects the audit ledger."""
    sdd_root = tmp_path / "sdd"
    audit_override = tmp_path / "elsewhere" / "history.jsonl"
    monkeypatch.setenv(wire.ENV_AUDIT_LOG_PATH, str(audit_override))
    monkeypatch.setenv(dl.ENV_DISABLE, "1")
    monkeypatch.setenv(wire.ENV_DECISION_LOG_PATH, str(tmp_path / "decisions.jsonl"))
    monkeypatch.setenv(wire.ENV_CALIBRATION_LOG_PATH, str(tmp_path / "calibration.jsonl"))
    wire.record_heal(
        run_id="run-6",
        head_sha="abc222",
        strategy="ruff-format",
        cls="safe",
        confidence=0.5,
        outcome="applied",
        candidates=("ruff-format",),
        sdd_dir=sdd_root,
    )
    assert audit_override.exists()
    # The default .sdd-rooted file must NOT have been created.
    assert not (sdd_root / "autoheal-history.jsonl").exists()


def test_decision_log_alternatives_exclude_winner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The winner must not appear in its own alternatives list."""
    paths = _scope_paths(monkeypatch, tmp_path)
    wire.record_heal(
        run_id="run-7",
        head_sha="abc333",
        strategy="ruff-format",
        cls="safe",
        confidence=0.5,
        outcome="applied",
        candidates=("ruff-format", "agents-md-sync", "typos-allowlist"),
        sdd_dir=tmp_path,
    )
    rows = list(dl.iter_records(paths["decision"]))
    alts = {a.id for a in rows[0].alternatives}
    assert "ruff-format" not in alts
    assert alts == {"agents-md-sync", "typos-allowlist"}


def test_audit_row_is_valid_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Audit rows must round-trip through json.loads cleanly."""
    paths = _scope_paths(monkeypatch, tmp_path)
    wire.record_heal(
        run_id="run-8",
        head_sha="abc444",
        strategy="ruff-format",
        cls="safe",
        confidence=0.5,
        outcome="applied",
        candidates=("ruff-format",),
        sdd_dir=tmp_path,
    )
    raw = paths["audit"].read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    parsed = json.loads(raw[0])
    assert parsed["strategy"] == "ruff-format"
    assert parsed["outcome"] == "applied"
