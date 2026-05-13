"""Build the committed `expected-pack.zip` reference output for each demo.

The Steward merges the real `bernstein compliance pack` CLI later. Until then
this script produces a structurally-faithful stand-in:

  out.zip/
    README.md
    article12-evidence.csv
    lineage-log.jsonl
    signatures/                       (mirror of fixtures/signatures)
    agent-cards/                      (mirror of fixtures/agent-cards)
    pack-manifest.json
    verify-instructions.md

`make demo-*` reproduces this byte-for-byte; auditors can diff against the
committed expected-pack.zip to confirm a clean reproduction.

Determinism rules (match `core/security/article12_bundle.py`):
  - alphabetical entry order
  - fixed mtime 1980-01-01 (zip floor)
  - stored mode 0644
  - canonical JSON (sorted keys, no whitespace)
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent

ZIP_MTIME = (1980, 1, 1, 0, 0, 0)


def _canon_json(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _ns_to_iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_csv(entries: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "ts_utc",
            "artefact_path",
            "artefact_kind",
            "agent_id",
            "kid",
            "content_hash",
            "tool_call_id",
            "span_id",
            "parent_count",
        ]
    )
    for e in sorted(entries, key=lambda x: x["ts_ns"]):
        writer.writerow(
            [
                _ns_to_iso(e["ts_ns"]),
                e["artefact_path"],
                e["artefact_kind"],
                e["agent_id"],
                e["agent_card_kid"],
                e["content_hash"],
                e["tool_call_id"],
                e["span_id"],
                len(e["parent_hashes"]),
            ]
        )
    return buf.getvalue().encode("utf-8")


def _add(zf: zipfile.ZipFile, arcname: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(filename=arcname, date_time=ZIP_MTIME)
    info.external_attr = (0o644 & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, payload)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _walk(p: Path) -> list[Path]:
    return sorted(q for q in p.rglob("*") if q.is_file())


def build_pack(demo_dir: Path, org_name: str, window: tuple[str, str]) -> Path:
    fixtures = demo_dir / "fixtures"
    log_path = fixtures / "log.jsonl"
    sigs_dir = fixtures / "signatures"
    cards_dir = fixtures / "agent-cards"

    entries = _read_jsonl(log_path)

    manifest = {
        "schema": "bernstein.compliance.pack/v1",
        "org": org_name,
        "window": {"since": window[0], "until": window[1]},
        "demo_mode": True,
        "entry_count": len(entries),
        "artefact_paths": sorted({e["artefact_path"] for e in entries}),
        "agents": sorted({e["agent_id"] for e in entries}),
    }

    out_zip = demo_dir / "expected-pack.zip"
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    readme = (
        f"# Compliance pack — {org_name}\n\n"
        f"Demo bundle for the {demo_dir.name} lineage scenario.\n"
        f"Window: {window[0]} .. {window[1]}\n"
        f"Entries: {len(entries)}\n\n"
        "Verify with:  bernstein-verify pack expected-pack.zip\n"
    ).encode()

    verify_md = (
        b"# Verifying this pack\n\n"
        b"1. Install the auditor CLI:    pipx install bernstein-verify\n"
        b"2. Run:                        bernstein-verify pack expected-pack.zip\n"
        b"3. Exit 0 + 'PASS' line = signatures valid, chain complete, "
        b"no unresolved forks.\n"
    )

    csv_bytes = _build_csv(entries)
    log_bytes = log_path.read_bytes()

    items: list[tuple[str, bytes]] = [
        ("README.md", readme),
        ("article12-evidence.csv", csv_bytes),
        ("lineage-log.jsonl", log_bytes),
        ("pack-manifest.json", _canon_json(manifest)),
        ("verify-instructions.md", verify_md),
    ]

    # Mirror agent-cards and signatures under the pack.
    for p in _walk(cards_dir):
        arc = "agent-cards/" + p.relative_to(cards_dir).as_posix()
        items.append((arc, p.read_bytes()))
    for p in _walk(sigs_dir):
        arc = "signatures/" + p.relative_to(sigs_dir).as_posix()
        items.append((arc, p.read_bytes()))

    items.sort(key=lambda x: x[0])

    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, mode="w") as zf:
        for arc, data in items:
            _add(zf, arc, data)

    print(f"wrote pack -> {out_zip}  ({len(items)} entries)")
    return out_zip


def main() -> None:
    build_pack(ROOT / "fintech", "Acme Bank", ("2026-01-01", "2026-02-14"))
    build_pack(
        ROOT / "healthcare",
        "Northstar Health",
        ("2026-02-01", "2026-02-28"),
    )
    build_pack(
        ROOT / "eu-manufacturer",
        "Bavarian Tooling GmbH",
        ("2026-03-01", "2026-03-31"),
    )


if __name__ == "__main__":
    main()
