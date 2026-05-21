"""Tests for the install-rev fingerprint role-prompt md emit slot.

``render_role_prompt`` appends an HTML-comment footer
``<!-- bernstein-rev: <token> -->`` to every rendered system prompt
when emission is on.

Covers:

* default off-state - no footer.
* kill-switch / seed-missing - no footer.
* live emit - footer present at the very end, exactly one line.
* prefix preservation - bytes before the footer are unchanged from the
  pre-wiring rendering (KV-cache locality requirement from design doc).
* round-trip decode - operator can verify the emitted token at HMAC
  strength when they hold the user's nonce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.identity import install_rev as ir
from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    ENV_DISABLE,
    ENV_NONCE_PATH,
    ENV_SEED,
    NONCE_BYTES,
    _compute_token,
)
from bernstein.templates.renderer import render_role_prompt

TEST_SEED_HEX = "01" * 32
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


@pytest.fixture()
def role_templates_dir(tmp_path: Path) -> Path:
    """Build a minimal role template fixture for prompt rendering."""
    role_dir = tmp_path / "backend"
    role_dir.mkdir()
    (role_dir / "system_prompt.md").write_text("# Backend\nGoal: {{GOAL}}\nNo trailing newline body - sentinel.")
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


def _enable_emission(monkeypatch: pytest.MonkeyPatch, nonce_path: Path) -> str:
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
    monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
    nonce_path.parent.mkdir(parents=True, exist_ok=True)
    nonce_path.write_bytes(TEST_NONCE)
    ir._reset_cache_for_tests()
    return _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, ir._version_byte())


# ---------------------------------------------------------------------------
# Suppression paths
# ---------------------------------------------------------------------------


class TestPromptEmitSuppression:
    """Default off-state and every kill switch must yield no footer."""

    def test_no_footer_when_emission_disabled(self, role_templates_dir: Path) -> None:
        rendered = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )
        assert "bernstein-rev:" not in rendered
        assert "<!--" not in rendered

    def test_no_footer_when_kill_switch_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        rendered = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )
        assert "bernstein-rev:" not in rendered
        assert DISABLED_SENTINEL not in rendered

    def test_no_footer_when_seed_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        rendered = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )
        assert "bernstein-rev:" not in rendered


# ---------------------------------------------------------------------------
# Live emit + prefix preservation
# ---------------------------------------------------------------------------


class TestPromptEmitLive:
    """When emission is on, the footer lands and the prefix is preserved."""

    def test_footer_present_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce_path)

        rendered = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )
        assert rendered.rstrip("\n").endswith(f"<!-- bernstein-rev: {expected} -->")
        # Exactly one rev line - no duplicate emit on re-render.
        assert rendered.count("bernstein-rev:") == 1

    def test_prefix_unchanged_vs_baseline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        # Baseline render with emission off.
        baseline = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )

        # Live render with emission on.
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        _enable_emission(monkeypatch, nonce_path)
        live = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )

        # Baseline is exactly the prefix of live; everything appended
        # lives strictly after.  This is the KV-cache locality contract.
        assert live.startswith(baseline)
        suffix = live[len(baseline) :]
        assert "bernstein-rev:" in suffix
        # Suffix is a markdown comment + newline, nothing else.
        stripped = suffix.strip()
        assert stripped.startswith("<!--")
        assert stripped.endswith("-->")

    def test_round_trip_decode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        role_templates_dir: Path,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce_path)

        rendered = render_role_prompt(
            "backend",
            {"GOAL": "Add JWT"},
            templates_dir=role_templates_dir,
        )
        # Pull the token out of the footer and verify_with_nonce.
        last = rendered.rstrip("\n").splitlines()[-1]
        # Format: "<!-- bernstein-rev: <token> -->"
        between = last.split("bernstein-rev:", 1)[1]
        emitted = between.removesuffix("-->").strip()
        assert emitted == expected
        assert ir.verify_with_nonce(emitted, TEST_NONCE, version_major=ir._version_byte()) is True
