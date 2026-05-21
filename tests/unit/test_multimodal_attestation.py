"""Unit tests for image-attachment passthrough with provenance (#1797).

Covers:
* Task model accepts an ``attachments`` list.
* Spawn-time wiring: :func:`build_attachment_context` reads paths, stores
  bytes in CAS, builds a :class:`MultiModalContext`, and records an
  audit-chain entry of type ``multimodal.attach``.
* The lineage helper exposes attachment digests as artefact parents.
* Capability gating refuses adapters that do not advertise multimodal
  support, with a structured error suggesting capable adapters.
* sha256 stability: encoding the same bytes always yields the same digest.
* Worktree pinning: an image attached in worktree wt-a is not reachable
  from a worker in wt-b.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.agents.multimodal import (
    ModalityType,
    MultiModalContext,
    is_multimodal_capable,
)
from bernstein.core.agents.multimodal_attestation import (
    AttachmentResolution,
    CapabilityRefusal,
    WorktreeAccessDenied,
    build_attachment_context,
    refuse_when_incapable,
    resolve_attachment_for_worker,
    worker_lineage_parents,
)
from bernstein.core.persistence.cas_store import CASStore
from bernstein.core.security.audit_chain import (
    EVENT_MULTIMODAL_ATTACH,
    AuditChainStore,
    record_multimodal_attach,
)
from bernstein.core.tasks.models import Task

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _make_image(path: Path, payload: bytes = PNG_MAGIC + b"hello") -> Path:
    path.write_bytes(payload)
    return path


def _audit_chain(tmp_path: Path) -> AuditChainStore:
    return AuditChainStore(
        audit_dir=tmp_path / "audit",
        key=b"k" * 32,
    )


# ---------------------------------------------------------------------------
# Task model field
# ---------------------------------------------------------------------------


class TestTaskAttachmentsField:
    def test_task_accepts_attachments_default_empty(self) -> None:
        task = Task(
            id="t-1",
            title="With attachment",
            description="desc",
            role="backend",
        )
        assert task.attachments == []

    def test_task_accepts_attachments_list(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
        task = Task(
            id="t-2",
            title="With attachment",
            description="desc",
            role="backend",
            attachments=[str(img)],
        )
        assert task.attachments == [str(img)]

    def test_task_from_dict_reads_attachments(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
        raw = {
            "id": "t-3",
            "title": "x",
            "description": "y",
            "role": "backend",
            "attachments": [str(img)],
        }
        task = Task.from_dict(raw)
        assert task.attachments == [str(img)]


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


class TestCapabilityGating:
    def test_capable_adapter_passes(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
        # No exception raised.
        refuse_when_incapable(adapter_name="claude", attachments=[str(img)])

    def test_incapable_adapter_refused(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
        with pytest.raises(CapabilityRefusal) as exc:
            refuse_when_incapable(adapter_name="codex", attachments=[str(img)])
        assert "codex" in str(exc.value).lower()
        # Error suggests adapters that DO support attachments.
        assert exc.value.suggested_adapters
        assert all(is_multimodal_capable(a) for a in exc.value.suggested_adapters)

    def test_no_attachments_no_refusal(self) -> None:
        # An incapable adapter is fine when no attachments are present.
        refuse_when_incapable(adapter_name="codex", attachments=[])


# ---------------------------------------------------------------------------
# sha256 stability
# ---------------------------------------------------------------------------


class TestSha256Stability:
    def test_same_bytes_same_digest(self, tmp_path: Path) -> None:
        img1 = _make_image(tmp_path / "a.png", payload=b"identical")
        img2 = _make_image(tmp_path / "b.png", payload=b"identical")
        chain1 = _audit_chain(tmp_path / "x1")
        chain2 = _audit_chain(tmp_path / "x2")
        cas1 = CASStore(tmp_path / "cas1")
        cas2 = CASStore(tmp_path / "cas2")

        ctx1 = build_attachment_context(
            attachments=[str(img1)],
            worker_id="wkr-1",
            turn_seq=1,
            worktree_id="wt-a",
            cas=cas1,
            audit_chain=chain1,
        )
        ctx2 = build_attachment_context(
            attachments=[str(img2)],
            worker_id="wkr-2",
            turn_seq=1,
            worktree_id="wt-b",
            cas=cas2,
            audit_chain=chain2,
        )
        assert ctx1.resolutions[0].sha256 == ctx2.resolutions[0].sha256


# ---------------------------------------------------------------------------
# build_attachment_context
# ---------------------------------------------------------------------------


class TestBuildAttachmentContext:
    def test_returns_multimodal_context(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        assert isinstance(result.context, MultiModalContext)
        assert result.context.primary_modality == ModalityType.IMAGE
        assert len(result.context.inputs) == 1
        assert result.context.inputs[0].mime_type == "image/png"

    def test_records_audit_chain_entry(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        result = build_attachment_context(
            attachments=[str(img)],
            worker_id="wkr-1",
            turn_seq=7,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        # Find the multimodal.attach event in the chain.
        entries = chain.query(event_type=EVENT_MULTIMODAL_ATTACH)
        assert len(entries) == 1
        details = entries[0].details
        assert details["sha256"] == result.resolutions[0].sha256
        assert details["mime"] == "image/png"
        assert details["worker_id"] == "wkr-1"
        assert details["turn_seq"] == 7
        assert details["worktree_id"] == "wt-a"
        # operator_install_id_sig is recorded (non-empty string).
        assert details["operator_install_id_sig"]
        assert details["prev_chain_digest"]

    def test_stores_bytes_in_cas(self, tmp_path: Path) -> None:
        payload = PNG_MAGIC + b"unique-bytes"
        img = _make_image(tmp_path / "shot.png", payload=payload)
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
        digest = result.resolutions[0].sha256
        assert cas.has(digest)
        assert cas.get(digest) == payload

    def test_multiple_attachments(self, tmp_path: Path) -> None:
        i1 = _make_image(tmp_path / "a.png", payload=b"a" * 16)
        i2 = _make_image(tmp_path / "b.png", payload=b"b" * 16)
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        result = build_attachment_context(
            attachments=[str(i1), str(i2)],
            worker_id="wkr-1",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        assert len(result.resolutions) == 2
        entries = chain.query(event_type=EVENT_MULTIMODAL_ATTACH)
        assert len(entries) == 2

    def test_audit_chain_valid_after_attach(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        valid, errors = chain.verify()
        assert valid, f"chain integrity broken: {errors}"


# ---------------------------------------------------------------------------
# Lineage parent inclusion
# ---------------------------------------------------------------------------


class TestLineageParents:
    def test_resolutions_become_lineage_parents(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        parents = worker_lineage_parents(result)
        assert len(parents) == 1
        # Lineage parent identifiers are content-addressed.
        assert result.resolutions[0].sha256 in parents[0]

    def test_no_attachments_no_parents(self, tmp_path: Path) -> None:
        chain = _audit_chain(tmp_path)
        cas = CASStore(tmp_path / "cas")
        result = build_attachment_context(
            attachments=[],
            worker_id="wkr-1",
            turn_seq=0,
            worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        assert worker_lineage_parents(result) == []


# ---------------------------------------------------------------------------
# Worktree pinning
# ---------------------------------------------------------------------------


class TestWorktreePinning:
    def test_same_worktree_can_resolve(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        digest = result.resolutions[0].sha256
        # Same worktree id can resolve the attachment back to bytes.
        bytes_back = resolve_attachment_for_worker(
            sha256=digest,
            requesting_worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        assert bytes_back == img.read_bytes()

    def test_cross_worktree_rejected(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        digest = result.resolutions[0].sha256
        with pytest.raises(WorktreeAccessDenied):
            resolve_attachment_for_worker(
                sha256=digest,
                requesting_worktree_id="wt-b",
                cas=cas,
                audit_chain=chain,
            )


# ---------------------------------------------------------------------------
# Replay & tamper detection
# ---------------------------------------------------------------------------


class TestReplayAndTamper:
    def test_replay_reproduces_exact_bytes(self, tmp_path: Path) -> None:
        payload = PNG_MAGIC + b"exact-replay-bytes"
        img = _make_image(tmp_path / "shot.png", payload=payload)
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
        digest = result.resolutions[0].sha256
        replayed = resolve_attachment_for_worker(
            sha256=digest,
            requesting_worktree_id="wt-a",
            cas=cas,
            audit_chain=chain,
        )
        assert replayed == payload

    def test_tamper_breaks_verification(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        # Tamper with the audit chain log on disk.
        log_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert log_files
        raw = log_files[0].read_bytes()
        # Flip one byte inside the JSON payload (not the trailing newline).
        # The first '{' is at offset 0; flip a hex character somewhere in
        # the middle of the line so the JSON still parses but the canonical
        # form drifts.
        tampered = bytearray(raw)
        # Find a digit / letter to flip in the payload.
        for i, b in enumerate(tampered):
            if chr(b).isalnum() and i > 0 and tampered[i - 1] != ord('"'):
                tampered[i] = ord("a") if chr(b) != "a" else ord("b")
                break
        log_files[0].write_bytes(bytes(tampered))
        valid, errors = chain.verify()
        assert not valid
        assert errors


# ---------------------------------------------------------------------------
# record_multimodal_attach helper
# ---------------------------------------------------------------------------


class TestRecordMultimodalAttach:
    def test_records_event_with_required_fields(self, tmp_path: Path) -> None:
        chain = _audit_chain(tmp_path)
        event = record_multimodal_attach(
            chain=chain,
            sha256="a" * 64,
            mime="image/png",
            operator_install_id_sig="install-sig-abc",
            worker_id="wkr-1",
            turn_seq=3,
            worktree_id="wt-a",
        )
        assert event.event_type == EVENT_MULTIMODAL_ATTACH
        assert event.details["sha256"] == "a" * 64
        assert event.details["mime"] == "image/png"
        assert event.details["worker_id"] == "wkr-1"
        assert event.details["turn_seq"] == 3
        assert event.details["worktree_id"] == "wt-a"
        assert event.details["operator_install_id_sig"] == "install-sig-abc"
        # prev_chain_digest comes from the underlying chain (genesis for first
        # event).
        assert event.details["prev_chain_digest"]

    def test_chain_continues_across_attaches(self, tmp_path: Path) -> None:
        chain = _audit_chain(tmp_path)
        e1 = record_multimodal_attach(
            chain=chain,
            sha256="a" * 64,
            mime="image/png",
            operator_install_id_sig="sig",
            worker_id="w",
            turn_seq=0,
            worktree_id="wt-a",
        )
        e2 = record_multimodal_attach(
            chain=chain,
            sha256="b" * 64,
            mime="image/jpeg",
            operator_install_id_sig="sig",
            worker_id="w",
            turn_seq=1,
            worktree_id="wt-a",
        )
        assert e2.details["prev_chain_digest"] == e1.hmac

    def test_query_filters_event_type(self, tmp_path: Path) -> None:
        chain = _audit_chain(tmp_path)
        # Mix in an unrelated event.
        chain.log(
            event_type="task.transition",
            actor="orchestrator",
            resource_type="task",
            resource_id="t-1",
        )
        record_multimodal_attach(
            chain=chain,
            sha256="a" * 64,
            mime="image/png",
            operator_install_id_sig="sig",
            worker_id="w",
            turn_seq=0,
            worktree_id="wt-a",
        )
        attaches = chain.query(event_type=EVENT_MULTIMODAL_ATTACH)
        assert len(attaches) == 1
        assert attaches[0].details["sha256"] == "a" * 64


# ---------------------------------------------------------------------------
# AttachmentResolution dataclass
# ---------------------------------------------------------------------------


class TestAttachmentResolution:
    def test_resolution_carries_digest_and_mime(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "shot.png")
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
        r = result.resolutions[0]
        assert isinstance(r, AttachmentResolution)
        assert len(r.sha256) == 64
        assert r.mime == "image/png"
        assert r.worktree_id == "wt-a"


# ---------------------------------------------------------------------------
# YAML plan loader integration
# ---------------------------------------------------------------------------


class TestPlanLoaderAttachments:
    def test_yaml_plan_attachments_propagate(self, tmp_path: Path) -> None:
        img = _make_image(tmp_path / "img.png")
        plan_yaml = tmp_path / "plan.yaml"
        plan_yaml.write_text(
            "name: test\n"
            "stages:\n"
            "  - name: phase1\n"
            "    steps:\n"
            "      - title: With attachment\n"
            "        role: backend\n"
            f"        attachments: ['{img}']\n"
        )
        from bernstein.core.planning.plan_loader import load_plan_from_yaml

        tasks = load_plan_from_yaml(plan_yaml)
        assert len(tasks) == 1
        assert tasks[0].attachments == [str(img)]
