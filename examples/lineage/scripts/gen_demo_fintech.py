"""Generate the fintech lineage demo fixtures.

Story: 4 agents (audit-helper, code-reviewer, security-scanner, docs-bot) edit
`src/payments/flow.py` across a 2-week period in early 2026. The primary log
shows a clean, signed chain. A separate `rogue-agent.jsonl` fixture surfaces a
parallel-edit fork that `bernstein-verify forks` is expected to detect.

Run:
    uv run python examples/lineage/scripts/gen_demo_fintech.py

All output is deterministic — same seed + same fixed timestamps every run.
"""

from __future__ import annotations

import random
from pathlib import Path

# Allow running directly: `uv run python examples/lineage/scripts/gen_demo_fintech.py`
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

SEED = 20260101
DEMO_DIR = Path(__file__).resolve().parent.parent / "fintech"
FIXTURES_DIR = DEMO_DIR / "fixtures"
LOG_PATH = FIXTURES_DIR / "log.jsonl"
SIGS_DIR = FIXTURES_DIR / "signatures"
CARDS_DIR = FIXTURES_DIR / "agent-cards"
ROGUE_LOG_PATH = FIXTURES_DIR / "rogue-agent.jsonl"
ROGUE_SIGS_DIR = FIXTURES_DIR / "signatures-rogue"

ARTEFACT = "src/payments/flow.py"

# 4 agents in a fixed order; the rng draws a deterministic keypair per agent.
AGENT_IDS = [
    "agent:audit-helper",
    "agent:code-reviewer",
    "agent:security-scanner",
    "agent:docs-bot",
]

# 30 timestamps across a 2-week window: 2026-01-13 → 2026-01-27 inclusive.
# Hand-picked so chronological order is also the dependency order.
TIMESTAMPS = [
    "2026-01-13T09:00:00Z",
    "2026-01-13T11:30:00Z",
    "2026-01-13T14:15:00Z",
    "2026-01-14T08:45:00Z",
    "2026-01-14T13:20:00Z",
    "2026-01-14T17:05:00Z",
    "2026-01-15T09:30:00Z",
    "2026-01-15T12:00:00Z",
    "2026-01-15T15:45:00Z",
    "2026-01-16T10:10:00Z",
    "2026-01-16T14:35:00Z",
    "2026-01-19T08:00:00Z",
    "2026-01-19T11:25:00Z",
    "2026-01-19T16:40:00Z",
    "2026-01-20T09:15:00Z",
    "2026-01-20T13:50:00Z",
    "2026-01-21T08:30:00Z",
    "2026-01-21T12:45:00Z",
    "2026-01-21T17:20:00Z",
    "2026-01-22T09:55:00Z",
    "2026-01-22T14:10:00Z",
    "2026-01-23T08:25:00Z",
    "2026-01-23T13:40:00Z",
    "2026-01-23T16:00:00Z",
    "2026-01-26T09:30:00Z",
    "2026-01-26T11:45:00Z",
    "2026-01-26T15:15:00Z",
    "2026-01-27T08:50:00Z",
    "2026-01-27T13:05:00Z",
    "2026-01-27T16:30:00Z",
]

# Pseudo file-revision contents: each entry just needs *some* deterministic
# bytes whose sha256 differs across revisions. Real lineage records the hash
# of whatever the editor actually wrote; the demo fakes plausible revisions.
EDIT_NARRATIVE = [
    "audit-helper: add request_id trail to charge_card()",
    "code-reviewer: rename amount_cents -> amount_minor_units",
    "security-scanner: redact PAN before logging",
    "docs-bot: add docstring to refund_flow()",
    "audit-helper: enforce idempotency-key check on retry",
    "code-reviewer: extract _validate_currency helper",
    "security-scanner: tighten TLS pinning on PSP client",
    "docs-bot: document settlement-window edge cases",
    "audit-helper: thread trace context into webhook handler",
    "code-reviewer: split charge_card into validate / authorise",
    "security-scanner: add CSRF token check on /refund",
    "docs-bot: add Mermaid sequence diagram for refund path",
    "audit-helper: emit OTel span for declined transactions",
    "code-reviewer: pull magic numbers into constants module",
    "security-scanner: rotate test-mode PSP API key",
    "docs-bot: cross-link to SOC2 evidence pack section",
    "audit-helper: record reversal reason code on chargeback",
    "code-reviewer: deduplicate retry/backoff loops",
    "security-scanner: add input-fuzz seed for amount parser",
    "docs-bot: clarify three-day capture window",
    "audit-helper: stamp clock_skew_ms on every charge event",
    "code-reviewer: type-narrow PSPResponse via TypeGuard",
    "security-scanner: add log-tamper canary on settlement",
    "docs-bot: link to threat model section 4.2",
    "audit-helper: include scheme_response_code in OTel attrs",
    "code-reviewer: collapse two duplicate guard clauses",
    "security-scanner: confirm PCI-DSS 3.4 redaction coverage",
    "docs-bot: refresh examples for tokenised card flows",
    "audit-helper: tag refund events with finance_period",
    "code-reviewer: final lint sweep + dead-code removal",
]


def main() -> None:
    rng = random.Random(SEED)

    reset_log(LOG_PATH)
    reset_log(ROGUE_LOG_PATH)
    reset_signatures(SIGS_DIR)
    reset_signatures(ROGUE_SIGS_DIR)

    # Deterministic per-agent identities. kid suffix differs per agent so the
    # Agent Cards don't collide on key id.
    agents: dict[str, tuple[str, object]] = {}
    for idx, aid in enumerate(AGENT_IDS, start=1):
        priv, card = make_agent(
            rng,
            agent_id=aid,
            card_dir=CARDS_DIR,
            kid_suffix=f"fintech-{idx:03d}",
        )
        agents[aid] = (priv, card)

    parent_hashes: list[str] = []
    n = len(TIMESTAMPS)
    for i in range(n):
        aid = AGENT_IDS[i % len(AGENT_IDS)]
        priv_pem, card = agents[aid]
        narrative = EDIT_NARRATIVE[i]
        content = f"revision {i:03d}: {narrative}\n".encode()
        ts_ns = fixed_iso_to_ns(TIMESTAMPS[i])
        entry = build_entry(
            artefact_path=ARTEFACT,
            artefact_kind="file",
            content=content,
            parent_hashes=parent_hashes,
            agent_id=aid,
            agent_card_kid=card.kid,
            tool_call_id=f"tc-fintech-{i:04d}",
            span_id=f"{i:016x}",
            ts_ns=ts_ns,
        )
        eh = write_entry_with_signature(
            LOG_PATH,
            SIGS_DIR,
            entry,
            priv_pem,
            kid=card.kid,
        )
        parent_hashes = [eh]

    # Rogue parallel-edit fork. Branches off the entry at index 14, writes a
    # divergent revision from a synthetic agent identity. `bernstein-verify
    # forks` should surface this when fed `rogue-agent.jsonl` alongside the
    # main log.
    fork_parent_index = 14
    # Rebuild the chain up to the fork point so we know that parent's hash.
    fork_parent_hash: list[str] = []
    chain: list[str] = []
    pseudo_parent: list[str] = []
    for i in range(fork_parent_index + 1):
        aid = AGENT_IDS[i % len(AGENT_IDS)]
        priv_pem, card = agents[aid]
        narrative = EDIT_NARRATIVE[i]
        content = f"revision {i:03d}: {narrative}\n".encode()
        ts_ns = fixed_iso_to_ns(TIMESTAMPS[i])
        e = build_entry(
            artefact_path=ARTEFACT,
            artefact_kind="file",
            content=content,
            parent_hashes=pseudo_parent,
            agent_id=aid,
            agent_card_kid=card.kid,
            tool_call_id=f"tc-fintech-{i:04d}",
            span_id=f"{i:016x}",
            ts_ns=ts_ns,
        )
        # Just to compute hashes, not appending to log.
        from bernstein.core.lineage.entry import entry_hash as _eh

        eh = _eh(e)
        chain.append(eh)
        pseudo_parent = [eh]
    fork_parent_hash = [chain[fork_parent_index]]

    rogue_priv, rogue_card = make_agent(
        rng,
        agent_id="agent:rogue-helper",
        card_dir=CARDS_DIR,
        kid_suffix="rogue-001",
    )
    rogue_content = b"revision 015-ROGUE: bypass currency check (suspicious!)\n"
    rogue_entry = build_entry(
        artefact_path=ARTEFACT,
        artefact_kind="file",
        content=rogue_content,
        parent_hashes=fork_parent_hash,
        agent_id="agent:rogue-helper",
        agent_card_kid=rogue_card.kid,
        tool_call_id="tc-fintech-ROGUE-0001",
        span_id="ffffffffffffffff",
        ts_ns=fixed_iso_to_ns("2026-01-20T10:00:00Z"),
    )
    write_entry_with_signature(
        ROGUE_LOG_PATH,
        ROGUE_SIGS_DIR,
        rogue_entry,
        rogue_priv,
        kid=rogue_card.kid,
    )

    print(f"wrote {n} fintech entries -> {LOG_PATH}")
    print(f"wrote 1 rogue entry      -> {ROGUE_LOG_PATH}")
    print(f"agent cards              -> {CARDS_DIR}")
    print(f"signatures               -> {SIGS_DIR}")


if __name__ == "__main__":
    main()
