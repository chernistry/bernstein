"""Generate the healthcare lineage demo fixtures.

Story: an AI agent maintains a triage decision-support YAML config
(`config/triage/decision_support.yaml`). Three agents collaborate:

  - clinical-rules-bot:   updates triage thresholds
  - hipaa-redactor:       enforces PHI redaction on input fields
  - article11-docs-bot:   keeps the Article 11 technical documentation in sync

The log is generated so every Article 12 paragraph maps to at least one
on-chain entry (see article12-mapping.md).

Run:
    uv run python examples/lineage/scripts/gen_demo_healthcare.py

Deterministic via fixed seed + fixed UTC timestamps.
"""

from __future__ import annotations

import random
from pathlib import Path

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    build_entry,
    fixed_iso_to_ns,
    make_agent,
    reset_log,
    reset_signatures,
    write_entry_with_signature,
)

SEED = 20260201
DEMO_DIR = Path(__file__).resolve().parent.parent / "healthcare"
FIXTURES_DIR = DEMO_DIR / "fixtures"
LOG_PATH = FIXTURES_DIR / "log.jsonl"
SIGS_DIR = FIXTURES_DIR / "signatures"
CARDS_DIR = FIXTURES_DIR / "agent-cards"

ARTEFACT = "config/triage/decision_support.yaml"
ARTICLE11_DOC = "docs/article-11/technical_documentation.md"

AGENT_IDS = [
    "agent:clinical-rules-bot",
    "agent:hipaa-redactor",
    "agent:article11-docs-bot",
]

# 32 timestamps across mid-Feb 2026.
TIMESTAMPS = [
    "2026-02-02T08:00:00Z",
    "2026-02-02T10:30:00Z",
    "2026-02-02T14:00:00Z",
    "2026-02-03T09:15:00Z",
    "2026-02-03T11:45:00Z",
    "2026-02-03T16:30:00Z",
    "2026-02-04T08:20:00Z",
    "2026-02-04T13:00:00Z",
    "2026-02-04T17:10:00Z",
    "2026-02-05T09:00:00Z",
    "2026-02-05T12:30:00Z",
    "2026-02-05T15:55:00Z",
    "2026-02-06T08:40:00Z",
    "2026-02-06T11:20:00Z",
    "2026-02-06T14:50:00Z",
    "2026-02-09T09:10:00Z",
    "2026-02-09T13:35:00Z",
    "2026-02-09T16:20:00Z",
    "2026-02-10T08:25:00Z",
    "2026-02-10T11:00:00Z",
    "2026-02-10T15:00:00Z",
    "2026-02-11T09:30:00Z",
    "2026-02-11T12:15:00Z",
    "2026-02-11T16:45:00Z",
    "2026-02-12T08:50:00Z",
    "2026-02-12T13:20:00Z",
    "2026-02-12T17:00:00Z",
    "2026-02-13T08:10:00Z",
    "2026-02-13T11:35:00Z",
    "2026-02-13T14:55:00Z",
    "2026-02-13T17:30:00Z",
    "2026-02-14T09:00:00Z",
]

# Mix of triage-config edits and Article 11 docs edits. Each entry carries
# a meaningful, regulator-readable narrative.
EDITS: list[tuple[str, str, str]] = [
    # (artefact, agent_id, narrative)
    (ARTEFACT, "agent:clinical-rules-bot", "initial threshold table for chest-pain triage"),
    (ARTEFACT, "agent:hipaa-redactor", "redact patient_name / mrn before model input"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "draft Article 11(1)(a) intended purpose"),
    (ARTEFACT, "agent:clinical-rules-bot", "tighten SpO2 cutoff from 92 to 94"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(b) system architecture diagram"),
    (ARTEFACT, "agent:hipaa-redactor", "redact dob to year-only granularity"),
    (ARTEFACT, "agent:clinical-rules-bot", "add pediatric branch (<12yo) with separate cutoffs"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(c) training-data lineage section"),
    (ARTEFACT, "agent:clinical-rules-bot", "calibrate sepsis suspicion weight (+0.15)"),
    (ARTEFACT, "agent:hipaa-redactor", "scrub free-text symptom field for embedded PHI"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(d) risk-management measures"),
    (ARTEFACT, "agent:clinical-rules-bot", "lower fall-risk threshold for >75yo cohort"),
    (ARTEFACT, "agent:clinical-rules-bot", "add NEWS2 score override path"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(e) human oversight controls"),
    (ARTEFACT, "agent:hipaa-redactor", "block geo coordinates finer than postal-area"),
    (ARTEFACT, "agent:clinical-rules-bot", "raise stroke-suspicion flag on FAST positive"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(f) accuracy metrics table"),
    (ARTEFACT, "agent:clinical-rules-bot", "rebalance respiratory-distress thresholds"),
    (ARTEFACT, "agent:hipaa-redactor", "ensure insurance_id stripped from logs"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(g) cybersecurity measures"),
    (ARTEFACT, "agent:clinical-rules-bot", "add lactate>4 → immediate ICU referral"),
    (ARTEFACT, "agent:clinical-rules-bot", "adjust GCS<13 escalation path"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(1)(h) post-market monitoring plan"),
    (ARTEFACT, "agent:hipaa-redactor", "verify no PHI leakage in error responses"),
    (ARTEFACT, "agent:clinical-rules-bot", "incorporate ESI v4 mapping for severity 2"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(2) annex with version history"),
    (ARTEFACT, "agent:clinical-rules-bot", "regression: restore SpO2 cutoff bug fix"),
    (ARTEFACT, "agent:hipaa-redactor", "lock down patient_address to ZIP3"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11(3) update tracking entry"),
    (ARTEFACT, "agent:clinical-rules-bot", "final calibration: AUROC 0.91 on test set"),
    (ARTICLE11_DOC, "agent:article11-docs-bot", "Article 11 sign-off: ready for conformity assessment"),
    (ARTEFACT, "agent:clinical-rules-bot", "release-candidate freeze of triage thresholds"),
]


def main() -> None:
    rng = random.Random(SEED)

    reset_log(LOG_PATH)
    reset_signatures(SIGS_DIR)

    agents: dict[str, tuple[str, object]] = {}
    for idx, aid in enumerate(AGENT_IDS, start=1):
        priv, card = make_agent(
            rng,
            agent_id=aid,
            card_dir=CARDS_DIR,
            kid_suffix=f"hc-{idx:03d}",
        )
        agents[aid] = (priv, card)

    # Track tip per artefact path.
    tip_for: dict[str, list[str]] = {ARTEFACT: [], ARTICLE11_DOC: []}

    n = len(EDITS)
    for i in range(n):
        artefact, aid, narrative = EDITS[i]
        priv_pem, card = agents[aid]
        content = f"revision {i:03d}: {narrative}\n".encode()
        ts_ns = fixed_iso_to_ns(TIMESTAMPS[i])
        entry = build_entry(
            artefact_path=artefact,
            artefact_kind="config" if artefact.endswith(".yaml") else "file",
            content=content,
            parent_hashes=tip_for[artefact],
            agent_id=aid,
            agent_card_kid=card.kid,
            tool_call_id=f"tc-hc-{i:04d}",
            span_id=f"{i + 0x1000:016x}",
            ts_ns=ts_ns,
        )
        eh = write_entry_with_signature(
            LOG_PATH,
            SIGS_DIR,
            entry,
            priv_pem,
            kid=card.kid,
        )
        tip_for[artefact] = [eh]

    print(f"wrote {n} healthcare entries -> {LOG_PATH}")
    print(f"agent cards                  -> {CARDS_DIR}")
    print(f"signatures                   -> {SIGS_DIR}")


if __name__ == "__main__":
    main()
