"""Unit tests for the Tier-3 OpenRouter shadow-mode escalation.

The acceptance-criteria coverage map (see #1711):

* ``test_flag_off_no_op_writes_nothing`` - flag-off is a true no-op.
* ``test_tier2_produced_patch_skips_tier3`` - Tier-2 wins; Tier-3 stays
  out of the way.
* ``test_unsafe_failure_class_is_rejected_before_run_hook`` - class
  allowlist gate fires before any provider call.
* ``test_shadow_capture_writes_patch_envelope_decision_lineage`` - the
  happy-path capture writes the diff, the envelope row, the
  decision-log entry and produces the lineage-v2 child body shape.
* ``test_cordon_violation_rejected_with_decision_log_kind`` - a patch
  that touches paths outside the cordon is rejected with the
  ``cordon_violation`` decision-log kind.
* ``test_recurrence_threshold_escalates_to_tier4`` - three captures in
  a window flip to ``recurrence_escalation``.
* ``test_run_hook_429_falls_back_to_next_model`` - the fallback walk is
  invisible to the runner; only the final ``model_used`` is recorded.
* ``test_promote_from_shadow_flag_flips_kind`` - promotion gate exists
  but is off by default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.autofix import tier3
from bernstein.core.autofix.tier3 import (
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_PRIMARY_MODEL,
    FailureContext,
    RecurrenceTracker,
    RunResult,
    Tier3Config,
    Tier3Runner,
    extract_paths_from_unified_diff,
)
from bernstein.core.observability import decision_log as dl

# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


@dataclass
class _RecordingRunHook:
    """Records each call and returns a queued :class:`RunResult`.

    The hook deliberately exposes the same signature as the real
    :class:`bernstein.core.autofix.tier3.RunHook` protocol so the tests
    exercise the production seam.
    """

    queued: list[RunResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None

    def __call__(self, **kwargs: Any) -> RunResult:
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        if not self.queued:
            return RunResult(patch="", model_used="")
        return self.queued.pop(0)


def _diff_for(path: str, body: str = "+added line") -> str:
    """Render a minimal unified diff touching ``path``."""
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,2 @@\n existing line\n{body}\n"


def _deletion_diff_for(path: str) -> str:
    """Render a minimal unified diff that deletes ``path`` entirely.

    Matches the format ``git diff`` emits for a pure deletion: the
    new-side header is ``+++ /dev/null`` and every existing line is a
    ``-`` deletion line.
    """
    return f"--- a/{path}\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-existing line\n"


def _rename_diff(old: str, new: str) -> str:
    """Render a minimal unified diff that renames ``old`` -> ``new``.

    Matches the format ``git diff`` emits for a content-preserving
    rename: ``rename from`` / ``rename to`` lines, then a paired
    ``--- a/<old>`` / ``+++ b/<new>`` and a single edit hunk so the
    file-pair headers are visible to the parser.
    """
    return (
        f"diff --git a/{old} b/{new}\n"
        "similarity index 95%\n"
        f"rename from {old}\n"
        f"rename to {new}\n"
        f"--- a/{old}\n"
        f"+++ b/{new}\n"
        "@@ -1,1 +1,1 @@\n"
        "-existing line\n"
        "+edited line\n"
    )


def _ctx(**overrides: Any) -> FailureContext:
    base: dict[str, Any] = {
        "failed_run_id": "987654321",
        "head_sha": "0123456789abcdef" * 2 + "0123456789abcdef" * 2,
        "failure_class": "Lint",
        "failing_test_nodeid": "tests/unit/test_a.py::test_z",
        "log_tail": "ruff check found violations",
        "regression_test_sha": "deadbeef" * 5,
    }
    base.update(overrides)
    return FailureContext(**base)


def _runner(
    *,
    sdd_dir: Path,
    enabled: bool = True,
    promote: bool = False,
    hook: _RecordingRunHook | None = None,
    decision_log_path: Path | None = None,
    recurrence_path: Path | None = None,
    recurrence_threshold: int = tier3.DEFAULT_RECURRENCE_THRESHOLD,
) -> tuple[Tier3Runner, _RecordingRunHook]:
    hook = hook if hook is not None else _RecordingRunHook()
    config = Tier3Config(
        enabled=enabled,
        promote_from_shadow=promote,
        openrouter_base_url="https://openrouter.example.invalid/api/v1",
        recurrence_threshold=recurrence_threshold,
    )
    tracker = RecurrenceTracker(
        path=recurrence_path if recurrence_path is not None else sdd_dir / "autoheal" / "recurrence.jsonl",
        window_seconds=tier3.DEFAULT_RECURRENCE_WINDOW_SECONDS,
        threshold=recurrence_threshold,
    )
    runner = Tier3Runner(
        config=config,
        run_hook=hook,
        sdd_dir=sdd_dir,
        recurrence_tracker=tracker,
        decision_log_path=decision_log_path,
    )
    return runner, hook


@pytest.fixture(autouse=True)
def _enable_decision_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the decision-log writer is enabled for every test.

    The writer reads ``BERNSTEIN_DECISION_LOG`` from the process env;
    a stray ``=0`` from a parent shell would silence every assertion
    below. We pin it to ``1`` explicitly.
    """
    monkeypatch.setenv(dl.ENV_DISABLE, "1")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_flag_off_no_op_writes_nothing(tmp_path: Path) -> None:
    """When ``BERNSTEIN_CI_SELF_DRIVE`` is unset, Tier-3 must be inert.

    No filesystem writes under ``.sdd/``, no decision-log entries, no
    hook invocation. The runner does not even consult the recurrence
    tracker because the flag-off branch fires before any state is
    touched.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    hook = _RecordingRunHook(queued=[RunResult(patch=_diff_for("typos.toml"), model_used="x")])
    runner, _ = _runner(
        sdd_dir=sdd,
        enabled=False,
        hook=hook,
        decision_log_path=decision_path,
    )

    outcome = runner.run(_ctx())

    assert outcome.kind == "flag_off"
    assert hook.calls == []
    assert not sdd.exists(), "tier3 must not touch .sdd/ when disabled"
    assert not decision_path.exists(), "tier3 must not write decision log when disabled"


def test_tier2_produced_patch_skips_tier3(tmp_path: Path) -> None:
    """Tier-2 already produced a patch - Tier-3 does not run.

    The hook must not be invoked, no patch must be persisted, and the
    outcome kind reflects the short-circuit.
    """
    sdd = tmp_path / "sdd"
    hook = _RecordingRunHook(queued=[RunResult(patch=_diff_for("typos.toml"), model_used="x")])
    runner, _ = _runner(sdd_dir=sdd, hook=hook)

    outcome = runner.run(_ctx(tier2_produced_patch=True))

    assert outcome.kind == "tier2_produced_patch"
    assert hook.calls == []
    assert not (sdd / "autoheal" / "tier3-shadow").exists()


def test_unsafe_failure_class_is_rejected_before_run_hook(tmp_path: Path) -> None:
    """Failure classes outside the workflow allowlist must be refused.

    The cordon-zone question is governance, not engineering; Tier-3
    refuses to make the call rather than widen the cordon.
    """
    sdd = tmp_path / "sdd"
    hook = _RecordingRunHook(queued=[RunResult(patch=_diff_for("typos.toml"), model_used="x")])
    runner, _ = _runner(sdd_dir=sdd, hook=hook)

    outcome = runner.run(_ctx(failure_class="Coverage drift"))

    assert outcome.kind == "unsafe_class"
    assert hook.calls == []


def test_shadow_capture_writes_patch_envelope_decision_lineage(
    tmp_path: Path,
) -> None:
    """Happy path: capture lands diff + envelope + decision + lineage.

    Asserts the lineage payload carries the full lineage-v2 child body
    shape required by the acceptance criteria (``failed_run_id``,
    ``tier=3``, ``model``, ``cost_usd``, ``patch_sha``,
    ``regression_test_sha``).
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _diff_for("typos.toml", body="+typo: fixiation")
    hook = _RecordingRunHook(
        queued=[
            RunResult(
                patch=patch,
                model_used=DEFAULT_PRIMARY_MODEL,
                cost_usd=0.0,
                meta={"confidence": 0.71},
            )
        ]
    )
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "shadow_captured"
    assert outcome.model_used == DEFAULT_PRIMARY_MODEL
    assert outcome.cost_usd == 0.0
    assert outcome.patch_sha, "patch_sha must be populated on capture"

    # Persisted diff.
    diff_path = sdd / "autoheal" / "tier3-shadow" / "987654321.diff"
    assert diff_path.read_text(encoding="utf-8") == patch

    # Envelope ledger row.
    envelope_path = sdd / "autoheal" / "ci-autoheal-envelope.jsonl"
    envelope_rows = [json.loads(line) for line in envelope_path.read_text(encoding="utf-8").splitlines()]
    assert len(envelope_rows) == 1
    envelope_row = envelope_rows[0]
    assert envelope_row["quota_envelope"] == "ci-autoheal"
    assert envelope_row["model"] == DEFAULT_PRIMARY_MODEL
    assert envelope_row["cost_usd"] == 0.0
    assert envelope_row["daily_hard_cap_usd"] == 0.0

    # Recurrence ledger row.
    recurrence_path = sdd / "autoheal" / "recurrence.jsonl"
    recurrence_rows = [json.loads(line) for line in recurrence_path.read_text(encoding="utf-8").splitlines()]
    assert len(recurrence_rows) == 1
    assert recurrence_rows[0]["failure_class"] == "Lint"
    assert recurrence_rows[0]["failing_test_nodeid"] == "tests/unit/test_a.py::test_z"

    # Decision log entry - kind=tier3_shadow with the right inputs.
    records = dl.replay(decision_path)
    assert len(records) == 1
    record = records[0]
    assert record.kind == "tier3_shadow"
    assert record.chosen == DEFAULT_PRIMARY_MODEL
    assert record.inputs["failed_run_id"] == "987654321"
    assert record.inputs["patch_sha"] == outcome.patch_sha
    assert record.inputs["quota_envelope"] == "ci-autoheal"
    assert record.inputs["promoted"] is False

    # Lineage payload - matches the AutohealLineagePayload shape.
    lineage = outcome.lineage_payload
    assert lineage["failed_run_id"] == "987654321"
    assert lineage["tier"] == 3
    assert lineage["model"] == DEFAULT_PRIMARY_MODEL
    assert lineage["cost_usd"] == 0.0
    assert lineage["patch_sha"] == outcome.patch_sha
    assert lineage["regression_test_sha"] == "deadbeef" * 5
    assert lineage["strategy"] == "tier3_openrouter_shadow"
    assert lineage["outcome"] == "shadow_captured"
    assert lineage["quota_envelope"] == "ci-autoheal"


def test_cordon_violation_rejected_with_decision_log_kind(tmp_path: Path) -> None:
    """Patch touching paths outside the cordon must be rejected.

    The runner emits a ``cordon_violation`` decision-log entry, drops
    the patch (no diff persisted, no recurrence row), and surfaces the
    offending paths on the outcome so the workflow can react.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    # The patch touches src/bernstein/core/server.py (non-whitespace
    # change - would only be cordon-allowed under a whitespace-only
    # diff) which is exactly the kind of touch the cordon must refuse.
    patch = _diff_for("src/bernstein/core/server.py", body="+raise Boom")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used="x", cost_usd=0.0)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "cordon_violation"
    assert outcome.rejected_paths == ("src/bernstein/core/server.py",)
    # No persisted diff and no recurrence row when the cordon refuses.
    assert not (sdd / "autoheal" / "tier3-shadow" / "987654321.diff").exists()
    assert not (sdd / "autoheal" / "recurrence.jsonl").exists()
    # Decision-log entry has the right kind.
    records = dl.replay(decision_path)
    assert len(records) == 1
    assert records[0].kind == "cordon_violation"
    assert "src/bernstein/core/server.py" in records[0].inputs["rejected_paths"]


def test_cordon_violation_deletion_outside_cordon(tmp_path: Path) -> None:
    """A pure deletion outside the cordon must trip a violation.

    The patch's new-side header is ``+++ /dev/null``; only the old-side
    ``--- a/<path>`` carries the file the patch would delete. The
    runner must surface that old-side path and reject the capture.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _deletion_diff_for("src/bernstein/core/server.py")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used="x", cost_usd=0.0)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "cordon_violation"
    assert outcome.rejected_paths == ("src/bernstein/core/server.py",)
    assert not (sdd / "autoheal" / "tier3-shadow" / "987654321.diff").exists()
    records = dl.replay(decision_path)
    assert len(records) == 1
    assert records[0].kind == "cordon_violation"
    # Decision log records the offending old-side path verbatim.
    assert "src/bernstein/core/server.py" in records[0].inputs["rejected_paths"]
    assert "src/bernstein/core/server.py" in records[0].inputs["touched_paths"]


def test_cordon_violation_deletion_within_cordon_passes(tmp_path: Path) -> None:
    """A pure deletion of a cordoned file must NOT trip a violation.

    The deletion is a destructive operation, but the cordon's purpose
    is to scope the blast radius, not to forbid deletion outright. A
    deletion of e.g. ``typos.toml`` (cordon-allowed) should land just
    like an addition or in-place edit.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _deletion_diff_for("typos.toml")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used=DEFAULT_PRIMARY_MODEL)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "shadow_captured"
    assert outcome.rejected_paths == ()


def test_cordon_violation_rename_out_of_cordon(tmp_path: Path) -> None:
    """A rename that moves a cordoned file outside the cordon trips a violation.

    Old side (``typos.toml``) passes the cordon; new side
    (``src/bernstein/core/server.py``) does not. Without the rename
    detection in ``extract_paths_from_unified_diff`` the patch would
    have been accepted because only the new side would have been
    inspected, then a follow-up patch could touch the renamed-out file
    while it sits outside the cordon.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _rename_diff("typos.toml", "src/bernstein/core/server.py")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used="x", cost_usd=0.0)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "cordon_violation"
    # Rejection surfaces the new-side path that left the cordon.
    assert "src/bernstein/core/server.py" in outcome.rejected_paths
    records = dl.replay(decision_path)
    assert len(records) == 1
    assert records[0].kind == "cordon_violation"
    # Both sides of the rename are recorded under touched_paths.
    touched = records[0].inputs["touched_paths"]
    assert "typos.toml" in touched
    assert "src/bernstein/core/server.py" in touched


def test_cordon_violation_rename_into_cordon(tmp_path: Path) -> None:
    """A rename that moves a file INTO the cordon also trips a violation.

    Old side (``src/bernstein/core/server.py``) is out-of-cordon, even
    though the new side is cordon-allowed. Without inspecting the old
    side, a Tier-3 patch could rename an arbitrary file into the
    cordon and then mutate it freely.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _rename_diff("src/bernstein/core/server.py", "typos.toml")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used="x", cost_usd=0.0)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "cordon_violation"
    assert "src/bernstein/core/server.py" in outcome.rejected_paths
    records = dl.replay(decision_path)
    assert records[0].kind == "cordon_violation"
    assert "src/bernstein/core/server.py" in records[0].inputs["rejected_paths"]


def test_cordon_rename_within_cordon_passes(tmp_path: Path) -> None:
    """A rename where both sides are inside the cordon must NOT trip.

    Both ``typos.toml`` and ``AGENTS.md`` are cordon-allowed root
    files; renaming one into the other is a legal Tier-3 edit.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _rename_diff("typos.toml", "AGENTS.md")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used=DEFAULT_PRIMARY_MODEL)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "shadow_captured"
    assert outcome.rejected_paths == ()


def test_cordon_violation_permits_tier3_yaml_extension(tmp_path: Path) -> None:
    """Tier-3 must accept ``tests/contract/contracts/*.yaml``.

    The standard auto-heal cordon does not enumerate those files; the
    Tier-3 extra-glob set picks them up so contract drift fixtures can
    be regenerated in shadow mode.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _diff_for("tests/contract/contracts/cli.yaml", body="+new: clause")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used=DEFAULT_PRIMARY_MODEL)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx(failure_class="Test (contract-drift)"))

    assert outcome.kind == "shadow_captured"
    assert outcome.rejected_paths == ()


def test_recurrence_threshold_escalates_to_tier4(tmp_path: Path) -> None:
    """The Nth+1 capture inside the window flips to recurrence_escalation.

    With the default threshold of 2, the third capture (count > 2)
    must escalate. We pre-seed the recurrence ledger with two rows for
    the same ``(class, nodeid)`` pair and assert the next ``run`` does
    not invoke the hook and emits the escalation kind.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    recurrence_path = sdd / "autoheal" / "recurrence.jsonl"
    recurrence_path.parent.mkdir(parents=True, exist_ok=True)
    # Two pre-existing captures inside the window - current capture is
    # the third.
    for i in range(3):
        recurrence_path.open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "ts": 1.0 + i,
                    "failure_class": "Lint",
                    "failing_test_nodeid": "tests/unit/test_a.py::test_z",
                    "failed_run_id": f"prev-{i}",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    hook = _RecordingRunHook(queued=[RunResult(patch=_diff_for("typos.toml"), model_used=DEFAULT_PRIMARY_MODEL)])
    runner, _ = _runner(
        sdd_dir=sdd,
        hook=hook,
        decision_log_path=decision_path,
        recurrence_path=recurrence_path,
        recurrence_threshold=tier3.DEFAULT_RECURRENCE_THRESHOLD,
    )
    # Pin the clock so the pre-seeded rows fall inside the 24h window.
    runner.clock = lambda: 5.0

    outcome = runner.run(_ctx())

    assert outcome.kind == "recurrence_escalated"
    assert hook.calls == [], "hook must not be invoked once the threshold is breached"
    records = dl.replay(decision_path)
    assert len(records) == 1
    assert records[0].kind == "recurrence_escalation"
    assert records[0].chosen == "tier4_handoff"
    assert records[0].inputs["recent_count"] >= 3


def test_run_hook_429_falls_back_to_next_model(tmp_path: Path) -> None:
    """The fallback list semantics are exercised by the hook contract.

    The runner does not orchestrate the fallback walk itself - that is
    the hook's job (so the actual provider quirks stay testable in
    one place). The hook contract surface here just asserts that the
    primary + fallback list is passed through and that the
    ``model_used`` returned by the hook (a fallback) is what lands in
    the decision-log entry and the envelope row.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    fallback_winner = DEFAULT_FALLBACK_MODELS[1]
    patch = _diff_for("typos.toml")
    hook = _RecordingRunHook(
        queued=[
            RunResult(
                patch=patch,
                model_used=fallback_winner,
                cost_usd=0.0,
                meta={"primary_429": True, "fallback_index": 1},
            )
        ]
    )
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "shadow_captured"
    assert outcome.model_used == fallback_winner
    # Hook must receive the full fallback list.
    assert hook.calls[0]["primary_model"] == DEFAULT_PRIMARY_MODEL
    assert tuple(hook.calls[0]["fallback_models"]) == DEFAULT_FALLBACK_MODELS

    # Decision-log entry records the winner.
    records = dl.replay(decision_path)
    assert records[0].chosen == fallback_winner
    assert records[0].inputs["model_used"] == fallback_winner
    assert records[0].inputs["primary_model"] == DEFAULT_PRIMARY_MODEL

    # Envelope row attributes spend to the fallback model.
    envelope_rows = [
        json.loads(line)
        for line in (sdd / "autoheal" / "ci-autoheal-envelope.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert envelope_rows[0]["model"] == fallback_winner


def test_shadow_empty_when_hook_returns_no_patch(tmp_path: Path) -> None:
    """An empty hook result must not write any sidecar artefacts.

    The classification still flows through (failure class is in the
    allowlist) but the run produced nothing, so we must not persist a
    patch, an envelope row, or a recurrence row.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    hook = _RecordingRunHook(queued=[RunResult(patch="", model_used="qwen", cost_usd=0.0)])
    runner, _ = _runner(sdd_dir=sdd, hook=hook, decision_log_path=decision_path)

    outcome = runner.run(_ctx())

    assert outcome.kind == "shadow_empty"
    assert not (sdd / "autoheal" / "tier3-shadow").exists()
    assert not (sdd / "autoheal" / "ci-autoheal-envelope.jsonl").exists()
    assert not (sdd / "autoheal" / "recurrence.jsonl").exists()
    assert not decision_path.exists()


def test_promote_from_shadow_flag_flips_kind(tmp_path: Path) -> None:
    """The promotion gate is off by default; setting it flips the kind.

    The actual push is the workflow's job; the runner only signals
    the intent via the ``promoted_push`` outcome kind.
    """
    sdd = tmp_path / "sdd"
    decision_path = tmp_path / "decisions.jsonl"
    patch = _diff_for("typos.toml")
    hook = _RecordingRunHook(queued=[RunResult(patch=patch, model_used=DEFAULT_PRIMARY_MODEL)])
    runner, _ = _runner(
        sdd_dir=sdd,
        hook=hook,
        promote=True,
        decision_log_path=decision_path,
    )

    outcome = runner.run(_ctx())

    assert outcome.kind == "promoted_push"
    records = dl.replay(decision_path)
    assert len(records) == 1
    assert records[0].inputs["promoted"] is True


# ---------------------------------------------------------------------------
# Config / pure-function tests
# ---------------------------------------------------------------------------


def test_tier3_config_from_env_defaults_disabled() -> None:
    """A clean env yields a disabled, shadow-only config."""
    config = Tier3Config.from_env(env={})
    assert config.enabled is False
    assert config.promote_from_shadow is False
    assert config.primary_model == DEFAULT_PRIMARY_MODEL
    assert config.fallback_models == DEFAULT_FALLBACK_MODELS
    assert config.daily_hard_cap_usd == 0.0


def test_tier3_config_from_env_enables_on_tier3_value() -> None:
    """``BERNSTEIN_CI_SELF_DRIVE=tier3`` flips the enabled bit."""
    config = Tier3Config.from_env(
        env={
            "BERNSTEIN_CI_SELF_DRIVE": "tier3",
            "BERNSTEIN_CI_SELF_DRIVE_PROMOTE_FROM_SHADOW": "1",
            "BERNSTEIN_OPENROUTER_BASE_URL": "https://openrouter.example.invalid/api/v1",
        }
    )
    assert config.enabled is True
    assert config.promote_from_shadow is True
    assert config.openrouter_base_url == "https://openrouter.example.invalid/api/v1"


def test_tier3_config_rejects_other_self_drive_values() -> None:
    """Tier-3 stays off when the env var holds anything else."""
    config = Tier3Config.from_env(env={"BERNSTEIN_CI_SELF_DRIVE": "tier2"})
    assert config.enabled is False


def test_tier3_config_falls_back_to_openai_base_url() -> None:
    """``OPENAI_BASE_URL`` is honoured when the Tier-3 var is unset."""
    config = Tier3Config.from_env(
        env={
            "BERNSTEIN_CI_SELF_DRIVE": "tier3",
            "OPENAI_BASE_URL": "https://openrouter.example.invalid/api/v1",
        }
    )
    assert config.openrouter_base_url == "https://openrouter.example.invalid/api/v1"


def test_tier3_config_daily_hard_cap_override() -> None:
    """Operator may raise the cap above zero for paid fallbacks."""
    config = Tier3Config.from_env(
        env={
            "BERNSTEIN_CI_SELF_DRIVE": "tier3",
            "BERNSTEIN_CI_AUTOHEAL_HARD_CAP_USD": "0.75",
        }
    )
    assert config.daily_hard_cap_usd == pytest.approx(0.75)


def test_tier3_config_ignores_negative_hard_cap() -> None:
    """A negative cap is dropped; the default zero stays in force."""
    config = Tier3Config.from_env(
        env={
            "BERNSTEIN_CI_SELF_DRIVE": "tier3",
            "BERNSTEIN_CI_AUTOHEAL_HARD_CAP_USD": "-1.0",
        }
    )
    assert config.daily_hard_cap_usd == 0.0


def test_extract_paths_from_unified_diff_single_file() -> None:
    """One path is extracted per ``+++ b/...`` header."""
    diff = _diff_for("typos.toml")
    assert extract_paths_from_unified_diff(diff) == ("typos.toml",)


def test_extract_paths_from_unified_diff_multi_file() -> None:
    """Multi-file diffs surface every touched path in order."""
    diff = _diff_for("typos.toml") + _diff_for("AGENTS.md", body="+ entry")
    paths = extract_paths_from_unified_diff(diff)
    assert paths == ("typos.toml", "AGENTS.md")


def test_extract_paths_from_unified_diff_collects_deletion_old_side() -> None:
    """A pure deletion must surface the old-side ``--- a/<path>``.

    Without this the cordon sees only ``/dev/null`` on the new side and
    a Tier-3 patch could delete an arbitrary out-of-cordon file.
    """
    diff = _deletion_diff_for("src/bernstein/core/server.py")
    paths = extract_paths_from_unified_diff(diff)
    assert paths == ("src/bernstein/core/server.py",)


def test_extract_paths_from_unified_diff_collects_both_sides_of_rename() -> None:
    """A rename must surface both the old-side and new-side paths.

    Without the old side, a cordoned file could be renamed out of the
    cordon (the new path passes; the old path is invisible) and a
    follow-up patch would then bypass the cordon entirely.
    """
    diff = _rename_diff("typos.toml", "src/bernstein/core/server.py")
    paths = extract_paths_from_unified_diff(diff)
    assert paths == ("typos.toml", "src/bernstein/core/server.py")


def test_recurrence_tracker_ignores_old_rows(tmp_path: Path) -> None:
    """Captures outside the window do not contribute to the threshold."""
    path = tmp_path / "recurrence.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Two captures, both older than the window.
    old_ts = 100.0
    for _ in range(3):
        path.open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "ts": old_ts,
                    "failure_class": "Lint",
                    "failing_test_nodeid": "tests/x.py::test_y",
                    "failed_run_id": "old",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    tracker = RecurrenceTracker(path=path, window_seconds=60.0, threshold=1)
    # Window is 60s, now is 1000s - the old rows are out of band.
    assert (
        tracker.count_recent(
            failure_class="Lint",
            failing_test_nodeid="tests/x.py::test_y",
            now=1000.0,
        )
        == 0
    )
    assert not tracker.should_escalate(
        failure_class="Lint",
        failing_test_nodeid="tests/x.py::test_y",
        now=1000.0,
    )


def test_decision_log_kind_validation_accepts_tier3_kinds(tmp_path: Path) -> None:
    """The new kinds are accepted by ``record_decision`` directly.

    Belt-and-braces guard: even if a future refactor stops calling the
    helper via ``Tier3Runner``, the kinds themselves must remain part
    of the closed-set vocabulary.
    """
    decision_path = tmp_path / "decisions.jsonl"
    for kind in ("tier3_shadow", "cordon_violation", "recurrence_escalation"):
        os.environ.setdefault(dl.ENV_DISABLE, "1")
        rec = dl.record_decision(
            kind=kind,
            chosen="x",
            rationale="t",
            confidence=0.5,
            inputs={"failed_run_id": "1"},
            path=decision_path,
        )
        assert rec is not None
        assert rec.kind == kind
