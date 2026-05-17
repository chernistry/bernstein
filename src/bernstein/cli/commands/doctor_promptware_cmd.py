"""Implementation of ``bernstein doctor promptware-scan <run-id>``.

Replays the tool-output records under ``.sdd/traces/<run-id>.jsonl`` and
prints any entries whose promptware-detector score reaches the requested
threshold. Designed for post-hoc triage; the live ingest path uses
:mod:`bernstein.core.security.promptware_ingest` for the same scoring.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from bernstein.core.security.promptware_detector import (
    PromptwareDetector,
    PromptwareScore,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "PromptwareScanRow",
    "iter_trace_records",
    "rows_from_trace",
    "run_promptware_scan",
]


@dataclass(frozen=True, slots=True)
class PromptwareScanRow:
    """One row in the doctor scan report."""

    line_number: int
    task: str
    adapter: str
    tool: str
    source_url: str
    score: PromptwareScore

    def to_json(self) -> dict[str, Any]:
        """Render the row as JSON-friendly primitives."""
        return {
            "line_number": self.line_number,
            "task": self.task,
            "adapter": self.adapter,
            "tool": self.tool,
            "source_url": self.source_url,
            "score": self.score.to_dict(),
        }


def iter_trace_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL trace file and return parseable records.

    Lines that fail to parse as a JSON object are skipped silently so a
    partial or corrupted trace still yields a useful report.
    """
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj: object = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                # The json module returns dict[Any, Any]; normalise to the
                # str-keyed shape we annotate elsewhere.
                items = cast("dict[Any, Any]", obj).items()
                typed: dict[str, Any] = {str(k): v for k, v in items}
                out.append(typed)
    return out


def rows_from_trace(
    records: list[dict[str, Any]],
    detector: PromptwareDetector,
    *,
    threshold: float,
) -> list[PromptwareScanRow]:
    """Score each record and return rows at or above ``threshold``."""
    rows: list[PromptwareScanRow] = []
    for line_number, record in enumerate(records, start=1):
        text = _extract_output(record)
        if not text:
            continue
        score = detector.classify(text)
        if score.score < threshold:
            continue
        rows.append(
            PromptwareScanRow(
                line_number=line_number,
                task=_str(record.get("task")),
                adapter=_str(record.get("adapter")),
                tool=_str(record.get("tool")),
                source_url=_str(record.get("source_url")),
                score=score,
            ),
        )
    return rows


def run_promptware_scan(
    *,
    run_id: str,
    workdir: Path,
    threshold: float,
    as_json: bool,
) -> int:
    """Execute the scan and emit a report.

    Returns:
        ``0`` when no entry is at or above the abort threshold,
        ``1`` otherwise. Operators can chain this into shell pipelines.
    """
    trace_path = workdir / ".sdd" / "traces" / f"{run_id}.jsonl"
    records = iter_trace_records(trace_path)
    detector = PromptwareDetector()
    rows = rows_from_trace(records, detector, threshold=threshold)

    if as_json:
        payload = {
            "run_id": run_id,
            "trace_path": str(trace_path),
            "threshold": threshold,
            "row_count": len(rows),
            "rows": [row.to_json() for row in rows],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        _render_table(run_id, trace_path, rows)

    has_abort = any(row.score.is_abort for row in rows)
    return 1 if has_abort else 0


def _render_table(run_id: str, trace_path: Path, rows: list[PromptwareScanRow]) -> None:
    """Plain-text table for human consumption."""
    sys.stdout.write(f"promptware-scan run_id={run_id} trace={trace_path}\n")
    sys.stdout.write(f"  rows above threshold: {len(rows)}\n")
    if not rows:
        sys.stdout.write("  no suspicious tool output detected\n")
        return
    for row in rows:
        sys.stdout.write(
            f"  line={row.line_number} task={row.task or '?'} "
            f"adapter={row.adapter or '?'} tool={row.tool or '?'} "
            f"score={row.score.score:.3f} verdict={row.score.verdict.value} "
            f"reasons={'; '.join(row.score.reasons)}\n",
        )


def _extract_output(record: dict[str, Any]) -> str:
    """Pull the tool-output text out of a trace record.

    The detector scans whatever payload was fed to the downstream agent.
    Several record schemas in the wild carry tool output under different
    keys, so we accept any of them and concatenate strings when a list
    of message parts is supplied.
    """
    for key in ("tool_output", "output", "result", "content", "text"):
        value: object = record.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            chunks: list[str] = []
            parts: list[object] = list(value)  # type: ignore[arg-type]
            for part in parts:
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, dict):
                    text_part: object = part.get("text")  # type: ignore[union-attr]
                    if isinstance(text_part, str):
                        chunks.append(text_part)
            joined = "\n".join(chunks)
            if joined:
                return joined
    return ""


def _str(value: object) -> str:
    """Stringify ``value`` while collapsing ``None`` to an empty string."""
    if value is None:
        return ""
    return str(value)
