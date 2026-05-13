# Article 12 paragraph → lineage entry mapping

EU Regulation 2024/1689 ("EU AI Act") Article 12 governs automatic
record-keeping for high-risk AI systems. This table maps each paragraph of
Article 12 to specific lineage entries in `fixtures/log.jsonl` that
satisfy the obligation.

Entry indices are 1-based and refer to chronological order in the log.

## Article 12(1) — Automatic recording over the lifetime of the system

> *"High-risk AI systems shall technically allow for the automatic recording
> of events ('logs') over the duration of the lifetime of the system."*

| Anchor | Evidence |
|---|---|
| Every write is logged. | All 32 entries. The lineage log itself is the Article 12(1) artefact. |
| Tamper-evident. | Each entry carries `operator_hmac` (HMAC-SHA256 envelope) **and** an out-of-band detached Ed25519 JWS at `signatures/<aa>/<full>/sha256_<hash>.jws`. |

## Article 12(2)(a) — Identification of risk situations / events

> *"Logging capabilities shall ensure a level of traceability of the AI
> system's functioning … appropriate to the intended purpose."*

| Entry # | Narrative | Why this satisfies 12(2)(a) |
|---|---|---|
| 1 | initial threshold table for chest-pain triage | Anchors the baseline against which subsequent risk decisions are measured. |
| 4 | tighten SpO2 cutoff from 92 to 94 | Captures a risk-relevant parameter change. |
| 9 | calibrate sepsis suspicion weight (+0.15) | Recalibration event traceable to specific agent + ts. |
| 12 | lower fall-risk threshold for >75yo cohort | Sub-cohort risk parameter change. |
| 16 | raise stroke-suspicion flag on FAST positive | Critical-path triage threshold. |
| 21 | add lactate>4 → immediate ICU referral | New escalation rule. |
| 27 | regression: restore SpO2 cutoff bug fix | Risk-correcting rollback recorded as first-class entry. |

## Article 12(2)(b) — Identification of situations that may lead to substantial modification

> *"Logging capabilities shall enable the identification of situations
> that may result in the AI system presenting a risk … or in a substantial
> modification."*

| Entry # | Narrative | Why this satisfies 12(2)(b) |
|---|---|---|
| 7 | add pediatric branch (<12yo) with separate cutoffs | New cohort = substantial modification. |
| 13 | add NEWS2 score override path | New scoring framework integration. |
| 18 | rebalance respiratory-distress thresholds | Multi-parameter recalibration. |
| 25 | incorporate ESI v4 mapping for severity 2 | Triage taxonomy revision. |
| 30 | final calibration: AUROC 0.91 on test set | Substantial revalidation event. |

## Article 12(2)(c) — Monitoring of operation re. Article 26(5)

> *"Logging capabilities shall facilitate post-market monitoring referred
> to in Article 72."*

| Anchor | Evidence |
|---|---|
| Cross-link to audit log. | Every entry carries `tool_call_id`; the audit log records the actual tool invocation, the lineage log records the artefact write. Both are needed for Article 72 post-market monitoring. |
| Continuous time series. | `ts_ns` fields are monotonically non-decreasing within each artefact path; gaps would surface as a fork. |

## Article 12(2)(d) — Monitoring of operation re. natural persons referred to in Article 14(3)

> *"Logging capabilities shall facilitate the monitoring of the operation
> of the high-risk AI system as referred to in Article 14(3)."*

(Article 14(3) = human oversight by natural persons.)

| Entry # | Narrative | Why this satisfies 12(2)(d) |
|---|---|---|
| 14 | Article 11(1)(e) human oversight controls | Documents oversight mechanisms. |
| 2 | redact patient_name / mrn before model input | Oversight: PHI exclusion enforced before any human review. |
| 10 | scrub free-text symptom field for embedded PHI | Oversight surface integrity. |
| 19 | ensure insurance_id stripped from logs | Restricts what the human reviewer can see. |
| 24 | verify no PHI leakage in error responses | Closes the side-channel. |

## Article 12(3) — Logs retained for at least 6 months

> *"The logs … shall be kept by the providers for a period appropriate to
> the intended purpose of the high-risk AI system, of at least six months,
> unless provided otherwise by applicable Union or national law, in
> particular in Union law on the protection of personal data."*

| Mechanism | Evidence |
|---|---|
| Append-only `log.jsonl`. | Source of truth. Never rewritten. |
| Cold-storage export. | See [`../eu-manufacturer/cold-storage-roundtrip.md`](../eu-manufacturer/cold-storage-roundtrip.md) for the 10-year retention path; identical procedure applies here. |
| Compliance pack snapshot. | `expected-pack.zip` is the form an auditor receives. Manifest carries `window.since` / `window.until` and a chain anchor (head HMAC). |

## Article 11 cross-reference

Article 11 ("Technical documentation") is a sibling obligation. The
healthcare demo edits the technical-documentation file directly. Article 11
sub-paragraphs map as follows:

| Entry # | Narrative | Article 11 anchor |
|---|---|---|
| 3 | draft Article 11(1)(a) intended purpose | 11(1)(a) |
| 5 | system architecture diagram | 11(1)(b) |
| 8 | training-data lineage section | 11(1)(c) |
| 11 | risk-management measures | 11(1)(d) |
| 14 | human oversight controls | 11(1)(e) |
| 17 | accuracy metrics table | 11(1)(f) |
| 20 | cybersecurity measures | 11(1)(g) |
| 23 | post-market monitoring plan | 11(1)(h) |
| 26 | annex with version history | 11(2) |
| 29 | update tracking entry | 11(3) |
| 31 | sign-off: ready for conformity assessment | Article 43 conformity assessment input |
