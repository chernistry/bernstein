# EU manufacturer demo — robotic safety thresholds

## TL;DR

| Item | Value |
|---|---|
| Org | Bavarian Tooling GmbH |
| Window | 2026-03-02 → 2026-03-13 |
| Primary artefact | `config/robotics/safety_thresholds.yaml` |
| Secondary artefact | `docs/annex-iii/conformity_packet.md` |
| Classification | EU AI Act Annex III §1(a) — high-risk machinery safety component |
| Agents | safety-threshold-bot, hazard-review-bot, annex-iii-docs-bot |
| Entries | 30 |
| Output | `expected-pack.zip` + `cold-storage-roundtrip.md` |

## The procurement story

Bavarian Tooling GmbH ships stamping-press control software classified as
high-risk under EU AI Act Annex III §1(a) (safety component of regulated
machinery). The CE-marking notified body asks for:

1. Article 11 technical documentation (Annex IV §1–9).
2. Article 12 event log retained for the **lifetime of the equipment**
   (typically 10+ years for industrial machinery).
3. Article 17 quality-management evidence — including who can change
   safety thresholds and how those changes are reviewed.

The four-eyes review model lives inside the lineage chain: every
`safety-threshold-bot` write must be followed by a `hazard-review-bot`
sign-off entry that names the threshold-bot entry as its parent.

## What's in `fixtures/`

| Path | Purpose |
|---|---|
| `log.jsonl` | 30 entries: threshold edits, reviewer sign-offs, Annex III doc edits. |
| `signatures/...` | Detached Ed25519 JWS per entry. |
| `agent-cards/` | 3 Agent Cards. |

## 10-year retention round-trip

See [`cold-storage-roundtrip.md`](./cold-storage-roundtrip.md) for the
export → delete-hot-copy → re-import → re-verify procedure. The demo
exercises the path that closes the notified body's "where will this evidence
be in 10 years" question.

## How to regenerate

```bash
uv run python examples/lineage/scripts/gen_demo_eu_mfg.py
uv run python examples/lineage/scripts/build_expected_pack.py
```

Deterministic — seed `20260301`, fixed UTC timestamps.

## How to run the demo

```bash
make -C examples/lineage demo-eu-mfg
```

Invokes:

1. `bernstein compliance pack` over the EU-mfg fixtures.
2. `bernstein-verify pack` on the bundle.
3. Cold-storage round-trip (manual; see `cold-storage-roundtrip.md`).

## Agents

| Agent ID | Role |
|---|---|
| `agent:safety-threshold-bot` | Proposes safety-threshold edits. |
| `agent:hazard-review-bot` | Reviews + signs off (four-eyes principle). |
| `agent:annex-iii-docs-bot` | Maintains the Annex III conformity packet. |
