"""Smoke test for the hot-reload watcher (#1720)."""

from __future__ import annotations

import textwrap
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.core.skills.watcher import start_skill_watcher

if TYPE_CHECKING:
    from bernstein.core.skills.loader import SkillLoader


def _write_skill(root: Path, name: str, *, body: str = "Body.") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""
            ---
            name: {name}
            description: Sample skill used by the watcher hot reload smoke test cases.
            ---

            # {name}

            {body}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_watcher_fires_on_file_change(tmp_path: Path) -> None:
    """Authoring a new skill triggers a debounced rebuild."""
    watch_path = tmp_path / "skills"
    watch_path.mkdir()
    _write_skill(watch_path, "alpha")

    reload_count = 0
    loaders_seen: list[SkillLoader] = []
    reload_event = threading.Event()
    lock = threading.Lock()

    def on_reload(loader: SkillLoader) -> None:
        nonlocal reload_count
        with lock:
            reload_count += 1
            loaders_seen.append(loader)
        reload_event.set()

    handle = start_skill_watcher(watch_path, on_reload)
    try:
        # Give the observer thread a moment to attach inotify/kqueue.
        time.sleep(0.1)
        _write_skill(watch_path, "beta")
        # Poll until the rebuilt loader sees ``beta`` (handles late-arriving
        # events on platforms where the directory is created before the
        # file inside it is flushed).
        deadline = time.monotonic() + 5.0
        saw_beta = False
        # Pre-declare ``names`` so the final assertion stays defined even
        # if the loop body never executes under extreme scheduling stalls.
        names: set[str] = set()
        while time.monotonic() < deadline:
            reload_event.wait(timeout=0.5)
            reload_event.clear()
            with lock:
                names = set()
                for loader in loaders_seen:
                    names.update(skill.name for skill in loader.list_all())
            if "beta" in names:
                saw_beta = True
                break
    finally:
        handle.stop(timeout=2.0)

    assert reload_count >= 1, "watcher never invoked the reload callback"
    assert saw_beta, f"watcher saw {names} but expected to observe 'beta'"
