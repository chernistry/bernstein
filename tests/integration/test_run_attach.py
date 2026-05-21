"""Integration tests for ``bernstein run --attach`` (issue #1797).

These tests exercise the full attachment passthrough flow against a
stubbed Claude adapter:

* The CLI flag accepts repeated ``--attach <path>`` invocations.
* Capability gating fails BEFORE any process is launched when the
  selected adapter does not advertise multimodal capability.
* An image attached at spawn time is encoded as base64 into the
  prompt body sent to the model API and the resulting lineage receipt
  carries the image's SHA-256 as a parent.
* A cross-worktree access attempt is rejected.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.adapters.base import SpawnResult
from bernstein.adapters.claude import (  # pyright: ignore[reportPrivateUsage]
    _inject_multimodal_attachments as claude_inject,
)
from bernstein.adapters.gemini import (  # pyright: ignore[reportPrivateUsage]
    _inject_multimodal_attachments as gemini_inject,
)
from bernstein.core.agents.multimodal import build_multimodal_context
from bernstein.core.agents.multimodal_attestation import (
    CapabilityRefusal,
    WorktreeAccessDenied,
    build_attachment_context,
    refuse_when_incapable,
    resolve_attachment_for_worker,
)
from bernstein.core.persistence.cas_store import CASStore
from bernstein.core.persistence.lineage_signer import (
    build_attachment_parent_uri,
    register_attachment_parents,
)
from bernstein.core.security.audit_chain import AuditChainStore
from bernstein.core.tasks.models import ModelConfig

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


@dataclass
class _SpawnCapture:
    """In-test stub adapter that records the prompt it was handed."""

    captured_prompt: str = ""
    captured_multimodal: object | None = None
    spawned: bool = False

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, object] | None = None,
        timeout_seconds: int = 600,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
        multimodal_context: object | None = None,
    ) -> SpawnResult:
        if multimodal_context is not None:
            prompt = claude_inject(prompt, multimodal_context)
        self.captured_prompt = prompt
        self.captured_multimodal = multimodal_context
        self.spawned = True
        # Return a placeholder SpawnResult; the integration test does not
        # exercise live process management.
        return SpawnResult(pid=0, log_path=workdir / "stub.log", proc=None)


def _audit_chain(tmp_path: Path) -> AuditChainStore:
    return AuditChainStore(audit_dir=tmp_path / "audit", key=b"k" * 32)


def _make_png(path: Path, payload: bytes = PNG_HEADER + b"data") -> Path:
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# CLI flag plumbing
# ---------------------------------------------------------------------------


class TestCliFlag:
    def test_attach_flag_is_repeatable(self, tmp_path: Path) -> None:
        """The CLI flag accepts multiple --attach arguments."""
        from bernstein.cli.run_bootstrap import run as run_cmd

        runner = CliRunner()
        a = _make_png(tmp_path / "a.png")
        b = _make_png(tmp_path / "b.png", payload=PNG_HEADER + b"bb")

        # Use --help-only contract: passing --attach + --help inspects
        # the parsed option without running the orchestrator.
        result = runner.invoke(
            run_cmd,
            [
                "--attach",
                str(a),
                "--attach",
                str(b),
                "--help",
            ],
            catch_exceptions=False,
        )
        # --help short-circuits; the important thing is the option was
        # accepted (no UsageError).
        assert result.exit_code == 0, result.output
        # The help text exposes the flag.
        assert "--attach" in result.output

    def test_attach_missing_path_rejected(self, tmp_path: Path) -> None:
        from bernstein.cli.run_bootstrap import run as run_cmd

        runner = CliRunner()
        ghost = tmp_path / "ghost.png"  # never created

        # Without --help, Click runs option validation for the existing
        # path check on --attach.
        result = runner.invoke(
            run_cmd,
            ["--attach", str(ghost), "--goal", "x"],
            catch_exceptions=False,
        )
        # Click validates existence at parse time -> non-zero exit and
        # a UsageError mentioning the missing file.
        assert result.exit_code != 0
        assert "ghost.png" in result.output or "does not exist" in result.output


# ---------------------------------------------------------------------------
# Round-trip on stubbed Claude adapter
# ---------------------------------------------------------------------------


class TestStubbedClaudeRoundTrip:
    def test_image_reaches_prompt_as_base64(self, tmp_path: Path) -> None:
        payload = PNG_HEADER + b"image-bytes"
        img = _make_png(tmp_path / "shot.png", payload=payload)
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")

        result = build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-1",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )

        stub = _SpawnCapture()
        stub.spawn(
            prompt="Describe the screenshot.",
            workdir=tmp_path,
            model_config=ModelConfig(model="sonnet", effort="high"),
            session_id="qa-abc12345",
            multimodal_context=result.context,
        )
        # The base64 payload is present in the captured prompt.
        import base64 as _b64

        expected_b64 = _b64.b64encode(payload).decode("ascii")
        assert expected_b64 in stub.captured_prompt
        # The MIME type is recorded in the wire format.
        assert 'mime="image/png"' in stub.captured_prompt
        # The SHA-256 is present in the wire format.
        digest = hashlib.sha256(payload).hexdigest()
        assert digest in stub.captured_prompt

    def test_lineage_receipt_carries_image_sha(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")

        result = build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-1",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        # Simulate the worker producing an artefact this turn and the
        # lineage subsystem augmenting the parents.
        parents = register_attachment_parents(
            parents=["worker:wkr-1#step0"],
            attachment_sha256s=[r.sha256 for r in result.resolutions],
        )
        assert any(result.resolutions[0].sha256 in p for p in parents)
        # The canonical URI format is content-addressed.
        assert build_attachment_parent_uri(result.resolutions[0].sha256) in parents

    def test_tamper_detection(self, tmp_path: Path) -> None:
        """Substituting bytes breaks the chain on verify."""
        img = _make_png(tmp_path / "shot.png")
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-1",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        # Tamper with the audit-chain log on disk.
        log_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert log_files
        tampered = bytearray(log_files[0].read_bytes())
        # Find a hex character and flip it (digest field) to invalidate
        # the on-disk canonical form.
        for i, b in enumerate(tampered):
            if chr(b) in "0123456789":
                tampered[i] = ord("0") if chr(b) != "0" else ord("1")
                break
        log_files[0].write_bytes(bytes(tampered))
        valid, errors = chain.verify()
        assert not valid
        assert errors


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


class TestCapabilityRefusal:
    def test_codex_with_attachment_refused(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        with pytest.raises(CapabilityRefusal) as exc:
            refuse_when_incapable(adapter_name="codex", attachments=[str(img)])
        assert "claude" in exc.value.suggested_adapters
        assert "gemini" in exc.value.suggested_adapters

    def test_claude_accepts(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        # No raise.
        refuse_when_incapable(adapter_name="claude", attachments=[str(img)])

    def test_gemini_accepts(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        refuse_when_incapable(adapter_name="gemini", attachments=[str(img)])

    def test_no_attachments_no_refusal(self) -> None:
        refuse_when_incapable(adapter_name="codex", attachments=[])


# ---------------------------------------------------------------------------
# Cross-worktree isolation
# ---------------------------------------------------------------------------


class TestCrossWorktreeIsolation:
    def test_wt_b_cannot_resolve_wt_a_attachment(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        result = build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-a",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        digest = result.resolutions[0].sha256
        with pytest.raises(WorktreeAccessDenied):
            resolve_attachment_for_worker(
                sha256=digest,
                requesting_worktree_id="wt-b",
                cas=cas,
                audit_chain=chain,
            )

    def test_wt_a_can_resolve_wt_a_attachment(self, tmp_path: Path) -> None:
        payload = PNG_HEADER + b"wt-a"
        img = _make_png(tmp_path / "shot.png", payload=payload)
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        result = build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-a",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        digest = result.resolutions[0].sha256
        replayed = resolve_attachment_for_worker(
            sha256=digest,
            requesting_worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        assert replayed == payload


# ---------------------------------------------------------------------------
# Gemini adapter wire format
# ---------------------------------------------------------------------------


class TestGeminiInjection:
    def test_gemini_inlines_attachment_block(self, tmp_path: Path) -> None:
        img = _make_png(tmp_path / "shot.png")
        ctx = build_multimodal_context([img])
        out = gemini_inject("Look at this.", ctx)
        assert '<attachment mime="image/png"' in out
        # Original prompt body is preserved.
        assert "Look at this." in out

    def test_empty_context_passthrough(self) -> None:
        ctx = build_multimodal_context([])
        assert gemini_inject("Hi", ctx) == "Hi"
