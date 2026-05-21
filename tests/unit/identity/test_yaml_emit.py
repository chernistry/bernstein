"""Tests for the install-rev fingerprint yaml emit slot.

Covers two yaml-render call sites:

* :func:`bernstein.cli.commands.init_wizard_cmd.generate_yaml` - the
  ``bernstein init`` wizard's bernstein.yaml output.
* :func:`bernstein.core.workflows.workflow_spec.render_blank_template`
  - the ``bernstein workflow init`` scaffolded manifest body.

For each: round-trip emit-then-decode with a real seed, kill-switch
suppress, operator-seed-unset suppress, and the
``IDENTITY_EMISSION_ENABLED`` gate.
"""

from __future__ import annotations

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
    TOKEN_LEN,
    _compute_token,
)
from bernstein.core.workflows.workflow_spec import render_blank_template

# ---------------------------------------------------------------------------
# Fixtures - mirror tests/unit/identity/test_install_rev.py for consistency
# ---------------------------------------------------------------------------

TEST_SEED_HEX = "01" * 32
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


@pytest.fixture(autouse=True)
def _reset_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin nonce path + clear cache + disable emission by default."""
    nonce_path = tmp_path / "install_nonce"
    monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
    monkeypatch.delenv(ENV_DISABLE, raising=False)
    monkeypatch.delenv(ENV_SEED, raising=False)
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", False)
    ir._reset_cache_for_tests()


def _enable_emission(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nonce: bytes | None = None,
    nonce_path: Path | None = None,
) -> str:
    """Helper: turn emission on with a deterministic seed and return the token.

    Writes the supplied ``nonce`` to the path pinned by
    ``BERNSTEIN_IDENTITY_NONCE_PATH`` so the produced token is
    reproducible from ``(seed, nonce, version_major)``.
    """
    monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
    monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
    if nonce is not None:
        # The autouse fixture pins ENV_NONCE_PATH; we resolve it here so
        # both helpers and assertions point at the same path.
        path = nonce_path or Path(ir._nonce_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(nonce)
    ir._reset_cache_for_tests()
    return _compute_token(bytes.fromhex(TEST_SEED_HEX), nonce or TEST_NONCE, ir._version_byte())


# ---------------------------------------------------------------------------
# generate_yaml - bernstein init wizard
# ---------------------------------------------------------------------------


class TestGenerateYamlEmit:
    """``generate_yaml`` (bernstein init) yaml comment emit slot."""

    def _yaml(self) -> str:
        return generate_yaml(
            goal="Test goal",
            project_type="python",
            max_agents=3,
            budget=5.0,
            adapter="auto",
            approval="auto",
        )

    def test_no_comment_when_emission_disabled(self) -> None:
        body = self._yaml()
        assert "bernstein-rev:" not in body

    def test_no_comment_when_kill_switch_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Arrange - emission gate ON, kill switch ON; result must still
        # suppress (kill switch wins, never emits the sentinel either).
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        body = self._yaml()
        assert "bernstein-rev:" not in body
        assert DISABLED_SENTINEL not in body

    def test_no_comment_when_seed_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Emission gate ON but no seed available - every emit site must
        # short-circuit, no comment in output.
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        body = self._yaml()
        assert "bernstein-rev:" not in body

    def test_emits_real_token_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce=TEST_NONCE, nonce_path=nonce_path)

        body = self._yaml()
        assert f"# bernstein-rev: {expected}" in body
        # Lands as the very first non-blank line - survival rate is best
        # when the marker reaches the head of the file.
        assert body.lstrip().startswith(f"# bernstein-rev: {expected}")
        # Token shape sanity.
        assert len(expected) == TOKEN_LEN
        assert expected != DISABLED_SENTINEL

    def test_round_trip_decode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce=TEST_NONCE, nonce_path=nonce_path)
        body = self._yaml()

        # Extract the token from the rendered yaml and confirm the
        # operator's verify_with_nonce reproduces it bit-for-bit.
        for line in body.splitlines():
            if line.startswith("# bernstein-rev:"):
                emitted = line.split(":", 1)[1].strip()
                break
        else:
            pytest.fail("yaml output is missing the bernstein-rev comment")

        assert emitted == expected
        assert ir.verify_with_nonce(emitted, TEST_NONCE, version_major=ir._version_byte()) is True


# ---------------------------------------------------------------------------
# render_blank_template - bernstein workflow init
# ---------------------------------------------------------------------------


class TestWorkflowBlankTemplateEmit:
    """``render_blank_template`` (bernstein workflow init) yaml emit slot."""

    def test_no_comment_when_emission_disabled(self) -> None:
        body = render_blank_template("idea-to-pr")
        assert "bernstein-rev:" not in body

    def test_no_comment_when_kill_switch_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_DISABLE, "1")
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        body = render_blank_template("idea-to-pr")
        assert "bernstein-rev:" not in body
        assert DISABLED_SENTINEL not in body

    def test_no_comment_when_seed_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        ir._reset_cache_for_tests()

        body = render_blank_template("idea-to-pr")
        assert "bernstein-rev:" not in body

    def test_emits_real_token_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        expected = _enable_emission(monkeypatch, nonce=TEST_NONCE, nonce_path=nonce_path)

        body = render_blank_template("idea-to-pr")
        # First line carries the rev comment so the operator's gh
        # search hits the head of the file consistently.
        assert body.startswith(f"# bernstein-rev: {expected}\n")

    def test_template_still_parses_with_rev_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # The init command's safety net is "scaffolded template must
        # round-trip through the parser".  Confirm the yaml parser still
        # accepts the body when we prefix a comment.
        from bernstein.core.workflows.workflow_spec import (
            load_workflow_spec_from_text,
        )

        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        _enable_emission(monkeypatch, nonce=TEST_NONCE, nonce_path=nonce_path)

        body = render_blank_template("idea-to-pr")
        spec = load_workflow_spec_from_text(body)
        assert spec.name == "idea-to-pr"
