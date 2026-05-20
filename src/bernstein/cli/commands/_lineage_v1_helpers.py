"""Shared read/index helpers for the lineage v1 CLI subcommands.

The lineage v1 commands (`gate`, `forks`, `chain`, `reindex`, `merge`) all
need to parse `.sdd/lineage/log.jsonl` and update the on-disk projections
(`by-artefact/` + `tips/`). These helpers stay deliberately small and
file-system-only so the commands remain unit-testable without depending
on the in-flight `LineageStore` work on the parallel branch.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from bernstein.core.lineage.entry import LineageEntry, entry_hash
from bernstein.core.lineage.tips import compute_tips

if TYPE_CHECKING:
    from pathlib import Path


def read_entries(log_path: Path) -> list[LineageEntry]:
    """Parse log.jsonl into LineageEntry objects. Skips malformed lines."""
    out: list[LineageEntry] = []
    if not log_path.exists():
        return out
    with log_path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                out.append(LineageEntry(**obj))
            except (json.JSONDecodeError, TypeError, ValueError):
                # Corrupt lines are surfaced by gate.check; reindex skips them.
                continue
    return out


def reindex(log_path: Path) -> int:
    """Rebuild by-artefact/<shard>/<hash>.jsonl and tips/<hash>.json.

    Returns the number of projections written.
    """
    entries = read_entries(log_path)
    log_dir = log_path.parent
    by_artefact_root = log_dir / "by-artefact"
    tips_root = log_dir / "tips"

    by_path: dict[str, list[LineageEntry]] = {}
    for e in entries:
        by_path.setdefault(e.artefact_path, []).append(e)

    tips = compute_tips(entries)

    written = 0
    for path, group in by_path.items():
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
        shard = digest[:2]
        out = by_artefact_root / shard / (digest + ".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            f.writelines(json.dumps(_asdict(e), sort_keys=True) + "\n" for e in group)
        # Tips
        tip_data = tips.get(path, {"open": [entry_hash(group[-1])], "merged": []})
        tips_out = tips_root / (digest + ".json")
        tips_out.parent.mkdir(parents=True, exist_ok=True)
        tips_out.write_text(
            json.dumps(
                {"open": tip_data["open"], "merged": tip_data["merged"]},
                sort_keys=True,
            )
        )
        written += 1
    return written


def _asdict(entry: LineageEntry) -> dict[str, object]:
    from dataclasses import asdict

    return asdict(entry)


__all__ = ["read_entries", "reindex"]
