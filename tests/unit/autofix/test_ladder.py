"""Unit tests for the autofix escalation ladder.

Covers rung selection ordering, cost-cap refusal, audit-trail emit,
and the rung-3 "out of scope" path. Rungs 1 and 2 are tested at the
detector boundary - their actors are stubbed in this MVP per the
ticket scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.autofix.ladder import (
    COST_CAP_RUNG_1_USD,
    COST_CAP_RUNG_2_USD,
    AutofixOutcome,
    CIFailure,
    LadderSettings,
    LintDriftActor,
    OutOfScopeActor,
    Rung,
    build_default_ladder,
    detect_lint_drift,
    detect_multi_file_pr_touched,
    detect_out_of_scope,
    detect_single_file_small_diff,
    emit_ladder_event,
    fire_rung,
    load_ladder_settings,
    run_ladder_for_failure,
    select_rung,
    stub_actor,
)
from bernstein.core.security.audit import AuditLog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _CommentRecorder:
    """Test double for the rung-3 post-comment callable."""

    posted: list[tuple[str, int, str]] = field(default_factory=list)

    def __call__(self, repo: str, pr_number: int, body: str) -> None:
        self.posted.append((repo, pr_number, body))


@dataclass
class _PatchRecorder:
    """Test double for the rung-0 apply-patch callable."""

    return_value: tuple[bool, str, str] = (True, "sha-rung0", "applied")
    calls: list[CIFailure] = field(default_factory=list)

    def __call__(self, failure: CIFailure) -> tuple[bool, str, str]:
        self.calls.append(failure)
        return self.return_value


def _audit(tmp_path: Path) -> AuditLog:
    """Build an isolated audit log for ladder lifecycle events."""
    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"a" * 32)
    key_path.chmod(0o600)
    return AuditLog(audit_dir=tmp_path / "audit", key_path=key_path)


def _failure(**overrides: Any) -> CIFailure:
    base: dict[str, Any] = {
        "repo": "owner/name",
        "pr_number": 42,
        "head_sha": "deadbeefcafebabe",
        "run_id": "9999",
        "failing_files": (),
        "pr_touched_files": (),
        "log_excerpt": "",
        "diff_line_count": 0,
        "signature": "sig-default",
    }
    base.update(overrides)
    return CIFailure(**base)


def _build_ladder() -> tuple[
    tuple[Rung, ...],
    _PatchRecorder,
    _CommentRecorder,
]:
    patch_recorder = _PatchRecorder()
    comment_recorder = _CommentRecorder()
    ladder = build_default_ladder(
        apply_lint_patch=patch_recorder,
        post_comment=comment_recorder,
    )
    return ladder, patch_recorder, comment_recorder


# ---------------------------------------------------------------------------
# Detector unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "excerpt",
    [
        "ruff check failed: src/foo.py:1:1 E501 line too long",
        "Would reformat src/foo.py",
        "isort: import order incorrect in src/bar.py",
    ],
)
def test_detect_lint_drift_matches_format_signals(excerpt: str) -> None:
    """Rung 0 detector matches ruff / format-drift payloads."""
    assert detect_lint_drift(_failure(log_excerpt=excerpt)) is True


@pytest.mark.parametrize(
    "excerpt",
    [
        "",
        "CodeQL alert: cve-2024-1111",
        "pytest exited 1: AssertionError",
    ],
)
def test_detect_lint_drift_skips_non_format_signals(excerpt: str) -> None:
    """Rung 0 detector ignores non-format payloads."""
    assert detect_lint_drift(_failure(log_excerpt=excerpt)) is False


def test_detect_single_file_small_diff_matches_in_scope() -> None:
    """Rung 1 detector accepts a small diff on a touched file."""
    failure = _failure(
        failing_files=("src/foo.py",),
        pr_touched_files=("src/foo.py", "tests/test_foo.py"),
        diff_line_count=12,
    )
    assert detect_single_file_small_diff(failure) is True


def test_detect_single_file_small_diff_rejects_large_diff() -> None:
    """Rung 1 detector refuses diffs over 30 lines."""
    failure = _failure(
        failing_files=("src/foo.py",),
        pr_touched_files=("src/foo.py",),
        diff_line_count=120,
    )
    assert detect_single_file_small_diff(failure) is False


def test_detect_single_file_small_diff_rejects_two_files() -> None:
    """Rung 1 detector refuses multi-file failures."""
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("src/a.py", "src/b.py"),
        diff_line_count=10,
    )
    assert detect_single_file_small_diff(failure) is False


def test_detect_multi_file_pr_touched_matches_overlap() -> None:
    """Rung 2 detector matches when failing + touched files overlap."""
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("src/a.py", "tests/test_a.py"),
    )
    assert detect_multi_file_pr_touched(failure) is True


def test_detect_multi_file_pr_touched_skips_no_overlap() -> None:
    """Rung 2 detector skips when failures do not touch PR scope."""
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("docs/x.md",),
    )
    assert detect_multi_file_pr_touched(failure) is False


def test_detect_out_of_scope_matches_disjoint_files() -> None:
    """Rung 3 detector matches when failing and touched files are disjoint."""
    failure = _failure(
        failing_files=("src/legacy.py",),
        pr_touched_files=("docs/README.md",),
    )
    assert detect_out_of_scope(failure) is True


def test_detect_out_of_scope_skips_empty_inputs() -> None:
    """Rung 3 detector needs both lists to be non-empty."""
    assert detect_out_of_scope(_failure()) is False


# ---------------------------------------------------------------------------
# Rung selection
# ---------------------------------------------------------------------------


def test_select_rung_picks_lowest_match() -> None:
    """The selector returns the lowest rung whose detector fires."""
    ladder, _, _ = _build_ladder()
    failure = _failure(
        log_excerpt="ruff check failed",
        failing_files=("src/foo.py",),
        pr_touched_files=("src/foo.py",),
        diff_line_count=10,
    )
    selection = select_rung(ladder, failure, cost_cap_per_pr=5.0)
    assert selection.accepted is True
    assert selection.rung is not None
    # Rung 0 matches before rung 1 even though both detectors fire.
    assert selection.rung.rung_id == "rung-0-lint"


def test_select_rung_returns_none_when_no_detector_fires() -> None:
    """An empty failure body matches no rung."""
    ladder, _, _ = _build_ladder()
    selection = select_rung(ladder, _failure(), cost_cap_per_pr=5.0)
    assert selection.rung is None
    assert selection.accepted is False


def test_select_rung_refuses_above_operator_cap() -> None:
    """Rungs above ``cost_cap_per_pr`` are matched but not accepted."""
    ladder, _, _ = _build_ladder()
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("src/a.py",),
    )
    selection = select_rung(
        ladder,
        failure,
        cost_cap_per_pr=COST_CAP_RUNG_2_USD - 0.10,
    )
    assert selection.rung is not None
    assert selection.rung.rung_id == "rung-2-multi-file"
    assert selection.accepted is False
    assert "refusing to escalate" in selection.reason


def test_select_rung_zero_cap_means_unlimited() -> None:
    """A ``cost_cap_per_pr`` of zero accepts every matched rung."""
    ladder, _, _ = _build_ladder()
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("src/a.py",),
    )
    selection = select_rung(ladder, failure, cost_cap_per_pr=0.0)
    assert selection.rung is not None
    assert selection.accepted is True


# ---------------------------------------------------------------------------
# Firing
# ---------------------------------------------------------------------------


def test_fire_rung_zero_applies_lint_patch() -> None:
    """Firing Rung 0 invokes the patch callable and returns ``applied``."""
    ladder, patch_recorder, _ = _build_ladder()
    failure = _failure(log_excerpt="ruff check found violations")
    outcome = fire_rung(ladder, failure, cost_cap_per_pr=5.0)
    assert outcome.outcome == "applied"
    assert outcome.rung_id == "rung-0-lint"
    assert outcome.commit_sha == "sha-rung0"
    assert len(patch_recorder.calls) == 1


def test_fire_rung_three_posts_out_of_scope_comment() -> None:
    """Firing Rung 3 posts a comment but does not apply any patch."""
    ladder, patch_recorder, comment_recorder = _build_ladder()
    failure = _failure(
        failing_files=("src/legacy.py",),
        pr_touched_files=("docs/README.md",),
    )
    outcome = fire_rung(ladder, failure, cost_cap_per_pr=5.0)
    assert outcome.outcome == "commented"
    assert outcome.rung_id == "rung-3-out-of-scope"
    assert patch_recorder.calls == []
    assert len(comment_recorder.posted) == 1
    repo, pr_number, body = comment_recorder.posted[0]
    assert repo == "owner/name"
    assert pr_number == 42
    assert "out of scope" in body.lower()
    assert "src/legacy.py" in body


def test_fire_rung_stubs_rung_one_actor() -> None:
    """Rung 1 actor returns ``stubbed`` in this MVP."""
    ladder, _, _ = _build_ladder()
    failure = _failure(
        failing_files=("src/foo.py",),
        pr_touched_files=("src/foo.py",),
        diff_line_count=10,
    )
    outcome = fire_rung(ladder, failure, cost_cap_per_pr=5.0)
    assert outcome.rung_id == "rung-1-single-file"
    assert outcome.outcome == "stubbed"
    assert "deferred to a follow-up" in outcome.message


def test_fire_rung_stubs_rung_two_actor() -> None:
    """Rung 2 actor returns ``stubbed`` in this MVP."""
    ladder, _, _ = _build_ladder()
    failure = _failure(
        failing_files=("src/a.py", "src/b.py"),
        pr_touched_files=("src/a.py",),
    )
    outcome = fire_rung(ladder, failure, cost_cap_per_pr=5.0)
    assert outcome.rung_id == "rung-2-multi-file"
    assert outcome.outcome == "stubbed"


def test_fire_rung_returns_skipped_when_no_match() -> None:
    """When no rung matches, ``fire_rung`` returns a synthetic skipped outcome."""
    ladder, _, _ = _build_ladder()
    outcome = fire_rung(ladder, _failure(), cost_cap_per_pr=5.0)
    assert outcome.outcome == "skipped"
    assert "no rung matched" in outcome.message


def test_fire_rung_returns_cost_capped_when_over_cap() -> None:
    """A matched rung above the cap returns ``cost_capped`` without firing the actor."""
    ladder, patch_recorder, comment_recorder = _build_ladder()
    # Force a rung-1 match and starve the cap so it cannot fire.
    failure = _failure(
        failing_files=("src/foo.py",),
        pr_touched_files=("src/foo.py",),
        diff_line_count=10,
    )
    outcome = fire_rung(
        ladder,
        failure,
        cost_cap_per_pr=COST_CAP_RUNG_1_USD - 0.05,
    )
    assert outcome.outcome == "cost_capped"
    assert outcome.rung_id == "rung-1-single-file"
    # Actors never fired.
    assert patch_recorder.calls == []
    assert comment_recorder.posted == []


# ---------------------------------------------------------------------------
# Actor edge cases
# ---------------------------------------------------------------------------


def test_lint_drift_actor_reports_no_patch_as_skipped() -> None:
    """The patch callable returning ``success=False`` becomes ``skipped``."""
    actor = LintDriftActor(apply_patch=lambda _f: (False, "", "no fixable issues"))
    outcome = actor(_failure(log_excerpt="ruff: ok"))
    assert outcome.outcome == "skipped"
    assert outcome.commit_sha == ""


def test_out_of_scope_actor_returns_errored_on_post_failure() -> None:
    """A raising poster becomes an ``errored`` outcome, not a crash."""

    def _raiser(_repo: str, _pr: int, _body: str) -> None:
        raise RuntimeError("github down")

    actor = OutOfScopeActor(post_comment=_raiser)
    outcome = actor(
        _failure(
            failing_files=("src/x.py",),
            pr_touched_files=("docs/y.md",),
        )
    )
    assert outcome.outcome == "errored"
    assert "github down" in outcome.message


def test_stub_actor_signals_deferred_action() -> None:
    """The shared stub helper produces a structured ``stubbed`` outcome."""
    actor = stub_actor("rung-1-single-file", "single-file")
    outcome = actor(_failure())
    assert outcome.outcome == "stubbed"
    assert outcome.rung_id == "rung-1-single-file"


# ---------------------------------------------------------------------------
# Audit-trail emission
# ---------------------------------------------------------------------------


def test_emit_ladder_event_writes_audit_entry(tmp_path: Path) -> None:
    """A fired rung writes the lifecycle event into the audit chain."""
    audit = _audit(tmp_path)
    failure = _failure(
        failing_files=("src/legacy.py",),
        pr_touched_files=("docs/README.md",),
        signature="sig-rung3",
    )
    outcome = AutofixOutcome(
        outcome="commented",
        rung_id="rung-3-out-of-scope",
        message="out-of-scope comment posted",
    )
    emit_ladder_event(audit, failure=failure, outcome=outcome)

    valid, errors = audit.verify()
    assert valid, errors

    log_files = sorted((tmp_path / "audit").glob("*.jsonl"))
    assert log_files, "audit log file must exist"
    lines = [line for line in log_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    import json

    record = json.loads(lines[0])
    assert record["event_type"] == "autofix.ladder.fire"
    assert record["actor"] == "autofix-ladder"
    assert record["resource_id"] == "owner/name#42"
    details = record["details"]
    assert details["producer"] == "autofix-ladder"
    assert details["rung_id"] == "rung-3-out-of-scope"
    assert details["failure_signature"] == "sig-rung3"
    assert details["outcome"] == "commented"
    assert details["failing_files"] == ["src/legacy.py"]
    assert details["pr_touched_files"] == ["docs/README.md"]


# ---------------------------------------------------------------------------
# Feature-flag / coordinator
# ---------------------------------------------------------------------------


def test_run_ladder_for_failure_short_circuits_when_disabled(tmp_path: Path) -> None:
    """The coordinator skips every rung when the feature flag is off."""
    ladder, patch_recorder, comment_recorder = _build_ladder()
    audit = _audit(tmp_path)
    failure = _failure(
        failing_files=("src/legacy.py",),
        pr_touched_files=("docs/README.md",),
    )
    outcome = run_ladder_for_failure(
        failure=failure,
        settings=LadderSettings(enabled=False, cost_cap_per_pr_usd=5.0),
        ladder=ladder,
        audit=audit,
    )
    assert outcome.outcome == "skipped"
    assert "operator-flagged off" in outcome.message
    # The disabled-path never invokes actors and never writes audit lines.
    assert patch_recorder.calls == []
    assert comment_recorder.posted == []
    log_files = list((tmp_path / "audit").glob("*.jsonl"))
    assert log_files == []


def test_run_ladder_for_failure_emits_audit_when_enabled(tmp_path: Path) -> None:
    """An enabled ladder fires the rung and writes one audit event."""
    ladder, _, comment_recorder = _build_ladder()
    audit = _audit(tmp_path)
    failure = _failure(
        failing_files=("src/legacy.py",),
        pr_touched_files=("docs/README.md",),
        signature="sig-enabled",
    )
    outcome = run_ladder_for_failure(
        failure=failure,
        settings=LadderSettings(enabled=True, cost_cap_per_pr_usd=5.0),
        ladder=ladder,
        audit=audit,
    )
    assert outcome.outcome == "commented"
    assert len(comment_recorder.posted) == 1
    valid, errors = audit.verify()
    assert valid, errors


def test_load_ladder_settings_reads_yaml(tmp_path: Path) -> None:
    """The yaml reader picks up the feature flag and the cap."""
    yaml_path = tmp_path / "bernstein.yaml"
    yaml_path.write_text(
        """
goal: test
autofix:
  cost_cap_per_pr: 2.5
  ladder:
    enabled: true
""",
        encoding="utf-8",
    )
    settings = load_ladder_settings(yaml_path)
    assert settings.enabled is True
    assert settings.cost_cap_per_pr_usd == 2.5


def test_load_ladder_settings_defaults_when_block_missing(tmp_path: Path) -> None:
    """Missing ``autofix`` block falls back to the operator-flagged-off default."""
    yaml_path = tmp_path / "bernstein.yaml"
    yaml_path.write_text("goal: test\n", encoding="utf-8")
    settings = load_ladder_settings(yaml_path)
    assert settings.enabled is False
    # Default cap is positive so detector-only stubs see a usable value.
    assert settings.cost_cap_per_pr_usd > 0


def test_load_ladder_settings_defaults_when_file_missing(tmp_path: Path) -> None:
    """A missing yaml file is non-fatal and returns the disabled defaults."""
    settings = load_ladder_settings(tmp_path / "does-not-exist.yaml")
    assert settings.enabled is False


# ---------------------------------------------------------------------------
# CLI dry-run
# ---------------------------------------------------------------------------


def test_cli_ladder_dry_run_reports_rung_zero(tmp_path: Path) -> None:
    """``bernstein autofix ladder --dry-run`` prints the rung that would fire."""
    from click.testing import CliRunner

    from bernstein.cli.commands.autofix_cmd import autofix_group

    yaml_path = tmp_path / "bernstein.yaml"
    yaml_path.write_text(
        "goal: test\nautofix:\n  cost_cap_per_pr: 1.0\n  ladder:\n    enabled: true\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        autofix_group,
        [
            "ladder",
            "--pr",
            "42",
            "--repo",
            "owner/name",
            "--dry-run",
            "--log-excerpt",
            "ruff check failed",
            "--config",
            str(yaml_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rung-0-lint" in result.output
    assert "accepted:" in result.output


def test_cli_ladder_requires_dry_run_flag() -> None:
    """The MVP CLI rejects calls without ``--dry-run``."""
    from click.testing import CliRunner

    from bernstein.cli.commands.autofix_cmd import autofix_group

    runner = CliRunner()
    result = runner.invoke(autofix_group, ["ladder", "--pr", "42"])
    assert result.exit_code != 0
    assert "--dry-run" in result.output


def test_cli_ladder_reports_no_match_when_failure_is_empty(tmp_path: Path) -> None:
    """When no detector fires the CLI reports ``(no match)``."""
    from click.testing import CliRunner

    from bernstein.cli.commands.autofix_cmd import autofix_group

    yaml_path = tmp_path / "bernstein.yaml"
    yaml_path.write_text(
        "autofix:\n  cost_cap_per_pr: 1.0\n  ladder:\n    enabled: true\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        autofix_group,
        [
            "ladder",
            "--pr",
            "1",
            "--repo",
            "owner/name",
            "--dry-run",
            "--config",
            str(yaml_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(no match)" in result.output
