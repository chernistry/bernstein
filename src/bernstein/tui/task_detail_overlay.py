"""TUI-018 / UX-006: Task detail overlay with tabbed sections.

Full-screen overlay showing task status+result at top (evidence-first),
followed by tabbed sections: Summary, Diff, Gates, Logs, Deps.
Keybindings 1-5 switch between tabs.  Escape/q to close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.screen import ModalScreen
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

_MAX_LOG_LINES = 50


class DetailTab(IntEnum):
    """Available tabs in the task detail overlay."""

    SUMMARY = 1
    DIFF = 2
    GATES = 3
    LOGS = 4
    DEPS = 5


#: Human labels for each tab, used in the tab bar.
TAB_LABELS: dict[DetailTab, str] = {
    DetailTab.SUMMARY: "Summary",
    DetailTab.DIFF: "Diff",
    DetailTab.GATES: "Gates",
    DetailTab.LOGS: "Logs",
    DetailTab.DEPS: "Deps",
}


@dataclass
class TaskDetail:
    """All data needed to render a task detail overlay.

    Attributes:
        task_id: Unique task identifier.
        title: Task title.
        description: Full task description.
        status: Current task status.
        role: Assigned role.
        agent_id: Assigned agent session ID.
        cost_usd: Cost incurred so far.
        result: Final result summary (e.g. "completed", error message).
        log_tail: Last N lines of agent log.
        diff_preview: Git diff preview string.
        quality_results: Quality gate results mapping.
        dependencies: List of dependency task IDs.
        blocked_by: List of task IDs that block this task.
    """

    task_id: str
    title: str
    description: str
    status: str
    role: str
    agent_id: str | None = None
    cost_usd: float | None = None
    result: str = ""
    log_tail: list[str] = field(default_factory=list)
    diff_preview: str = ""
    quality_results: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)


def _render_header(detail: TaskDetail) -> str:
    """Render the evidence-first header: status + result at top.

    Args:
        detail: Task detail data.

    Returns:
        Formatted header string.
    """
    lines: list[str] = []
    lines.append(f"{'=' * 60}")
    lines.append(f"  Status: {detail.status}  |  Task: {detail.task_id}")
    if detail.result:
        lines.append(f"  Result: {detail.result}")
    lines.append(f"  Title: {detail.title}")
    lines.append(f"  Role: {detail.role}")
    if detail.agent_id:
        lines.append(f"  Agent: {detail.agent_id}")
    if detail.cost_usd is not None:
        lines.append(f"  Cost: ${detail.cost_usd:.2f}")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def _render_tab_bar(active: DetailTab) -> str:
    """Render a tab bar showing which tab is active.

    Args:
        active: Currently selected tab.

    Returns:
        Formatted tab bar string.
    """
    parts: list[str] = []
    for tab in DetailTab:
        label = TAB_LABELS[tab]
        if tab == active:
            parts.append(f"[{tab.value}] >> {label} <<")
        else:
            parts.append(f"[{tab.value}] {label}")
    return "  ".join(parts)


def _render_summary(detail: TaskDetail) -> str:
    """Render the Summary tab content.

    Args:
        detail: Task detail data.

    Returns:
        Formatted summary string.
    """
    lines: list[str] = []
    if detail.description:
        lines.append("--- Description ---")
        lines.append(detail.description)
    else:
        lines.append("[No description]")
    return "\n".join(lines)


def _render_diff(detail: TaskDetail) -> str:
    """Render the Diff tab content.

    Args:
        detail: Task detail data.

    Returns:
        Formatted diff preview string.
    """
    if detail.diff_preview:
        return f"--- Diff Preview ---\n{detail.diff_preview}"
    return "[No diff available]"


def _render_gates(detail: TaskDetail) -> str:
    """Render the Gates tab content.

    Args:
        detail: Task detail data.

    Returns:
        Formatted quality gates string.
    """
    if not detail.quality_results:
        return "[No quality gate results]"
    lines: list[str] = ["--- Quality Gates ---"]
    for gate, result in detail.quality_results.items():
        icon = "pass" if result == "pass" else "FAIL"
        lines.append(f"  [{icon}] {gate}")
    return "\n".join(lines)


def _render_logs(detail: TaskDetail) -> str:
    """Render the Logs tab content.

    Args:
        detail: Task detail data.

    Returns:
        Formatted log tail string.
    """
    if not detail.log_tail:
        return "[No log entries]"
    lines: list[str] = ["--- Recent Log ---"]
    tail = detail.log_tail[-_MAX_LOG_LINES:]
    lines.extend(tail)
    return "\n".join(lines)


def _render_deps(detail: TaskDetail) -> str:
    """Render the Deps tab content.

    Args:
        detail: Task detail data.

    Returns:
        Formatted dependency information string.
    """
    lines: list[str] = []
    if detail.dependencies:
        lines.append("--- Dependencies ---")
        for dep in detail.dependencies:
            lines.append(f"  -> {dep}")
    if detail.blocked_by:
        lines.append("--- Blocked By ---")
        for blocker in detail.blocked_by:
            lines.append(f"  !! {blocker}")
    if not lines:
        return "[No dependencies]"
    return "\n".join(lines)


#: Tab renderers keyed by tab enum value.
_TAB_RENDERERS: dict[DetailTab, object] = {
    DetailTab.SUMMARY: _render_summary,
    DetailTab.DIFF: _render_diff,
    DetailTab.GATES: _render_gates,
    DetailTab.LOGS: _render_logs,
    DetailTab.DEPS: _render_deps,
}


def render_tab_content(detail: TaskDetail, tab: DetailTab) -> str:
    """Render the content for a specific tab.

    Args:
        detail: Task detail data.
        tab: Which tab to render.

    Returns:
        Formatted content string for the selected tab.
    """
    renderer = _TAB_RENDERERS.get(tab, _render_summary)
    return renderer(detail)  # type: ignore[operator]


def format_task_detail(detail: TaskDetail, tab: DetailTab = DetailTab.SUMMARY) -> str:
    """Format task detail into a display string with header + active tab.

    Args:
        detail: Task detail data.
        tab: Currently active tab.

    Returns:
        Formatted multi-line string for display.
    """
    sections: list[str] = []
    sections.append(_render_header(detail))
    sections.append("")
    sections.append(_render_tab_bar(tab))
    sections.append("")
    sections.append(render_tab_content(detail, tab))
    return "\n".join(sections)


class TaskDetailScreen(ModalScreen[None]):
    """Full-screen modal overlay for task detail view.

    Evidence-first layout: status+result at top, then tabbed sections.
    Keys 1-5 switch tabs (Summary/Diff/Gates/Logs/Deps).
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Close", show=True),
        Binding("q", "dismiss", "Close", show=False),
        Binding("1", "tab_1", "Summary", show=True),
        Binding("2", "tab_2", "Diff", show=True),
        Binding("3", "tab_3", "Gates", show=True),
        Binding("4", "tab_4", "Logs", show=True),
        Binding("5", "tab_5", "Deps", show=True),
    ]

    DEFAULT_CSS = """
    TaskDetailScreen {
        align: center middle;
    }
    TaskDetailScreen > Static {
        width: 90%;
        height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        overflow-y: auto;
    }
    """

    def __init__(self, detail: TaskDetail) -> None:
        """Initialize with task detail data.

        Args:
            detail: Task detail to display.
        """
        super().__init__()
        self._detail = detail
        self._active_tab: DetailTab = DetailTab.SUMMARY

    def compose(self) -> ComposeResult:
        """Build the overlay content."""
        yield Static(
            format_task_detail(self._detail, self._active_tab),
            id="task-detail-content",
        )

    def _switch_tab(self, tab: DetailTab) -> None:
        """Switch to a specific tab and re-render.

        Args:
            tab: The tab to switch to.
        """
        self._active_tab = tab
        content = self.query_one("#task-detail-content", Static)
        content.update(format_task_detail(self._detail, self._active_tab))

    def action_tab_1(self) -> None:
        """Switch to Summary tab."""
        self._switch_tab(DetailTab.SUMMARY)

    def action_tab_2(self) -> None:
        """Switch to Diff tab."""
        self._switch_tab(DetailTab.DIFF)

    def action_tab_3(self) -> None:
        """Switch to Gates tab."""
        self._switch_tab(DetailTab.GATES)

    def action_tab_4(self) -> None:
        """Switch to Logs tab."""
        self._switch_tab(DetailTab.LOGS)

    def action_tab_5(self) -> None:
        """Switch to Deps tab."""
        self._switch_tab(DetailTab.DEPS)

    @property
    def active_tab(self) -> DetailTab:
        """Return the currently active tab."""
        return self._active_tab

    async def action_dismiss(self, result: None = None) -> None:
        """Close the overlay."""
        self.dismiss()
