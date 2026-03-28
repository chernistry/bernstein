"""Custom Textual widgets for the Bernstein TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.widgets import DataTable, RichLog, Static

# ---------------------------------------------------------------------------
# Colour mapping for task statuses
# ---------------------------------------------------------------------------

STATUS_COLORS: dict[str, str] = {
    "open": "white",
    "claimed": "cyan",
    "in_progress": "yellow",
    "done": "green",
    "failed": "red",
    "blocked": "dim",
    "cancelled": "dim",
}


def status_color(status: str) -> str:
    """Return the Rich colour name for a given task status string.

    Args:
        status: Task status value (e.g. "open", "done").

    Returns:
        Rich colour name suitable for markup.
    """
    return STATUS_COLORS.get(status, "white")


# ---------------------------------------------------------------------------
# Task data helper
# ---------------------------------------------------------------------------


@dataclass
class TaskRow:
    """Parsed row for the task list table.

    Attributes:
        task_id: Unique task identifier.
        status: Current task status string.
        role: Agent role assigned to the task.
        title: Human-readable task title.
    """

    task_id: str
    status: str
    role: str
    title: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TaskRow:
        """Build a TaskRow from a task-server JSON dict.

        Args:
            raw: Dictionary as returned by GET /tasks.

        Returns:
            Parsed TaskRow instance.
        """
        return cls(
            task_id=str(raw.get("id", "")),
            status=str(raw.get("status", "open")),
            role=str(raw.get("role", "")),
            title=str(raw.get("title", "")),
        )


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class TaskListWidget(DataTable[Text]):
    """DataTable showing tasks with colour-coded status."""

    def on_mount(self) -> None:
        """Set up columns when the widget is mounted."""
        self.add_columns("ID", "Status", "Role", "Title")
        self.cursor_type = "row"

    def refresh_tasks(self, rows: list[TaskRow]) -> None:
        """Replace all rows with fresh task data.

        Args:
            rows: Parsed task rows to display.
        """
        self.clear()
        for row in rows:
            colour = status_color(row.status)
            self.add_row(
                Text(row.task_id, style="bold"),
                Text(row.status, style=colour),
                Text(row.role, style="cyan"),
                Text(row.title),
                key=row.task_id,
            )


class AgentLogWidget(RichLog):
    """Scrollable log output for agent activity."""

    def append_line(self, line: str) -> None:
        """Append a single line to the log.

        Args:
            line: Text line to append.
        """
        self.write(line)


class StatusBar(Static):
    """Summary bar: agents active, tasks done/total, cost."""

    def set_summary(
        self,
        *,
        agents_active: int = 0,
        tasks_done: int = 0,
        tasks_total: int = 0,
        tasks_failed: int = 0,
        cost_usd: float = 0.0,
        elapsed_seconds: float = 0.0,
        server_online: bool = True,
    ) -> None:
        """Update the status bar content.

        Args:
            agents_active: Number of active agents.
            tasks_done: Number of completed tasks.
            tasks_total: Total number of tasks.
            tasks_failed: Number of failed tasks.
            cost_usd: Total cost in USD.
            elapsed_seconds: Elapsed wall-clock seconds.
            server_online: Whether the task server is reachable.
        """
        minutes = int(elapsed_seconds) // 60
        seconds = int(elapsed_seconds) % 60
        elapsed_str = f"{minutes}m{seconds:02d}s"

        if not server_online:
            self.update(Text("Server offline — waiting for connection...", style="bold red"))
            return

        parts: list[str] = [
            f"[bold]Agents:[/bold] {agents_active}",
            f"[bold]Tasks:[/bold] {tasks_done}/{tasks_total}",
        ]
        if tasks_failed:
            parts.append(f"[red]Failed: {tasks_failed}[/red]")
        parts.append(f"[bold]Cost:[/bold] ${cost_usd:.2f}")
        parts.append(f"[bold]Elapsed:[/bold] {elapsed_str}")

        self.update(Text.from_markup("  |  ".join(parts)))
