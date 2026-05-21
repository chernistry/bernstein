"""EU AI Act Article 12 evidence renderers.

This module produces the human- and machine-readable surfaces of the
compliance pack:

* :func:`render_pdf` - reportlab-driven PDF summary keyed off the
  paragraph map below.
* :func:`render_csv` - flat CSV with one row per :class:`LineageEntry`.
* :data:`ARTICLE12_PARAGRAPH_MAP` - pure-function table mapping each
  Article 12 paragraph to the derived fact a compliance officer expects
  to see. Every value is a callable
  ``(entries: list[LineageEntry], period: tuple[str, str]) -> dict``
  whose return shape is ``{"paragraph": str, "description": str,
  "value": Any}``.

Article 12 references EU AI Act (Regulation (EU) 2024/1689):

* 12(1)  - automatic event-logging exists.
* 12(2)(a) - period of use.
* 12(2)(b) - reference DB against which input data was checked (n/a here;
              left to the deploying system to attest).
* 12(2)(c) - input data that led to a match.
* 12(2)(d) - identification of natural persons involved in verification.
* 12(3)  - minimum 6-month retention (or 10 years for high-risk Annex III).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from bernstein.core.lineage.entry import LineageEntry


# Article 12(3): high-risk systems must retain logs for 10 years; the
# baseline minimum is 6 months for non-high-risk regulated AI systems.
# We surface the 6-month baseline so compliance officers can spot a
# pack window that is itself too narrow to satisfy the retention rule.
_MIN_RETENTION_DAYS = 6 * 30


CSV_FIELDS: tuple[str, ...] = (
    "ts_ns",
    "artefact_path",
    "artefact_kind",
    "content_hash",
    "parent_hashes",
    "agent_id",
    "agent_card_kid",
    "tool_call_id",
    "span_id",
)


def render_csv(entries: list[LineageEntry]) -> str:
    """Return CSV text with one row per entry.

    The header row is emitted unconditionally so an empty list yields a
    valid (but data-less) CSV file -- otherwise downstream readers that
    expect at least the header would choke on a zero-byte payload.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_FIELDS), lineterminator="\n")
    writer.writeheader()
    for entry in entries:
        row = asdict(entry)
        # parent_hashes is a list; flatten via space-separated string so
        # `csv.DictReader` doesn't choke on commas inside the field.
        row["parent_hashes"] = " ".join(row["parent_hashes"])
        # Drop fields not in our schema (e.g. ``v``/``operator_hmac``)
        # so the CSV stays compact and reviewer-friendly.
        writer.writerow({k: row[k] for k in CSV_FIELDS})
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Paragraph map
# ---------------------------------------------------------------------------


def _paragraph_12_1(entries: list[LineageEntry], _period: tuple[str, str]) -> dict[str, Any]:
    return {
        "paragraph": "12(1)",
        "description": "Automatic recording of events ('logs') over the lifetime of the system.",
        "value": len(entries),
    }


def _paragraph_12_2_a(entries: list[LineageEntry], period: tuple[str, str]) -> dict[str, Any]:
    if entries:
        first_ts = min(e.ts_ns for e in entries)
        last_ts = max(e.ts_ns for e in entries)
    else:
        first_ts = 0
        last_ts = 0
    return {
        "paragraph": "12(2)(a)",
        "description": "Period of use of the high-risk AI system.",
        "value": {
            "first_ts_ns": first_ts,
            "last_ts_ns": last_ts,
            "period_since": period[0],
            "period_until": period[1],
        },
    }


def _paragraph_12_2_b(_entries: list[LineageEntry], _period: tuple[str, str]) -> dict[str, Any]:
    return {
        "paragraph": "12(2)(b)",
        "description": (
            "Reference database against which input data has been checked by the "
            "system. Not derivable from lineage alone; the deploying organisation "
            "must attest separately."
        ),
        "value": None,
    }


def _paragraph_12_2_c(entries: list[LineageEntry], _period: tuple[str, str]) -> dict[str, Any]:
    return {
        "paragraph": "12(2)(c)",
        "description": "Input data for which the search has led to a match.",
        "value": sorted({e.artefact_path for e in entries}),
    }


def _paragraph_12_2_d(entries: list[LineageEntry], _period: tuple[str, str]) -> dict[str, Any]:
    return {
        "paragraph": "12(2)(d)",
        "description": "Identification of the natural persons involved in the verification of results.",
        "value": sorted({e.agent_id for e in entries}),
    }


def _paragraph_12_3(_entries: list[LineageEntry], period: tuple[str, str]) -> dict[str, Any]:
    since = datetime.strptime(period[0], "%Y-%m-%d").date()
    until = datetime.strptime(period[1], "%Y-%m-%d").date()
    span_days = (until - since).days
    return {
        "paragraph": "12(3)",
        "description": (
            "Logs shall be kept for an appropriate period (minimum 6 months) in proportion to the intended purpose."
        ),
        "value": {
            "period_days": span_days,
            "minimum_required_days": _MIN_RETENTION_DAYS,
            "meets_minimum": span_days >= _MIN_RETENTION_DAYS,
        },
    }


ParagraphFn = Callable[[list["LineageEntry"], tuple[str, str]], dict[str, Any]]


ARTICLE12_PARAGRAPH_MAP: dict[str, ParagraphFn] = {
    "12(1)": _paragraph_12_1,
    "12(2)(a)": _paragraph_12_2_a,
    "12(2)(b)": _paragraph_12_2_b,
    "12(2)(c)": _paragraph_12_2_c,
    "12(2)(d)": _paragraph_12_2_d,
    "12(3)": _paragraph_12_3,
}


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def render_pdf(
    entries: list[LineageEntry],
    *,
    org: str,
    period: tuple[str, str],
) -> bytes:
    """Render a multi-page PDF summarising Article 12 conformance.

    Layout:
      * Header - org name + period.
      * Per-paragraph table with the derived fact.
      * Footer - generation timestamp.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"EU AI Act Article 12 - {org}",
    )
    styles = getSampleStyleSheet()
    story: list[Any] = []

    story.extend(
        (
            Paragraph("<b>EU AI Act Article 12 - Evidence Report</b>", styles["Title"]),
            Spacer(1, 0.4 * cm),
            Paragraph(f"<b>Organisation:</b> {org}", styles["Normal"]),
            Paragraph(f"<b>Period:</b> {period[0]} → {period[1]}", styles["Normal"]),
            Paragraph(f"<b>Entries:</b> {len(entries)}", styles["Normal"]),
            Spacer(1, 0.4 * cm),
        )
    )

    # Paragraph table.
    rows: list[list[str]] = [["Paragraph", "Derived fact"]]
    for paragraph, fn in ARTICLE12_PARAGRAPH_MAP.items():
        fact = fn(entries, period)
        value = fact["value"]
        rows.append([paragraph, _render_value_for_pdf(value)])

    tbl = Table(rows, colWidths=[3 * cm, 13 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ],
        ),
    )
    story.extend((tbl, Spacer(1, 0.4 * cm)))

    # Sample of entries (first 25) so a regulator can eyeball the data
    # without having to crack the CSV.
    sample = entries[:25]
    if sample:
        story.append(Paragraph("<b>Sample entries (first 25)</b>", styles["Heading3"]))
        sample_rows: list[list[str]] = [["ts_ns", "agent_id", "artefact_path"]]
        for e in sample:
            sample_rows.append([str(e.ts_ns), e.agent_id, e.artefact_path])
        sample_tbl = Table(sample_rows, colWidths=[3.5 * cm, 4.5 * cm, 8 * cm])
        sample_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ],
            ),
        )
        story.extend((sample_tbl, Spacer(1, 0.4 * cm)))

    story.append(
        Paragraph(
            f"<i>Generated {datetime.now(UTC).isoformat(timespec='seconds')} by bernstein.</i>",
            styles["Italic"],
        ),
    )

    doc.build(story)
    return buf.getvalue()


def _render_value_for_pdf(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, list):
        if not value:
            return "(none)"
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


__all__ = [
    "ARTICLE12_PARAGRAPH_MAP",
    "CSV_FIELDS",
    "ParagraphFn",
    "render_csv",
    "render_pdf",
]
