"""Unit tests for the blast-radius scorer (issue #1322).

Coverage:

* Known-destructive operations (DROP TABLE, rm -rf, schema migration,
  `.env` writes) -> score 1.0 + ``hard_one_way``.
* Pure documentation change -> score < 0.1.
* Mixed change (docs + a moderately risky path) -> mid-range score.
* Gate evaluation: ``--max-blast-radius`` ceiling, opt-in behaviour, and
  the rule that hard one-way detectors always require explicit approval.
* Detector loading + persisted report round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.lifecycle.blast_radius_gate import (
    ENV_MAX_BLAST_RADIUS,
    install_blast_radius_gate,
)
from bernstein.core.quality.blast_radius import (
    BlastRadiusScorer,
    Detector,
    default_detectors_path,
    evaluate_gate,
    load_detectors,
    load_report,
    save_report,
    score_change,
)

# ---------------------------------------------------------------------------
# Defaults load successfully
# ---------------------------------------------------------------------------


def test_default_detectors_load() -> None:
    detectors = load_detectors()
    ids = {d.id for d in detectors}
    # A representative sample of the must-detect patterns called out in
    # issue #1322. If any of these disappear from the YAML the gate
    # silently regresses for an entire risk category.
    expected = {
        "sql_drop_statement",
        "sql_delete_unbounded",
        "shell_rm_rf",
        "alembic_migration",
        "django_migration",
        "dotenv_write",
        "pem_or_key_file",
    }
    missing = expected - ids
    assert not missing, f"default detectors missing critical ids: {missing}"


def test_default_detectors_path_resolves() -> None:
    path = default_detectors_path()
    assert path.exists()
    assert path.name == "detectors.yaml"


# ---------------------------------------------------------------------------
# Destructive operations -> score 1.0
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scorer() -> BlastRadiusScorer:
    return BlastRadiusScorer()


def test_drop_table_scores_one(scorer: BlastRadiusScorer) -> None:
    diff = "DROP TABLE users;\n"
    report = scorer.score(files=("db/cleanup.sql",), diff_text=diff)
    assert report.score == 1.0
    assert report.hard_one_way is True
    assert any(h.detector_id == "sql_drop_statement" for h in report.hits)


def test_delete_from_unbounded_scores_one(scorer: BlastRadiusScorer) -> None:
    diff = "DELETE FROM payments;"
    report = scorer.score(files=("scripts/cleanup.sql",), diff_text=diff)
    assert report.score == 1.0
    assert report.hard_one_way is True
    assert any(h.detector_id == "sql_delete_unbounded" for h in report.hits)


def test_rm_rf_scores_one(scorer: BlastRadiusScorer) -> None:
    diff = "rm -rf $HOME/cache\n"
    report = scorer.score(files=("scripts/cleanup.sh",), diff_text=diff)
    assert report.score == 1.0
    assert report.hard_one_way is True
    assert any(h.detector_id == "shell_rm_rf" for h in report.hits)


def test_alembic_migration_scores_one(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=("alembic/versions/2024_01_drop_legacy.py",))
    assert report.score == 1.0
    assert report.hard_one_way is True
    assert any(h.detector_id == "alembic_migration" for h in report.hits)


def test_django_migration_scores_one(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=("apps/users/migrations/0042_drop_legacy.py",))
    assert report.score == 1.0
    assert report.hard_one_way is True


def test_dotenv_write_scores_one(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=(".env",))
    assert report.score == 1.0
    assert report.hard_one_way is True
    assert any(h.detector_id == "dotenv_write" for h in report.hits)


def test_pem_key_scores_one(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=("deploy/keys/prod.pem",))
    assert report.score == 1.0
    assert report.hard_one_way is True


# ---------------------------------------------------------------------------
# Pure docs change -> score < 0.1
# ---------------------------------------------------------------------------


def test_pure_doc_change_scores_below_threshold(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(
        files=("docs/intro.md",),
        diff_text="# Welcome\n\nA few new paragraphs explaining the feature.\n",
    )
    assert report.score < 0.1
    assert report.hard_one_way is False
    # No hits: the docs path doesn't match any detector.
    assert report.hits == ()


def test_single_comment_only_change(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(
        files=("src/lib/util.py",),
        diff_text="# fix typo in comment\n",
    )
    assert report.score < 0.1


# ---------------------------------------------------------------------------
# Mixed change -> mid-range score
# ---------------------------------------------------------------------------


def test_mixed_change_scores_mid_range(scorer: BlastRadiusScorer) -> None:
    """A change touching package.json + an api/ handler should land mid-range.

    Neither detector is hard one-way, so the score must be > 0.1 (signal
    fired) and strictly less than 1.0 (no hard detectors).
    """
    report = scorer.score(
        files=(
            "package.json",
            "src/api/handlers/users.ts",
        ),
        diff_text="export function listUsers() { /* ... */ }\n",
    )
    assert 0.1 < report.score < 1.0
    assert report.hard_one_way is False
    ids = {h.detector_id for h in report.hits}
    assert {"package_manifest", "public_api_module"}.issubset(ids)


def test_many_files_bumps_score_via_file_count_component(scorer: BlastRadiusScorer) -> None:
    files = tuple(f"src/lib/mod_{i}.py" for i in range(100))
    report = scorer.score(files=files, diff_text="")
    # File-count component saturates at 0.2; with no other hits this is the floor.
    assert report.score >= 0.2
    assert report.hard_one_way is False


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def test_gate_no_ceiling_is_passthrough(scorer: BlastRadiusScorer) -> None:
    """No ceiling => existing runs unaffected (issue-#1322 constraint)."""
    report = scorer.score(files=("alembic/versions/2024_01.py",))
    decision = evaluate_gate(report, max_score=None)
    assert decision.allowed is True
    assert "skipped" in decision.reason


def test_gate_refuses_when_score_exceeds_ceiling(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=(".env.prod",))
    decision = evaluate_gate(report, max_score=0.4)
    assert decision.allowed is False
    assert "refused" in decision.reason


def test_gate_refuses_hard_one_way_even_below_ceiling(
    scorer: BlastRadiusScorer,
) -> None:
    """Hard one-way detectors must require explicit approval (ceiling < 1.0)."""
    report = scorer.score(files=("alembic/versions/2024_01.py",))
    # Score is 1.0 due to hard detector, so any ceiling < 1.0 must refuse.
    decision = evaluate_gate(report, max_score=0.9)
    assert decision.allowed is False
    assert report.hard_one_way is True


def test_gate_allows_when_score_within_ceiling(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=("docs/intro.md",))
    decision = evaluate_gate(report, max_score=0.5)
    assert decision.allowed is True


def test_gate_rejects_invalid_ceiling(scorer: BlastRadiusScorer) -> None:
    report = scorer.score(files=("docs/intro.md",))
    with pytest.raises(ValueError):
        evaluate_gate(report, max_score=1.5)


# ---------------------------------------------------------------------------
# Custom detectors
# ---------------------------------------------------------------------------


def test_custom_detector_via_score_change() -> None:
    custom = [
        Detector(
            id="custom_glob",
            kind="path_glob",
            pattern="**/forbidden.txt",
            description="custom",
            severity="critical",
            weight=1.0,
            hard_one_way=True,
        )
    ]
    report = score_change(files=("path/to/forbidden.txt",), detectors=custom)
    assert report.score == 1.0
    assert report.hard_one_way is True


def test_detector_validation_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        Detector(
            id="x",
            kind="bogus",  # type: ignore[arg-type]
            pattern="*",
            description="",
            severity="low",
            weight=0.1,
        )


def test_detector_validation_rejects_bad_weight() -> None:
    with pytest.raises(ValueError):
        Detector(
            id="x",
            kind="path_glob",
            pattern="*",
            description="",
            severity="low",
            weight=2.0,
        )


# ---------------------------------------------------------------------------
# Persisted report round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_report_round_trip(tmp_path: Path) -> None:
    scorer_local = BlastRadiusScorer()
    report = scorer_local.score(files=("alembic/versions/2024.py",))
    path = save_report(report, task_id="T-1322", workdir=tmp_path)
    assert path.exists()
    loaded = load_report("T-1322", workdir=tmp_path)
    assert loaded is not None
    assert loaded.score == report.score
    assert loaded.hard_one_way == report.hard_one_way
    assert {h.detector_id for h in loaded.hits} == {h.detector_id for h in report.hits}


def test_load_report_missing_returns_none(tmp_path: Path) -> None:
    assert load_report("nope", workdir=tmp_path) is None


def test_report_to_dict_is_json_serializable() -> None:
    report = score_change(files=("db/migrations/0001.sql",))
    payload = report.to_dict()
    # Round-trip via JSON to catch any non-serialisable fields.
    json.dumps(payload)
    assert payload["hard_one_way"] is True


# ---------------------------------------------------------------------------
# Lifecycle wire-in (install_blast_radius_gate)
# ---------------------------------------------------------------------------


def test_install_gate_noop_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.security.blocking_hooks import BlockingHookRunner

    monkeypatch.delenv(ENV_MAX_BLAST_RADIUS, raising=False)
    runner = BlockingHookRunner()
    installed = install_blast_radius_gate(runner)
    assert installed is False
    assert "pre_merge" not in runner.registered_events()


def test_install_gate_registers_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.security.blocking_hooks import BlockingHookRunner

    monkeypatch.setenv(ENV_MAX_BLAST_RADIUS, "0.4")
    runner = BlockingHookRunner()
    installed = install_blast_radius_gate(runner)
    assert installed is True
    assert "pre_merge" in runner.registered_events()


def test_install_gate_ignores_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.security.blocking_hooks import BlockingHookRunner

    monkeypatch.setenv(ENV_MAX_BLAST_RADIUS, "not-a-float")
    runner = BlockingHookRunner()
    installed = install_blast_radius_gate(runner)
    assert installed is False


def test_install_gate_ignores_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.security.blocking_hooks import BlockingHookRunner

    monkeypatch.setenv(ENV_MAX_BLAST_RADIUS, "1.5")
    runner = BlockingHookRunner()
    installed = install_blast_radius_gate(runner)
    assert installed is False
