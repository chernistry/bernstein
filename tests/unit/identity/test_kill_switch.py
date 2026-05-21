"""End-to-end kill-switch sweep across every install-rev emit site.

A privacy-paranoid user who sets ``BERNSTEIN_DISABLE_IDENTITY=1`` must
see zero ``bernstein-rev:`` strings in **every** artefact bernstein
produces - yaml, trace JSONL, role prompt md.  This file is the
single regression catch for the public-facing kill-switch contract.

The same sweep is repeated for the operator-side gate
(``IDENTITY_EMISSION_ENABLED=False``) so the default-off landing state
is also locked in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.cli.commands.init_wizard_cmd import generate_yaml
from bernstein.core.identity import install_rev as ir
from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    ENV_DISABLE,
    ENV_NONCE_PATH,
    ENV_SEED,
    NONCE_BYTES,
)
from bernstein.core.observability.traces import (
    AgentTrace,
    TraceStep,
    TraceStore,
)
from bernstein.core.workflows.workflow_spec import render_blank_template
from bernstein.templates.renderer import render_role_prompt

TEST_SEED_HEX = "01" * 32
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


@pytest.fixture()
def role_templates_dir(tmp_path: Path) -> Path:
    role_dir = tmp_path / "manager"
    role_dir.mkdir()
    (role_dir / "system_prompt.md").write_text("# Manager\nGoal: {{GOAL}}\n")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nonce_path = tmp_path / "install_nonce"
    monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
    monkeypatch.delenv(ENV_DISABLE, raising=False)
    monkeypatch.delenv(ENV_SEED, raising=False)
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", False)
    ir._reset_cache_for_tests()


def _exercise_every_emit_site(
    *,
    role_templates_dir: Path,
    traces_dir: Path,
) -> dict[str, str]:
    """Hit every emit site once and return the rendered artefacts.

    Returns:
        Dict keyed by slot name (``yaml-init``, ``yaml-workflow``,
        ``trace-jsonl``, ``role-prompt``) with the on-disk / in-memory
        payload as the value.  Each is the exact bytes a downstream
        consumer would observe.
    """
    yaml_init = generate_yaml(
        goal="Test goal",
        project_type="python",
        max_agents=3,
        budget=5.0,
        adapter="auto",
        approval="auto",
    )
    yaml_workflow = render_blank_template("idea-to-pr")

    store = TraceStore(traces_dir)
    store.write(
        AgentTrace(
            trace_id="abcdef1234567890",
            session_id="s",
            task_ids=["t-1"],
            agent_role="backend",
            model="sonnet",
            effort="high",
            spawn_ts=1.0,
            steps=[TraceStep(type="spawn", timestamp=1.0)],
        )
    )
    trace_jsonl = (traces_dir / "t-1.jsonl").read_text()

    role_prompt = render_role_prompt(
        "manager",
        {"GOAL": "ship"},
        templates_dir=role_templates_dir,
    )

    return {
        "yaml-init": yaml_init,
        "yaml-workflow": yaml_workflow,
        "trace-jsonl": trace_jsonl,
        "role-prompt": role_prompt,
    }


def _assert_no_rev_anywhere(artefacts: dict[str, str]) -> None:
    """Loud assertion that every artefact is scrubbed of rev markers."""
    for slot, payload in artefacts.items():
        assert "bernstein-rev:" not in payload, f"{slot} leaks bernstein-rev:"
        assert DISABLED_SENTINEL not in payload, f"{slot} leaks the sentinel"
        # Trace jsonl gets a stricter check - no _rev key in any line.
        if slot == "trace-jsonl":
            for line in payload.splitlines():
                if not line:
                    continue
                assert "_rev" not in json.loads(line), f"{slot} carries _rev field"


# ---------------------------------------------------------------------------
# The two off-states a user can be in
# ---------------------------------------------------------------------------


class TestKillSwitchSuppressesEverywhere:
    """Every kill-switch path must zero-out every emit site."""

    def test_default_off_state_emits_nothing(
        self,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        # IDENTITY_EMISSION_ENABLED=False (the default at module load).
        artefacts = _exercise_every_emit_site(
            role_templates_dir=role_templates_dir,
            traces_dir=tmp_path / "traces",
        )
        _assert_no_rev_anywhere(artefacts)

    def test_user_kill_switch_overrides_emission_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        # Operator opted in (gate ON) and seed is configured, but the
        # user set ``BERNSTEIN_DISABLE_IDENTITY=1``.  User wins.
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        nonce_path.parent.mkdir(parents=True, exist_ok=True)
        nonce_path.write_bytes(TEST_NONCE)
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        monkeypatch.setenv(ENV_DISABLE, "1")
        ir._reset_cache_for_tests()

        artefacts = _exercise_every_emit_site(
            role_templates_dir=role_templates_dir,
            traces_dir=tmp_path / "traces",
        )
        _assert_no_rev_anywhere(artefacts)

    def test_seed_missing_emits_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        # Operator gate ON but the user has no seed (the realistic state
        # for every end-user install - they never have the operator's
        # secret).  Every emit site short-circuits to a no-op rather
        # than spelling the sentinel into the public artefact.
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        artefacts = _exercise_every_emit_site(
            role_templates_dir=role_templates_dir,
            traces_dir=tmp_path / "traces",
        )
        _assert_no_rev_anywhere(artefacts)
