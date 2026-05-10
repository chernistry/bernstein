"""Tests for ``bernstein identity`` cli (show / decode / verify / disable)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.identity_cmd import identity_group
from bernstein.core.identity import install_rev as ir
from bernstein.core.identity.install_rev import (
    DISABLED_SENTINEL,
    ENV_DISABLE,
    ENV_NONCE_PATH,
    ENV_SEED,
    NONCE_BYTES,
    _compute_token,
)

TEST_SEED_HEX = "01" * 32
TEST_NONCE = bytes.fromhex("0123456789abcdef0123")
assert len(TEST_NONCE) == NONCE_BYTES


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


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShowCmd:
    def test_show_returns_sentinel_when_emission_disabled(self, runner: CliRunner) -> None:
        result = runner.invoke(identity_group, ["show"])
        assert result.exit_code == 0
        assert result.stdout.strip() == DISABLED_SENTINEL
        assert "emission disabled" in result.stderr

    def test_show_returns_token_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        nonce_path = tmp_path / "install_nonce"
        monkeypatch.setenv(ENV_NONCE_PATH, str(nonce_path))
        nonce_path.parent.mkdir(parents=True, exist_ok=True)
        nonce_path.write_bytes(TEST_NONCE)
        monkeypatch.setattr(ir, "IDENTITY_EMISSION_ENABLED", True)
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        ir._reset_cache_for_tests()

        result = runner.invoke(identity_group, ["show"])
        expected = _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, 1)
        assert result.exit_code == 0
        assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# decode
# ---------------------------------------------------------------------------


class TestDecodeCmd:
    def test_decode_valid_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        token = _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, 1)

        result = runner.invoke(identity_group, ["decode", token])
        assert result.exit_code == 0
        assert result.stdout.strip() == "valid"

    def test_decode_sentinel_is_invalid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        result = runner.invoke(identity_group, ["decode", DISABLED_SENTINEL])
        assert result.exit_code == 1
        assert result.stdout.strip() == "invalid"

    def test_decode_seed_missing_exits_2(
        self,
        runner: CliRunner,
    ) -> None:
        result = runner.invoke(identity_group, ["decode", "abcdefghij234567"])
        assert result.exit_code == 2
        assert "seed missing" in result.stderr


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerifyCmd:
    def test_verify_with_correct_nonce(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        token = _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, 1)

        result = runner.invoke(
            identity_group,
            [
                "verify",
                token,
                "--nonce",
                TEST_NONCE.hex(),
                "--version-major",
                "1",
            ],
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "valid"

    def test_verify_with_wrong_nonce_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)
        token = _compute_token(bytes.fromhex(TEST_SEED_HEX), TEST_NONCE, 1)
        wrong_nonce = bytes(NONCE_BYTES)  # all zeros, deterministically wrong

        result = runner.invoke(
            identity_group,
            ["verify", token, "--nonce", wrong_nonce.hex()],
        )
        assert result.exit_code == 1
        assert result.stdout.strip() == "invalid"

    def test_verify_rejects_bad_nonce_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        result = runner.invoke(
            identity_group,
            ["verify", "abcdefghij234567", "--nonce", "01"],
        )
        assert result.exit_code == 1
        assert "must be" in result.stderr

    def test_verify_rejects_non_hex_nonce(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runner: CliRunner,
    ) -> None:
        monkeypatch.setenv(ENV_SEED, TEST_SEED_HEX)

        result = runner.invoke(
            identity_group,
            ["verify", "abcdefghij234567", "--nonce", "zzzz"],
        )
        assert result.exit_code == 1
        assert "invalid --nonce hex" in result.stderr


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


class TestDisableCmd:
    def test_disable_prints_export_line(self, runner: CliRunner) -> None:
        result = runner.invoke(identity_group, ["disable"])
        assert result.exit_code == 0
        assert result.stdout.strip() == "export BERNSTEIN_DISABLE_IDENTITY=1"
