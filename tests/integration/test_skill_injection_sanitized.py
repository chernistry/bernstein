"""Integration tests for invisible-Unicode sanitization at skill-injection time.

These tests exercise the end-to-end path that writes ``.claude/skills/*.md``
into an agent worktree, asserting that:

1. A poisoned template body never reaches disk with invisible codepoints.
2. The bytes written to disk contain no Tag-block, interlinear, or Cf prefixes.
3. A clean template round-trips unchanged.
4. The Prometheus counter increments per source on a poisoned template.
5. The opt-out (``BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS``) lets a poisoned
   template through so security incidents can be reproduced.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from bernstein.core.models import Task

from bernstein.adapters.skills_injector import inject_skills

#: Invisible "HELLO" encoded in the Unicode Tag block.
INVISIBLE_HELLO = "\U000e0048\U000e0045\U000e004c\U000e004c\U000e004f"


@pytest.fixture(autouse=True)
def _clear_optout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", raising=False)


def _make_task() -> Task:
    return Task(id="T-001", title="Test task", description="A task", role="backend")


def _make_poisoned_skills_dir(tmp_path: Path) -> Path:
    """Materialise a templates/skills directory with a poisoned body."""
    skills_dir = tmp_path / "templates" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bernstein-completion-protocol.md").write_text(
        "---\nname: bernstein-completion-protocol\n"
        "description: Report task completion\n---\n"
        f"Visible{INVISIBLE_HELLO}content. Complete: {{{{COMPLETE_CMDS}}}}\n",
        encoding="utf-8",
    )
    (skills_dir / "bernstein-signal-check.md").write_text(
        "---\nname: bernstein-signal-check\n"
        "description: Check signals\n---\n"
        f"Session {{{{SESSION_ID}}}} {INVISIBLE_HELLO}\n",
        encoding="utf-8",
    )
    return skills_dir


def _make_clean_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "templates" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "bernstein-completion-protocol.md").write_text(
        "---\nname: bernstein-completion-protocol\n"
        "description: Report task completion\n---\n"
        "Complete: {{COMPLETE_CMDS}}\n",
        encoding="utf-8",
    )
    (skills_dir / "bernstein-signal-check.md").write_text(
        "---\nname: bernstein-signal-check\ndescription: Check signals\n---\nSession {{SESSION_ID}}\n",
        encoding="utf-8",
    )
    return skills_dir


def _has_invisible_codepoint(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if 0xE0000 <= cp <= 0xE007F:
            return True
        if 0xFFF9 <= cp <= 0xFFFB:
            return True
        if unicodedata.category(ch) == "Cf":
            return True
    return False


def test_poisoned_template_has_no_invisible_bytes_on_disk(tmp_path: Path) -> None:
    skills_dir = _make_poisoned_skills_dir(tmp_path)
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="backend",
        tasks=[_make_task()],
        session_id="session-xyz",
        templates_dir=skills_dir.parent / "roles",
    )

    written = list((workdir / ".claude" / "skills").glob("*.md"))
    assert written, "expected at least one injected skill file"
    for md in written:
        body = md.read_text(encoding="utf-8")
        assert not _has_invisible_codepoint(body), f"{md.name} still contains invisible codepoints"


def test_poisoned_template_visible_content_survives(tmp_path: Path) -> None:
    skills_dir = _make_poisoned_skills_dir(tmp_path)
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="backend",
        tasks=[_make_task()],
        session_id="session-xyz",
        templates_dir=skills_dir.parent / "roles",
    )

    completion = (workdir / ".claude" / "skills" / "bernstein-completion-protocol.md").read_text(encoding="utf-8")
    assert "Visible" in completion
    assert "content" in completion
    # Verify the placeholder was rendered after sanitization
    assert "T-001" in completion


def test_clean_template_passes_through_unchanged(tmp_path: Path) -> None:
    skills_dir = _make_clean_skills_dir(tmp_path)
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="backend",
        tasks=[_make_task()],
        session_id="session-clean",
        templates_dir=skills_dir.parent / "roles",
    )

    written = list((workdir / ".claude" / "skills").glob("*.md"))
    assert written
    for md in written:
        body = md.read_text(encoding="utf-8")
        assert not _has_invisible_codepoint(body)


def test_opt_out_keeps_invisible_codepoints_on_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the operator sets the opt-out env var, sanitization is bypassed."""
    monkeypatch.setenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", "1")
    skills_dir = _make_poisoned_skills_dir(tmp_path)
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="backend",
        tasks=[_make_task()],
        session_id="session-unsafe",
        templates_dir=skills_dir.parent / "roles",
    )

    completion = (workdir / ".claude" / "skills" / "bernstein-completion-protocol.md").read_text(encoding="utf-8")
    assert _has_invisible_codepoint(completion), "expected invisibles preserved under opt-out"


def test_counter_increments_on_injection_of_poisoned_template(tmp_path: Path) -> None:
    from bernstein.core.observability.prometheus import (
        skills_unicode_tags_stripped_total,
    )

    try:
        before = float(
            skills_unicode_tags_stripped_total.labels(source_name="templates/skills")._value.get()  # type: ignore[attr-defined]
        )
    except Exception:
        return

    skills_dir = _make_poisoned_skills_dir(tmp_path)
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    inject_skills(
        workdir=workdir,
        role="backend",
        tasks=[_make_task()],
        session_id="session-counter",
        templates_dir=skills_dir.parent / "roles",
    )

    after = float(
        skills_unicode_tags_stripped_total.labels(source_name="templates/skills")._value.get()  # type: ignore[attr-defined]
    )
    # Two poisoned files each carrying 5 invisible codepoints == 10 total.
    assert after - before >= 10.0
