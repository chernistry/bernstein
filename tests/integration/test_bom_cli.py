"""Integration tests for ``bernstein bom`` -- end-to-end CLI flow.

≥10 round-trip tests on synthetic run -> BOM -> verify. The shape we
test mirrors the production integration: a JSON snapshot drops into
``.sdd/runs/<run_id>/bom_snapshot.json`` and the CLI projects it into
an encoded BOM (json/cyclonedx/spdx). Verify then re-reads the BOM.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from bernstein.cli.commands.bom_cmd import bom_group


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _snapshot() -> dict[str, Any]:
    return {
        "run_id": "20260518-test-001",
        "started_at": "2026-05-18T10:10:10Z",
        "finished_at": "2026-05-18T10:11:00Z",
        "lineage_root_hash": _sha("lineage-root"),
        "bernstein_version": "2.1.0",
        "models": [
            {
                "name": "claude-3-7-sonnet",
                "provider": "anthropic",
                "version": "2026-02-15",
                "sha256": _sha("model-sonnet"),
                "invocation_count": 4,
            },
        ],
        "prompts": [
            {"name": "manager-system", "role": "manager", "sha256": _sha("prompt-manager")},
        ],
        "adapters": [
            {
                "name": "claude",
                "version": "1.4.0",
                "sha256": _sha("adapter-claude"),
                "binary": "claude",
            },
        ],
        "tools": [
            {"name": "git", "kind": "shell", "sha256": _sha("tool-git")},
        ],
        "data_sources": [
            {"uri": "git+https://github.com/x/y@deadbeef", "kind": "repo", "sha256": _sha("source-x")},
        ],
    }


def _write_snapshot(workdir: Path, run_id: str, snap: dict[str, Any]) -> Path:
    run_dir = workdir / ".sdd" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    snap_path = run_dir / "bom_snapshot.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    return snap_path


# ---------------------------------------------------------------------------
# 1. ``bom emit`` happy paths
# ---------------------------------------------------------------------------


class TestBOMEmit:
    def test_emit_json_to_stdout(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(bom_group, ["emit", "--snapshot", str(snap_path)])
        assert result.exit_code == 0, result.output
        decoded = json.loads(result.output.strip())
        assert decoded["run_id"] == "20260518-test-001"
        assert decoded["schema_version"] == "1.0"

    def test_emit_cyclonedx_to_stdout(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path), "--format", "cyclonedx"],
        )
        assert result.exit_code == 0, result.output
        decoded = json.loads(result.output.strip())
        assert decoded["bomFormat"] == "CycloneDX"
        assert decoded["specVersion"] == "1.5"

    def test_emit_spdx_to_stdout(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path), "--format", "spdx"],
        )
        assert result.exit_code == 0, result.output
        decoded = json.loads(result.output.strip())
        assert decoded["spdxVersion"] == "SPDX-2.3"

    def test_emit_with_run_id_reads_runs_dir(self, tmp_path: Path) -> None:
        run_id = "20260518-from-runs"
        _write_snapshot(tmp_path, run_id, _snapshot())
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--run", run_id, "--workdir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        decoded = json.loads(result.output.strip())
        assert decoded["run_id"] == "20260518-test-001"

    def test_emit_writes_to_out_path(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        out_path = tmp_path / "bom.json"
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            [
                "emit",
                "--snapshot",
                str(snap_path),
                "--out",
                str(out_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out_path.exists()
        decoded = json.loads(out_path.read_text(encoding="utf-8"))
        assert decoded["run_id"] == "20260518-test-001"


# ---------------------------------------------------------------------------
# 2. ``bom emit`` validation
# ---------------------------------------------------------------------------


class TestBOMEmitValidation:
    def test_mutually_exclusive_run_and_snapshot(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--run", "x", "--snapshot", str(tmp_path / "missing.json")],
        )
        assert result.exit_code != 0

    def test_neither_run_nor_snapshot(self) -> None:
        runner = CliRunner()
        result = runner.invoke(bom_group, ["emit"])
        assert result.exit_code != 0
        assert "required" in result.output

    def test_missing_run_snapshot_errors(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--run", "does-not-exist", "--workdir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_corrupt_snapshot_errors(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text("not valid json", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path)],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 3. Round-trip emit -> verify
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_json_roundtrip(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        bom_path = tmp_path / "bom.json"
        runner = CliRunner()

        emit = runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path), "--out", str(bom_path)],
        )
        assert emit.exit_code == 0, emit.output

        verify = runner.invoke(bom_group, ["verify", str(bom_path)])
        assert verify.exit_code == 0, verify.output
        assert "PASS" in verify.output

    def test_verify_tamper_detection(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        bom_path = tmp_path / "bom.json"
        runner = CliRunner()

        runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path), "--out", str(bom_path)],
        )
        # Tamper with one sha
        doc = json.loads(bom_path.read_text(encoding="utf-8"))
        doc["models"][0]["sha256"] = "not-a-sha"
        bom_path.write_text(json.dumps(doc), encoding="utf-8")

        verify = runner.invoke(bom_group, ["verify", str(bom_path)])
        assert verify.exit_code != 0
        assert "FAIL" in verify.output

    def test_verify_quiet_mode(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        bom_path = tmp_path / "bom.json"
        runner = CliRunner()
        runner.invoke(
            bom_group,
            ["emit", "--snapshot", str(snap_path), "--out", str(bom_path)],
        )
        verify = runner.invoke(bom_group, ["verify", "--quiet", str(bom_path)])
        assert verify.exit_code == 0
        assert verify.output.strip() == ""

    def test_emit_twice_byte_identical(self, tmp_path: Path) -> None:
        """Pure projection: re-emitting the same snapshot gives identical bytes."""
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        runner = CliRunner()

        r1 = runner.invoke(bom_group, ["emit", "--snapshot", str(snap_path)])
        r2 = runner.invoke(bom_group, ["emit", "--snapshot", str(snap_path)])
        assert r1.exit_code == 0 and r2.exit_code == 0
        assert r1.output == r2.output

    def test_cyclonedx_roundtrip_carries_run_id(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        bom_path = tmp_path / "bom.cdx.json"
        runner = CliRunner()
        runner.invoke(
            bom_group,
            [
                "emit",
                "--snapshot",
                str(snap_path),
                "--format",
                "cyclonedx",
                "--out",
                str(bom_path),
            ],
        )
        decoded = json.loads(bom_path.read_text(encoding="utf-8"))
        props = {p["name"]: p["value"] for p in decoded["metadata"]["properties"]}
        assert props["bernstein:run_id"] == "20260518-test-001"

    def test_spdx_roundtrip_packages_count(self, tmp_path: Path) -> None:
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        bom_path = tmp_path / "bom.spdx.json"
        runner = CliRunner()
        runner.invoke(
            bom_group,
            [
                "emit",
                "--snapshot",
                str(snap_path),
                "--format",
                "spdx",
                "--out",
                str(bom_path),
            ],
        )
        decoded = json.loads(bom_path.read_text(encoding="utf-8"))
        # 1 model + 1 prompt + 1 adapter + 1 tool + 1 source = 5 packages
        assert len(decoded["packages"]) == 5

    def test_multi_run_emits_are_independent(self, tmp_path: Path) -> None:
        snap_a = _snapshot()
        snap_b = snap_a.copy()
        snap_b["run_id"] = "20260518-test-002"
        a_path = tmp_path / "a.json"
        b_path = tmp_path / "b.json"
        a_path.write_text(json.dumps(snap_a), encoding="utf-8")
        b_path.write_text(json.dumps(snap_b), encoding="utf-8")
        runner = CliRunner()
        r_a = runner.invoke(bom_group, ["emit", "--snapshot", str(a_path)])
        r_b = runner.invoke(bom_group, ["emit", "--snapshot", str(b_path)])
        assert r_a.output != r_b.output

    def test_emit_then_verify_with_run_id(self, tmp_path: Path) -> None:
        run_id = "20260518-from-runs-2"
        _write_snapshot(tmp_path, run_id, _snapshot())
        bom_path = tmp_path / "bom.json"
        runner = CliRunner()
        emit = runner.invoke(
            bom_group,
            [
                "emit",
                "--run",
                run_id,
                "--workdir",
                str(tmp_path),
                "--out",
                str(bom_path),
            ],
        )
        assert emit.exit_code == 0, emit.output
        verify = runner.invoke(bom_group, ["verify", str(bom_path)])
        assert verify.exit_code == 0, verify.output

    def test_verify_garbage_file_fails(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("just text", encoding="utf-8")
        runner = CliRunner()
        verify = runner.invoke(bom_group, ["verify", str(bad)])
        assert verify.exit_code != 0

    def test_three_format_roundtrip_same_run(self, tmp_path: Path) -> None:
        """Emitting the same snapshot in all three formats produces the
        expected per-format payload while preserving the run identity."""
        snap_path = tmp_path / "snap.json"
        snap_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        runner = CliRunner()

        for fmt, key in (
            ("json", "schema_version"),
            ("cyclonedx", "bomFormat"),
            ("spdx", "spdxVersion"),
        ):
            out = tmp_path / f"bom.{fmt}.json"
            result = runner.invoke(
                bom_group,
                ["emit", "--snapshot", str(snap_path), "--format", fmt, "--out", str(out)],
            )
            assert result.exit_code == 0, result.output
            decoded = json.loads(out.read_text(encoding="utf-8"))
            assert key in decoded
