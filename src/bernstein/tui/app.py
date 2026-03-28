"""Main Textual application for the Bernstein TUI session manager."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from bernstein.tui.widgets import AgentLogWidget, StatusBar, TaskListWidget, TaskRow

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVER_URL = os.environ.get("BERNSTEIN_SERVER_URL", "http://localhost:8052")
_POLL_INTERVAL: float = 2.0


def _auth_headers() -> dict[str, str]:
    """Return Authorization header dict if BERNSTEIN_AUTH_TOKEN is set.

    Returns:
        Header dict, possibly empty.
    """
    token = os.environ.get("BERNSTEIN_AUTH_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _get(path: str) -> dict[str, Any] | list[Any] | None:
    """HTTP GET from the task server.

    Args:
        path: URL path (e.g. "/status").

    Returns:
        Parsed JSON, or None when the server is unreachable.
    """
    try:
        resp = httpx.get(f"{SERVER_URL}{path}", timeout=5.0, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
    except (httpx.ConnectError, httpx.TimeoutException):
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

CSS_PATH = "styles.tcss"


class BernsteinApp(App[None]):
    """Textual TUI for monitoring a Bernstein orchestration session."""

    TITLE = "Bernstein"
    CSS_PATH = CSS_PATH

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("tab", "focus_next", "Switch focus"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, *, poll_interval: float = _POLL_INTERVAL) -> None:
        """Initialise the app.

        Args:
            poll_interval: Seconds between task-server polls.
        """
        super().__init__()
        self._poll_interval = poll_interval
        self._start_ts = time.time()

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        yield Header()
        yield StatusBar(id="top-bar")
        with Horizontal(id="main-content"):
            yield TaskListWidget(id="task-list")
            yield AgentLogWidget(id="agent-log")
        yield Footer()

    def on_mount(self) -> None:
        """Start the periodic poll timer after mounting."""
        self.set_interval(self._poll_interval, self._poll_server)
        # Fire an immediate poll so the UI is populated straight away.
        self.call_later(self._poll_server)

    # -- actions --------------------------------------------------------------

    def action_refresh(self) -> None:
        """Force an immediate server poll (bound to 'r')."""
        self._poll_server()

    # -- data fetching --------------------------------------------------------

    def _poll_server(self) -> None:
        """Fetch data from the task server and update widgets."""
        status_bar = self.query_one("#top-bar", StatusBar)
        task_list = self.query_one("#task-list", TaskListWidget)
        log_widget = self.query_one("#agent-log", AgentLogWidget)

        status_raw = _get("/status")
        if status_raw is None or not isinstance(status_raw, dict):
            status_bar.set_summary(server_online=False)
            return

        tasks_raw = _get("/tasks")
        tasks: list[dict[str, Any]] = (
            [t for t in tasks_raw if isinstance(t, dict)] if isinstance(tasks_raw, list) else []
        )

        # Parse tasks
        rows = [TaskRow.from_api(t) for t in tasks]
        task_list.refresh_tasks(rows)

        # Agent count from agents.json
        agents_active = self._count_active_agents()

        elapsed = time.time() - self._start_ts
        status_bar.set_summary(
            agents_active=agents_active,
            tasks_done=int(status_raw.get("done", 0)),
            tasks_total=int(status_raw.get("total", 0)),
            tasks_failed=int(status_raw.get("failed", 0)),
            cost_usd=float(status_raw.get("total_cost_usd", 0.0)),
            elapsed_seconds=elapsed,
            server_online=True,
        )

        # Append recent task completions / failures to the log
        self._update_log(log_widget, tasks)

    @staticmethod
    def _count_active_agents() -> int:
        """Read agent count from the orchestrator's agents.json file.

        Returns:
            Number of non-dead agents.
        """
        agents_json = Path(".sdd/runtime/agents.json")
        if not agents_json.exists():
            return 0
        try:
            data = json.loads(agents_json.read_text())
            agents: list[dict[str, Any]] = data.get("agents", [])
            return sum(1 for a in agents if a.get("status") != "dead")
        except (OSError, ValueError, KeyError):
            return 0

    def _update_log(self, log_widget: AgentLogWidget, tasks: list[dict[str, Any]]) -> None:
        """Write recent task events to the agent log widget.

        Shows the most recent task transitions as log entries.

        Args:
            log_widget: The RichLog widget to append to.
            tasks: Raw task dicts from the server.
        """
        # Show progress_log entries from tasks that have them
        for task in tasks:
            progress: list[dict[str, Any]] = task.get("progress_log", [])
            if not progress:
                continue
            # Show the most recent log entry per task
            last = progress[-1]
            msg = last.get("message", "")
            task_id = task.get("id", "?")
            status = task.get("status", "open")
            if msg:
                log_widget.append_line(f"[{status}] {task_id}: {msg}")
