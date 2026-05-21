"""Generate the EU manufacturer lineage demo fixtures.

Story: an industrial-automation agent fleet maintains the safety-threshold
config for a stamping-press robot cell. The config is classified high-risk
under EU AI Act Annex III §1(a) (machinery safety components). The demo
exercises the 10-year cold-storage round-trip path: export the log, delete
the hot copy, re-import, re-verify.

Agents:
  - safety-threshold-bot: edits the threshold YAML
  - hazard-review-bot:    reviews + approves changes
  - annex-iii-docs-bot:   keeps the Annex III conformity packet in sync

Run:
    uv run python examples/lineage/scripts/gen_demo_eu_mfg.py
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

SEED = 20260301
DEMO_DIR = Path(__file__).resolve().parent.parent / "eu-manufacturer"
FIXTURES_DIR = DEMO_DIR / "fixtures"
LOG_PATH = FIXTURES_DIR / "log.jsonl"
SIGS_DIR = FIXTURES_DIR / "signatures"
CARDS_DIR = FIXTURES_DIR / "agent-cards"

ARTEFACT = "config/robotics/safety_thresholds.yaml"
ANNEX3_DOC = "docs/annex-iii/conformity_packet.md"

AGENT_IDS = [
    "agent:safety-threshold-bot",
    "agent:hazard-review-bot",
    "agent:annex-iii-docs-bot",
]

# 30 entries across early March 2026.
TIMESTAMPS = [
    "2026-03-02T07:00:00Z",
    "2026-03-02T09:30:00Z",
    "2026-03-02T13:15:00Z",
    "2026-03-03T08:00:00Z",
    "2026-03-03T11:40:00Z",
    "2026-03-03T15:25:00Z",
    "2026-03-04T07:50:00Z",
    "2026-03-04T12:10:00Z",
    "2026-03-04T16:35:00Z",
    "2026-03-05T08:20:00Z",
    "2026-03-05T11:55:00Z",
    "2026-03-05T15:30:00Z",
    "2026-03-06T07:45:00Z",
    "2026-03-06T12:00:00Z",
    "2026-03-06T16:15:00Z",
    "2026-03-09T08:10:00Z",
    "2026-03-09T11:25:00Z",
    "2026-03-09T15:50:00Z",
    "2026-03-10T07:30:00Z",
    "2026-03-10T13:00:00Z",
    "2026-03-10T16:40:00Z",
    "2026-03-11T08:00:00Z",
    "2026-03-11T11:35:00Z",
    "2026-03-11T15:10:00Z",
    "2026-03-12T07:55:00Z",
    "2026-03-12T12:25:00Z",
    "2026-03-12T16:00:00Z",
    "2026-03-13T08:30:00Z",
    "2026-03-13T12:50:00Z",
    "2026-03-13T15:45:00Z",
]

EDITS: list[tuple[str, str, str]] = [
    (ARTEFACT, "agent:safety-threshold-bot", "initial threshold table for press cell A1"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "Annex III §1(a) classification rationale"),
    (ARTEFACT, "agent:hazard-review-bot", "reviewer sign-off on A1 thresholds"),
    (ARTEFACT, "agent:safety-threshold-bot", "drop guard-bypass timeout from 250ms to 150ms"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "EN ISO 13849 PLd evidence cross-reference"),
    (ARTEFACT, "agent:hazard-review-bot", "approve timeout reduction after FMEA review"),
    (ARTEFACT, "agent:safety-threshold-bot", "raise e-stop response window monitor to 80ms"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "ISO 12100 hazard analysis attachment"),
    (ARTEFACT, "agent:safety-threshold-bot", "add light-curtain mute-conditions table"),
    (ARTEFACT, "agent:hazard-review-bot", "block mute-conditions: needs human safety officer"),
    (ARTEFACT, "agent:safety-threshold-bot", "revised mute-conditions with PLe override gate"),
    (ARTEFACT, "agent:hazard-review-bot", "approve mute-conditions revision"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "CE marking declaration of conformity draft"),
    (ARTEFACT, "agent:safety-threshold-bot", "press-force cap lowered to 480kN at slow-jog"),
    (ARTEFACT, "agent:hazard-review-bot", "approve press-force cap (validated by maintenance)"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "post-market monitoring plan attached"),
    (ARTEFACT, "agent:safety-threshold-bot", "two-hand control debounce raised to 50ms"),
    (ARTEFACT, "agent:safety-threshold-bot", "two-hand control: add safety category 4 enforcement"),
    (ARTEFACT, "agent:hazard-review-bot", "approve two-hand control changes"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "Article 14 human-oversight evidence"),
    (ARTEFACT, "agent:safety-threshold-bot", "interlocked-guard re-engagement delay 1500ms"),
    (ARTEFACT, "agent:hazard-review-bot", "reviewer: re-engagement delay accepted"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "incident-reporting workflow documented"),
    (ARTEFACT, "agent:safety-threshold-bot", "predictive maintenance: vibration cap 6mm/s"),
    (ARTEFACT, "agent:hazard-review-bot", "vibration cap approved (ISO 10816-3 ref)"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "registry submission package compiled"),
    (ARTEFACT, "agent:safety-threshold-bot", "final calibration: validated on 500 cycles"),
    (ANNEX3_DOC, "agent:annex-iii-docs-bot", "notified body audit response prepared"),
    (ARTEFACT, "agent:safety-threshold-bot", "release-candidate freeze for production"),
    (ARTEFACT, "agent:hazard-review-bot", "final reviewer sign-off - ready for deployment"),
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
            kid_suffix=f"mfg-{idx:03d}",
        )
        agents[aid] = (priv, card)

    tip_for: dict[str, list[str]] = {ARTEFACT: [], ANNEX3_DOC: []}
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
            tool_call_id=f"tc-mfg-{i:04d}",
            span_id=f"{i + 0x2000:016x}",
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

    print(f"wrote {n} EU-mfg entries -> {LOG_PATH}")
    print(f"agent cards              -> {CARDS_DIR}")
    print(f"signatures               -> {SIGS_DIR}")


if __name__ == "__main__":
    main()
