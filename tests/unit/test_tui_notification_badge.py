"""Tests for TUI-020: Notification badge for background events."""

from __future__ import annotations

from bernstein.tui.notification_badge import BadgeTracker, NotificationHistory, render_notification_center


class TestBadgeTracker:
    def test_initial_zero(self) -> None:
        tracker = BadgeTracker()
        assert tracker.get_count("tasks") == 0
        assert tracker.get_count("logs") == 0

    def test_increment(self) -> None:
        tracker = BadgeTracker()
        tracker.increment("tasks")
        tracker.increment("tasks")
        assert tracker.get_count("tasks") == 2

    def test_clear_panel(self) -> None:
        tracker = BadgeTracker()
        tracker.increment("tasks")
        tracker.increment("tasks")
        tracker.clear("tasks")
        assert tracker.get_count("tasks") == 0

    def test_clear_does_not_affect_other(self) -> None:
        tracker = BadgeTracker()
        tracker.increment("tasks")
        tracker.increment("logs")
        tracker.clear("tasks")
        assert tracker.get_count("logs") == 1

    def test_clear_all(self) -> None:
        tracker = BadgeTracker()
        tracker.increment("tasks")
        tracker.increment("logs")
        tracker.clear_all()
        assert tracker.get_count("tasks") == 0
        assert tracker.get_count("logs") == 0

    def test_format_badge_zero(self) -> None:
        tracker = BadgeTracker()
        assert tracker.format_badge("tasks") == ""

    def test_format_badge_nonzero(self) -> None:
        tracker = BadgeTracker()
        tracker.increment("tasks")
        tracker.increment("tasks")
        tracker.increment("tasks")
        assert tracker.format_badge("tasks") == "[3 new]"

    def test_format_badge_alert(self) -> None:
        tracker = BadgeTracker()
        tracker.set_alert("logs")
        assert tracker.format_badge("logs") == "[!]"

    def test_alert_cleared_with_clear(self) -> None:
        tracker = BadgeTracker()
        tracker.set_alert("logs")
        tracker.clear("logs")
        assert tracker.format_badge("logs") == ""

    def test_has_unread(self) -> None:
        tracker = BadgeTracker()
        assert tracker.has_unread() is False
        tracker.increment("tasks")
        assert tracker.has_unread() is True

    def test_focused_panel_ignored(self) -> None:
        tracker = BadgeTracker()
        tracker.set_focused("tasks")
        tracker.increment("tasks")
        assert tracker.get_count("tasks") == 0


class TestNotificationHistory:
    def test_tracks_unread_count_until_acknowledged(self) -> None:
        history = NotificationHistory()

        history.add("Task finished", level="success", timestamp=10.0)
        history.add("Budget warning", level="warning", timestamp=20.0)

        assert history.get_unread_count() == 2

        history.mark_all_read()

        assert history.get_unread_count() == 0

    def test_returns_newest_first(self) -> None:
        history = NotificationHistory()

        history.add("older", timestamp=10.0)
        history.add("newer", timestamp=20.0)

        assert [record.message for record in history.get_history()] == ["newer", "older"]


class TestRenderNotificationCenter:
    def test_empty_state(self) -> None:
        rendered = render_notification_center([], unread_count=0)
        plain = rendered.plain

        assert "Notifications" in plain
        assert "0 unread" in plain
        assert "No notifications yet." in plain

    def test_renders_unread_entries(self) -> None:
        history = NotificationHistory()
        history.add("Task finished", level="success", timestamp=10.0)
        history.add("Agent stalled", level="warning", timestamp=20.0)
        rendered = render_notification_center(history.get_history(), unread_count=history.get_unread_count())
        plain = rendered.plain

        assert "2 unread" in plain
        assert "new WARN" in plain
        assert "Agent stalled" in plain
