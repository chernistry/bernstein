"""Regression tests for the frontend smoke-test bug sweep.

These tests pin the fixes applied to the 10 P0-P4 bugs found by the
2026-05-15 end-to-end GUI smoke run.  Each test name maps to a bug number
in ``.sdd/backlog/closed/2026-05-15-frontend-followup-from-smoke.md`` so
future failures can be cross-referenced.

The scope is narrow on purpose: each test exercises a single regression
without standing up the full bootstrap or GUI dev server, so this file
runs fast and never touches the network.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bernstein.core.git.git_basic import remote_exists, safe_push
from bernstein.core.server.server_app import create_app
from bernstein.core.server.server_models import TaskCountsResponse
from bernstein.core.tasks.models import TaskStatus

# Bug #1: --idle propagates adapter to orchestrator subprocess.


class TestBug1IdleAdapterPropagation:
    """``_start_spawner`` must accept an explicit adapter override and the
    orchestrator __main__ block must honour ``BERNSTEIN_ADAPTER`` from env.
    """

    def test_start_spawner_accepts_adapter(self) -> None:
        from bernstein.core.server.server_launch import _start_spawner

        sig = inspect.signature(_start_spawner)
        assert "adapter" in sig.parameters, (
            "_start_spawner must accept an ``adapter`` kw so the bootstrap can "
            "pin the orchestrator subprocess to the resolved cli (mock for --idle)"
        )

    def test_orchestrator_main_reads_bernstein_adapter_env(self) -> None:
        """The orchestrator __main__ block reads ``BERNSTEIN_ADAPTER`` as the
        argparse default so a forgotten ``--adapter`` flag does not silently
        fall back to ``claude``.
        """
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "core" / "orchestration" / "orchestrator.py"
        text = path.read_text(encoding="utf-8")
        assert 'os.environ.get("BERNSTEIN_ADAPTER"' in text, (
            "orchestrator.py __main__ block must read BERNSTEIN_ADAPTER env so "
            "--idle (which sets cli=mock) actually reaches the spawned subprocess"
        )

    def test_run_bootstrap_idle_exports_adapter_env(self) -> None:
        """``bernstein run --idle`` must export ``BERNSTEIN_ADAPTER`` so the
        orchestrator subprocess does not silently spawn Claude.
        """
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "cli" / "run_bootstrap.py"
        text = path.read_text(encoding="utf-8")
        assert 'os.environ["BERNSTEIN_ADAPTER"]' in text, (
            "--idle block in run_bootstrap.py must export BERNSTEIN_ADAPTER "
            "so the orchestrator subprocess argparse default picks up mock"
        )


# Bug #2: plan_file bypasses prior-session resume.


class TestBug2PlanFileBypassesPriorSession:
    """When the operator passes ``plan_file``, the prior-session resume
    check must NOT swallow the explicit task list.  Source inspection is
    enough here — the full bootstrap path needs a server, server takes
    >2 s to spin up, and this is a code-level invariant.
    """

    def test_goal_sync_skips_prior_session_when_tasks_provided(self) -> None:
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "core" / "orchestration" / "bootstrap.py"
        text = path.read_text(encoding="utf-8")
        # The fix replaces the bare ``check_resume_session(...)`` call with a
        # conditional that short-circuits on a provided ``tasks`` list.
        assert "None if tasks else check_resume_session" in text, (
            "_goal_sync_and_plan must bypass check_resume_session() when an "
            "explicit ``tasks`` list is passed in (typically from --plan_file)"
        )


# Bug #3: /openapi.json must return 200 with valid JSON.


@pytest.fixture()
def app(tmp_path: Path) -> FastAPI:
    return create_app(jsonl_path=tmp_path / "tasks.jsonl")


@pytest.fixture()
def client(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with auth disabled so the /openapi.json route is reachable."""
    monkeypatch.setenv("BERNSTEIN_AUTH_DISABLED", "1")
    return TestClient(app)


class TestBug3OpenAPIJson:
    """``GET /openapi.json`` must return 200 with a well-formed schema."""

    def test_openapi_json_returns_200(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200, resp.text[:500]

    def test_openapi_json_payload_is_well_formed(self, client: TestClient) -> None:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        payload = resp.json()
        # Minimum FastAPI/OpenAPI 3 contract.
        assert isinstance(payload, dict)
        assert payload.get("openapi", "").startswith("3."), payload.get("openapi")
        assert "paths" in payload and isinstance(payload["paths"], dict)
        assert "components" in payload and isinstance(payload["components"], dict)
        # Sanity: at least one real route is documented (e.g. /tasks/counts).
        assert "/tasks/counts" in payload["paths"], (
            "OpenAPI schema is missing /tasks/counts — schema gen likely regressed under a future Pydantic update"
        )


# Bug #4: TaskCountsResponse exposes every TaskStatus value.


class TestBug4TaskCountsAllStatuses:
    """The /tasks/counts schema must surface every value in
    :class:`TaskStatus` so the GUI status chips render real numbers.
    """

    REQUIRED_FIELDS = (
        "open",
        "claimed",
        "in_progress",
        "done",
        "closed",
        "failed",
        "blocked",
        "cancelled",
        "planned",
        "pending_approval",
        "waiting_for_subtasks",
        "orphaned",
        "total",
    )

    def test_schema_fields_cover_every_task_status(self) -> None:
        fields = set(TaskCountsResponse.model_fields.keys())
        for required in self.REQUIRED_FIELDS:
            assert required in fields, f"TaskCountsResponse must expose {required!r}"

    def test_schema_fields_cover_every_enum_value(self) -> None:
        """Future-proofing: if someone adds a new TaskStatus and forgets to
        plumb it through, this fails so they remember.
        """
        fields = set(TaskCountsResponse.model_fields.keys())
        for status in TaskStatus:
            assert status.value in fields, (
                f"TaskCountsResponse missing field for TaskStatus.{status.name} "
                f"(value={status.value!r}) — add it so the GUI badge can show "
                f"a real count instead of —"
            )

    def test_default_is_zero_for_every_field(self) -> None:
        instance = TaskCountsResponse()
        for required in self.REQUIRED_FIELDS:
            assert getattr(instance, required) == 0


# Bug #6: safe_push tolerates no remote.


class TestBug6SafePushNoRemote:
    """``safe_push`` against a local-only repo (no origin) must be a clean
    no-op, not a noisy ``fatal: 'origin' does not appear...``.
    """

    def test_remote_exists_false_for_fresh_repo(self, tmp_path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        assert remote_exists(tmp_path) is False

    def test_safe_push_no_op_when_remote_missing(self, tmp_path: Path) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        # Need at least one commit so HEAD resolves — otherwise rev-list errors
        # and we never even get to the push attempt.
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init", "-q"],
            check=True,
            env={
                "PATH": "/usr/bin:/bin",
                "GIT_AUTHOR_NAME": "x",
                "GIT_AUTHOR_EMAIL": "x@x",
                "GIT_COMMITTER_NAME": "x",
                "GIT_COMMITTER_EMAIL": "x@x",
            },
        )
        result = safe_push(tmp_path, "main")
        # Successful no-op: returncode 0 and stderr explains the skip.
        assert result.returncode == 0, result.stderr
        assert "push skipped" in result.stderr or result.ok


# Bug #7: /api/v1/openapi.json redirects to /openapi.json.


class TestBug7ApiV1OpenAPIAlias:
    """``GET /api/v1/openapi.json`` must redirect to ``/openapi.json``
    (some clients hard-code the versioned path).
    """

    def test_api_v1_openapi_redirects(self, client: TestClient) -> None:
        resp = client.get("/api/v1/openapi.json", follow_redirects=False)
        assert resp.status_code in (301, 302, 307, 308)
        assert resp.headers["location"] == "/openapi.json"

    def test_api_v1_openapi_followed_returns_schema(self, client: TestClient) -> None:
        resp = client.get("/api/v1/openapi.json", follow_redirects=True)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("openapi", "").startswith("3.")


# Bug #5: orphan-shell cleanup helpers exist.


class TestBug5OrphanShellCleanup:
    """``_collect_repo_processes`` must do a second pass to kill disowned
    shell/curl loops that survive a regular SIGTERM sweep.  Source-level
    check — exercising the real ps scan would require fork(); keep it light.
    """

    def test_second_pass_implementation_present(self) -> None:
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "cli" / "commands" / "stop_cmd.py"
        text = path.read_text(encoding="utf-8")
        assert "Second pass: orphan-shell cleanup" in text, (
            "stop_cmd._collect_repo_processes must include a second pass that "
            "kills disowned shell/curl loops (the heartbeat / hooks-POST loops "
            "that survived the agent process they were spawned from)"
        )


# Bug #8: --dev mode hint accurately describes the Vite port story.


class TestBug8DevHintAccuracy:
    """The ``bernstein gui serve --dev`` help text must reference the
    actual config source for the Vite port (``vite.config.ts``) rather
    than pinning a single port in the docs.
    """

    def test_dev_help_references_vite_config(self) -> None:
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "gui" / "cli.py"
        text = path.read_text(encoding="utf-8")
        assert "vite.config.ts" in text, (
            "--dev help should reference ``web/vite.config.ts`` as the source "
            "of truth for the Vite port (previously hard-coded :5173 in prose)"
        )

    def test_dev_mode_runtime_hint_mentions_override(self) -> None:
        path = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "gui" / "cli.py"
        text = path.read_text(encoding="utf-8")
        assert "--port" in text, (
            "Dev-mode runtime hint should tell operators how to override the "
            "port (npm run dev -- --port <port>) when the smoke harness uses "
            "a non-default port"
        )


# Bug #9 + #10: AppShell sidebar — sidebar contains Settings + Fleet and
# the FooterBar pluralises agents correctly.


class TestBug9And10AppShell:
    """Source-level check on the TSX so we don't need a JS runtime here."""

    APPSHELL = Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "AppShell.tsx"

    def test_sidebar_includes_settings(self) -> None:
        text = self.APPSHELL.read_text(encoding="utf-8")
        # Sidebar nav uses the NAV array — Settings must appear inside it.
        nav_block_start = text.index("const NAV = [")
        nav_block_end = text.index("] as const;", nav_block_start)
        nav_block = text[nav_block_start:nav_block_end]
        assert "Settings" in nav_block, "Sidebar NAV must include Settings"
        assert "/settings" in nav_block, "Sidebar NAV must route to /settings"

    def test_sidebar_includes_fleet(self) -> None:
        text = self.APPSHELL.read_text(encoding="utf-8")
        nav_block_start = text.index("const NAV = [")
        nav_block_end = text.index("] as const;", nav_block_start)
        nav_block = text[nav_block_start:nav_block_end]
        assert "Fleet" in nav_block, "Sidebar NAV must include Fleet"
        assert "/fleet" in nav_block, "Sidebar NAV must route to /fleet"

    def test_footer_pluralises_agents_correctly(self) -> None:
        text = self.APPSHELL.read_text(encoding="utf-8")
        # The fix uses a conditional so "1 agent" renders for agentsTotal===1.
        assert "agentsTotal === 1" in text, (
            "FooterBar must conditionally render 'agent' vs 'agents' based on "
            "the count (was rendering '1 agents' before)"
        )
