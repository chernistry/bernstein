"""Unit tests for the file-backed atomic ``claim_next`` primitive."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.main import cli
from bernstein.core.tasks.claim import Backlog, BacklogEntry, ClaimFilter, claim_next, claim_next_entry


def _rows(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_claim_next_missing_backlog_returns_none(tmp_path: Path) -> None:
    """A missing backlog file is treated as empty."""
    assert claim_next(tmp_path / "missing.json", claimer_id="worker-a") is None


def test_claim_next_empty_backlog_returns_none_without_mutating(tmp_path: Path) -> None:
    """An explicitly-empty backlog returns None without changing disk state."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(backlog_path, [])
    before = backlog_path.read_bytes()

    assert claim_next(backlog_path, claimer_id="worker-a") is None

    assert backlog_path.read_bytes() == before


def test_claim_next_marks_entry_in_progress_and_stamps_claimer(tmp_path: Path) -> None:
    """A successful claim flips the row to in_progress and records ownership."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            BacklogEntry(id="review-1", role="reviewer"),
            BacklogEntry(id="backend-1", role="backend"),
        ],
    )

    claimed = claim_next(backlog_path, claimer_id="worker-a", filter=ClaimFilter(role="reviewer"))

    assert claimed == "review-1"
    rows = _rows(backlog_path)
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["claimer"] == "worker-a"
    assert rows[0]["attempts"] == 1
    assert isinstance(rows[0]["claimed_at"], float)
    assert rows[1]["status"] == "open"
    assert rows[1]["claimer"] is None


def test_claim_next_skips_already_claimed_rows(tmp_path: Path) -> None:
    """Workers never receive rows that are already in progress."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            BacklogEntry(id="taken", role="reviewer", status="in_progress", claimer="other"),
            BacklogEntry(id="open", role="reviewer"),
        ],
    )

    assert claim_next(backlog_path, claimer_id="worker-a", filter=ClaimFilter(role="reviewer")) == "open"


def test_claim_next_filters_by_project_role_capability_dependencies_and_attempts(tmp_path: Path) -> None:
    """Only rows matching every filter predicate are eligible."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            BacklogEntry(id="wrong-project", project="other", role="reviewer", capabilities=["review"]),
            BacklogEntry(id="wrong-role", project="bernstein", role="backend", capabilities=["review"]),
            BacklogEntry(id="wrong-capability", project="bernstein", role="reviewer", capabilities=["docs"]),
            BacklogEntry(id="blocked", project="bernstein", role="reviewer", capabilities=["review"], depends_on=["dep"]),
            BacklogEntry(id="exhausted", project="bernstein", role="reviewer", capabilities=["review"], attempts=2),
            BacklogEntry(id="eligible", project="bernstein", role="reviewer", capabilities=["review"], depends_on=["dep"]),
        ],
    )

    claimed = claim_next(
        backlog_path,
        claimer_id="worker-a",
        filter=ClaimFilter(
            project="bernstein",
            role="reviewer",
            capability="review",
            completed_ids={"dep"},
            max_attempts=2,
        ),
    )

    assert claimed == "blocked"
    assert claim_next(
        backlog_path,
        claimer_id="worker-b",
        filter=ClaimFilter(
            project="bernstein",
            role="reviewer",
            capability="review",
            completed_ids={"dep"},
            max_attempts=2,
        ),
    ) == "eligible"


def test_claim_next_skips_rows_with_unmet_dependencies(tmp_path: Path) -> None:
    """Rows with dependencies outside completed_ids remain open and unclaimed."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            BacklogEntry(id="blocked", role="reviewer", depends_on=["dep"]),
            BacklogEntry(id="eligible", role="reviewer"),
        ],
    )

    claimed = claim_next(
        backlog_path,
        claimer_id="worker-a",
        filter=ClaimFilter(role="reviewer", completed_ids=set()),
    )

    assert claimed == "eligible"
    rows = _rows(backlog_path)
    assert rows[0]["id"] == "blocked"
    assert rows[0]["status"] == "open"
    assert rows[0]["claimer"] is None
    assert rows[1]["id"] == "eligible"
    assert rows[1]["status"] == "in_progress"


def test_claim_next_entry_returns_claimed_record(tmp_path: Path) -> None:
    """The richer API returns the updated row for CLI/adapters."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(backlog_path, [BacklogEntry(id="review-1", role="reviewer")])

    claimed = claim_next_entry(backlog_path, claimer_id="worker-a", filter=ClaimFilter(role="reviewer"))

    assert claimed is not None
    assert claimed.id == "review-1"
    assert claimed.status == "in_progress"
    assert claimed.claimer == "worker-a"


def test_backlog_claim_cli_claims_disjoint_role_filtered_tasks(tmp_path: Path) -> None:
    """``bernstein backlog claim --role`` exposes the same primitive to external workers."""
    backlog_path = tmp_path / "backlog.json"
    Backlog.write(
        backlog_path,
        [
            BacklogEntry(id="review-1", role="reviewer"),
            BacklogEntry(id="backend-1", role="backend"),
            BacklogEntry(id="review-2", role="reviewer"),
        ],
    )
    runner = CliRunner()

    first = runner.invoke(
        cli,
        ["backlog", "claim", "--backlog", str(backlog_path), "--role", "reviewer", "--agent-id", "agent-a", "--json"],
    )
    second = runner.invoke(
        cli,
        ["backlog", "claim", "--backlog", str(backlog_path), "--role", "reviewer", "--agent-id", "agent-b", "--json"],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = json.loads(first.output)
    second_payload = json.loads(second.output)
    assert first_payload["id"] == "review-1"
    assert second_payload["id"] == "review-2"

    rows = _rows(backlog_path)
    assert rows[0]["claimer"] == "agent-a"
    assert rows[1]["status"] == "open"
    assert rows[2]["claimer"] == "agent-b"
