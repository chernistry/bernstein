"""Tests for ``bernstein interop a2a card|verify`` (AC 1, 4)."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

from click.testing import CliRunner

from bernstein.cli.commands.interop_cmd import interop_group
from bernstein.core.interop.a2a_card import SignedCapabilityCard, card_public_key_fingerprint

if TYPE_CHECKING:
    from pathlib import Path


def _issue(runner: CliRunner, tmp_path: Path, *extra: str) -> Path:
    out = tmp_path / "card.json"
    result = runner.invoke(
        interop_group,
        ["a2a", "card", "--issuer", "acme", "--output", str(out), "--ttl-seconds", "3600", *extra],
    )
    assert result.exit_code == 0, result.output
    return out


def test_card_command_writes_signed_card(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path, "--tool", "task_orchestration", "--tool", "code_review")
    assert out.exists()
    signed = SignedCapabilityCard.from_json(out.read_text())
    assert signed.card.issuer == "acme"
    assert signed.card.advertised_tools == ["task_orchestration", "code_review"]
    assert "BEGIN PUBLIC KEY" in signed.card.public_key_pem


def test_card_command_persists_private_key_0600(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path)
    key_path = out.with_suffix(out.suffix + ".key.pem")
    assert key_path.exists()
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600
    assert b"BEGIN PRIVATE KEY" in key_path.read_bytes()


def test_verify_command_accepts_valid_card(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path)
    result = runner.invoke(interop_group, ["a2a", "verify", "--card", str(out)])
    assert result.exit_code == 0, result.output
    assert "is valid" in result.output


def test_verify_command_rejects_tampered_card(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path)
    doc = json.loads(out.read_text())
    doc["card"]["issuer"] = "evil"
    out.write_text(json.dumps(doc))
    result = runner.invoke(interop_group, ["a2a", "verify", "--card", str(out)])
    assert result.exit_code == 1
    assert "NOT valid" in result.output


def test_verify_command_enforces_trusted_fingerprint(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path)
    signed = SignedCapabilityCard.from_json(out.read_text())
    good_fp = card_public_key_fingerprint(signed.card.public_key_pem)

    # wrong fingerprint -> rejected.
    bad = runner.invoke(interop_group, ["a2a", "verify", "--card", str(out), "--trusted-fingerprint", "sha256:nope"])
    assert bad.exit_code == 1
    assert "trusted-issuer set" in bad.output

    # correct fingerprint -> accepted.
    ok = runner.invoke(interop_group, ["a2a", "verify", "--card", str(out), "--trusted-fingerprint", good_fp])
    assert ok.exit_code == 0, ok.output


def test_verify_command_enforces_policy_requirements(tmp_path: Path) -> None:
    runner = CliRunner()
    out = _issue(runner, tmp_path, "--cost-cap-usd", "50", "--redaction-tier", "basic", "--sandbox-profile", "process")
    result = runner.invoke(
        interop_group,
        [
            "a2a",
            "verify",
            "--card",
            str(out),
            "--require-cost-cap-usd",
            "10",
            "--require-redaction-tier",
            "strict",
            "--require-sandbox-profile",
            "microvm",
        ],
    )
    assert result.exit_code == 1
    assert "cost cap" in result.output
    assert "redaction" in result.output
    assert "sandbox" in result.output


def test_card_command_accepts_existing_private_key(tmp_path: Path) -> None:
    runner = CliRunner()
    first = _issue(runner, tmp_path)
    key_path = first.with_suffix(first.suffix + ".key.pem")
    first_fp = card_public_key_fingerprint(SignedCapabilityCard.from_json(first.read_text()).card.public_key_pem)

    second = tmp_path / "card2.json"
    result = runner.invoke(
        interop_group,
        [
            "a2a",
            "card",
            "--issuer",
            "acme",
            "--output",
            str(second),
            "--private-key",
            str(key_path),
            "--ttl-seconds",
            "3600",
        ],
    )
    assert result.exit_code == 0, result.output
    second_fp = card_public_key_fingerprint(SignedCapabilityCard.from_json(second.read_text()).card.public_key_pem)
    assert first_fp == second_fp
    # supplying a key should not write a new one next to the second card.
    assert not second.with_suffix(second.suffix + ".key.pem").exists()
