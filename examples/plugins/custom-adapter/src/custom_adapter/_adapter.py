"""Deterministic mock adapter for offline CI / contract tests.

The adapter implements the bernstein :class:`CLIAdapter` interface but
deliberately bypasses ``subprocess.Popen`` — it writes a canned NDJSON
stream-json transcript to the session log path and synthesises a
fast-exit ``Popen``-shape that the orchestrator can poll.

This means the orchestrator path runs end-to-end (job dispatch, log
consumption, exit-code propagation) without spending a cent on real
Claude calls. The only thing the adapter does NOT exercise is the real
upstream CLI's error-recovery behaviour; for that, use the fake-CLI
harness (``tests/integration/fake_cli/``) instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from bernstein.adapters.base import (
    DEFAULT_TIMEOUT_SECONDS,
    CLIAdapter,
    SpawnResult,
)

if TYPE_CHECKING:
    from bernstein.core.models import ModelConfig


# Default canned response keyed by prompt prefix. Operators can pass a
# custom map at construction time; bernstein's entry-point discovery
# instantiates the adapter without args, so the default map is what
# ``bernstein run --cli claude_mock`` sees out of the box.
_DEFAULT_CANNED: dict[str, str] = {
    "": "claude-mock: deterministic canned response",
}


class ClaudeMockAdapter(CLIAdapter):
    """Offline mock adapter — returns canned stream-json output.

    The adapter never spawns the real ``claude`` binary. Instead it
    writes a deterministic NDJSON transcript to the session log and
    spawns a trivial ``true`` process so the orchestrator's PID-based
    liveness checks have something to poll.

    Attributes:
        canned_responses: Map of prompt-prefix → assistant-text. The
            longest matching key wins; falls back to the empty-string
            key when nothing matches.
    """

    # Mock adapter has no real network endpoints — explicitly empty so
    # the network-policy enforcement helper short-circuits.
    external_endpoints: ClassVar[tuple[tuple[str, int], ...]] = ()

    def __init__(self, *, canned_responses: dict[str, str] | None = None) -> None:
        super().__init__()
        self._canned: dict[str, str] = dict(canned_responses or _DEFAULT_CANNED)
        # Always provide a default so the picker never returns ``None``.
        self._canned.setdefault("", _DEFAULT_CANNED[""])

    def name(self) -> str:
        return "Claude Mock"

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> SpawnResult:
        """Write canned stream-json to the log, return a fast-exit handle."""
        # Suppress unused-arg warnings while keeping the signature compatible
        # with the abstract :class:`CLIAdapter.spawn`.
        _ = mcp_config, task_scope, budget_multiplier, system_addendum

        log_path = workdir / ".sdd" / "runtime" / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        canned_text = self._pick_canned(prompt)
        events: list[dict[str, Any]] = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": session_id,
                "model": model_config.model or "claude-mock",
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": canned_text}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": canned_text,
                "total_cost_usd": 0.0,
                "num_turns": 1,
                "duration_ms": 0,
            },
        ]
        with log_path.open("w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")

        # Fast-exit subprocess so liveness probes resolve immediately.
        # ``sys.executable -c "0"`` works across platforms and bypasses
        # the host's ``true`` / ``false`` differences.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
        result = SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)
        if timeout_seconds > 0:
            result.timeout_timer = self._start_timeout_watchdog(
                proc.pid,
                timeout_seconds,
                session_id,
            )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_canned(self, prompt: str) -> str:
        """Return the canned response for *prompt*.

        Picks the longest matching prefix key; falls back to the empty
        string key (always present) when nothing matches.
        """
        best_key: str = ""
        for key in self._canned:
            if prompt.startswith(key) and len(key) > len(best_key):
                best_key = key
        return self._canned[best_key]
