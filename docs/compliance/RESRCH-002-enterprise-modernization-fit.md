# RESRCH-002 — Enterprise modernization-fit gap analysis

Date: 2026-05-09
Owner: Alex Chernysh
Status: research, no code in this PR
Ticket: `.sdd/backlog/closed/RESRCH-002-enterprise-modernization-fit.md` (local-only; `.sdd/` is gitignored)
Source thesis: `pr/research/bernstein/enterprise_discovery.md` (sister repo)

The ticket prefers `.sdd/audit/...` or `docs/research/...`; both are gitignored. Filed under `docs/compliance/` so the deliverable is reviewable in this PR.

## TL;DR

| # | Capability gap | Regulator anchor | FINOS AIGF unblock | Effort | Priority |
|---|----------------|------------------|--------------------|--------|----------|
| 1 | AIGF controls map + reciprocal citation | Cross-cuts Art. 12, DORA Art. 28, ISO 42001 cl. 9, SR 11-7 §V | tool-chain-logic, regulatory-violation, audit-trail-bypass | S (1-2 days, doc only) | P0 |
| 2 | DSSE / in-toto envelope on the audit chain | DORA Art. 9(3) integrity, EU AI Act Art. 12(2)(c) tamper-evidence | inadequate-record-keeping, model-supply-chain | M (3-5 days) | P0 |
| 3 | Standalone (no-bernstein-import) verifier | EU AI Act Art. 19(1) third-party reproducibility, DORA Art. 30 ICT-TPP audit-right | regulator-reviewable-evidence | M (2-4 days) | P0 |
| 4 | Role-based agent execution policies (deny per role) | SR 11-7 §V.4 segregation of duties, ISO 42001 cl. 7.5.3 | unauthorised-tool-invocation | M (3-5 days) | P1 |
| 5 | DORA Art. 8-15 ICT-asset register export | DORA Register-of-Information cycle | inadequate-third-party-evidence | M-L (5-7 days) | P1 |
| 6 | S3 Object Lock / immutable-storage adapter for the Article 12 bundle | Art. 12(3) retention enforceability | retention-bypass | M (3-5 days) | P1 |
| 7 | Self-generated SOC-2-evidence-pack template (user-side, not bernstein-side) | SOC 2 TSC CC2.1 / CC6.1 / CC7.2 / CC9.2 | controls-evidence-mapping | S-M (already partially shipped via `audit_pack.py`; gap is the user-facing template) | P2 |

Spec-only vs prod-tested today (honest):

| Layer | Status |
|-------|--------|
| HMAC-chained audit log | prod, tested |
| Article 12 bundle assembler + zip + retention pin | shipped, in-tree tests, not yet field-tested by an external auditor |
| Article 12 bundle "standalone" verifier | shipped but **imports `bernstein.core.security.article12_bundle`** — not actually standalone |
| Sigstore release attestation (SLSA L1) | **not in CI** — no `attest-build-provenance` workflow on `main` |
| OpenSSF Scorecard badge | **not configured** |
| FINOS AIGF mapping | **does not exist** in repo or docs |
| DORA Art. 8-15 evidence | **does not exist** |
| ISO 42001 / SR 11-7 mapping | **does not exist** |
| Per-role agent deny-list | partial: `claude_permission_profiles.py` has tool deny-lists per role but no agent-level deny (e.g. "security role cannot spawn cloud LLM adapters") |

---

## 1. Current state — what's already shipped

Inventory of regulator-relevant code as of `origin/main` (ref commit `6564221`, 2026-05-09):

| Module | Function | Maturity |
|--------|----------|----------|
| `core/security/audit.py` (506 lines) | HMAC-chained JSONL audit log, key isolated outside `.sdd/`, mode-0600 enforced | Prod |
| `core/security/audit_integrity.py` | Chain verification, gap detection | Prod |
| `core/security/audit_export.py` | JSONL slice export by time range | Prod |
| `core/security/audit_pack.py` (646 lines) | SOC 2 evidence checklist (CC1.1, CC1.2, CC2.1, CC6.1, CC6.6, CC6.7, CC6.8, CC7.2, CC7.4) — Markdown + JSON | Prod, run weekly via `soc2-evidence-nightly.yml` |
| `core/security/article12_bundle.py` (1140 lines) | Article 12 evidence bundle: events.jsonl + data_catalog.json + clause_map.json + manifest.json, deterministic zip, retention pin (10y high-risk / 183d minimum) | Shipped (2026-05-07), unit-tested, deterministic |
| `core/security/eu_ai_act.py` (270 lines) | Task-level risk classifier (minimal/limited/high/unacceptable), keyword heuristics, per-task assessment log | Prod |
| `core/security/compliance_policies.py` | Compliance-as-code policy library: SOC 2, ISO 27001, PCI-DSS, NIST 800-53 (HIPAA via separate module) | Prod |
| `core/security/soc2_report.py` (479 lines) | SOC 2 TSC mapping with evidence summaries + Merkle-root attestation | Prod |
| `core/security/sigstore_attestation.py` | Per-task Sigstore/Rekor keyless attestation with Ed25519 fallback | Shipped, fallback-tested |
| `core/security/capability_matrix.py` | Lethal-trifecta enforcement (PRIVATE_DATA × UNTRUSTED_INPUT × EXTERNAL_COMM) | Prod |
| `core/security/claude_permission_profiles.py` | Per-role allowedTools / denyPatterns for Claude Code agents | Prod |
| `core/security/rbac.py` | API-route RBAC (admin/operator/viewer) | Prod |
| `core/security/data_residency.py` | Per-tenant region policy + write-time check | Prod |
| `core/security/sbom.py` | SBOM generation | Prod |
| `core/persistence/lineage.py` | Per-artefact lineage trail with `regulatory_class` + customer-Ed25519 signature (schema v2) | Prod |
| `core/persistence/lineage_signer.py` | Customer-controlled detached signing key | Prod |
| `core/persistence/disk_retention.py` | Local retention enforcer (calendar-day rotation) | Prod |
| `.github/workflows/soc2-evidence-nightly.yml` | Weekly SOC 2 evidence pack run + artefact upload | Prod |

That's a 95-file `core/security/` tree, ~8000 lines of compliance-adjacent Python. The substrate is real. The gap is documentation + a small number of integration features that would let a regulator-class buyer pick this up.

---

## 2. EU AI Act Article 12 conformance — clause-by-clause gap

Reference: Regulation (EU) 2024/1689, Art. 12 ("Record-keeping") + Art. 19(1) ("Automatically generated logs").

| Clause | Requirement | Bernstein artefact today | Gap |
|--------|-------------|--------------------------|-----|
| 12(1) | Automatic recording of events ("logs") over the lifetime of the system | `events.jsonl` inside the bundle, daily-rotated `<sdd>/audit/*.jsonl` | None on the recording side. **Lifetime-coverage gap**: there is no documented expectation that a single bernstein deployment covers the full lifetime of one agent fleet — this has to be operator-stated. |
| 12(2)(a) | Identification of situations that may result in the AI system presenting a risk within Art. 79(1) or in a substantial modification | Bundle pulls `event_type` + `outcome` fields; `eu_ai_act.py:assess_task()` classifies tasks | **Substantial-modification detection** is not implemented. We do not flag the case where a task changes the agent fleet's effective capabilities (new tool, new MCP server). Auditor will ask "how does the log surface that?". |
| 12(2)(b) | Facilitation of post-market monitoring | `data_catalog.json` aggregates per-resource activity counts | None at the artefact level. **Gap**: no defined "post-market monitoring report" cadence — the bundle is on-demand only. |
| 12(2)(c) | Monitoring of operation of high-risk AI systems referred to in Art. 26(5) | `chain_anchor` + HMAC chain | **Tamper-evidence gap**: HMAC alone is single-key. Art. 26(5) implies third-party-verifiable monitoring → DSSE/in-toto envelope or Sigstore-anchored chain checkpoint required for a regulator to verify without bernstein's HMAC key. |
| 12(3) | Logs kept ≥6 months unless otherwise provided; 10y for high-risk under Art. 19(1) | `RetentionPin` in `manifest.json` enforces 10y / 183d at bundle-build time | **Storage-enforceability gap**: pin is metadata only. Article 12(3) needs an immutable backend (S3 Object Lock, WORM Postgres, `chattr +i`) to make the retention enforceable. Currently the operator can `rm` the bundle and the pin disappears with it. |
| 19(1) | Providers keep automatically generated logs for at least 6 months | Same as 12(3) | Same gap. Plus: we do not produce a **bundle index** the operator can hand to an auditor showing "every bundle for this system over the last six months", which is what the auditor actually wants. |

**Verdict:** Bernstein satisfies 12(1) and 12(2)(b) cleanly. 12(2)(a), 12(2)(c), 12(3), 19(1) have specific gaps the auditor will probe in the first hour of a conformity assessment.

---

## 3. DORA Art. 8-15 ICT-risk evidence gap

Reference: Regulation (EU) 2022/2554, Articles 8-15 (ICT risk management framework + Register of Information).

| Article | Requirement (paraphrased) | Bernstein artefact today | Gap |
|---------|---------------------------|--------------------------|-----|
| Art. 8 | ICT-asset inventory + classification | None | **Not produced.** Bernstein has agent registry + adapter list (~40 adapters), but no "inventory export" suitable for the DORA Register-of-Information cycle. |
| Art. 9(3) | Integrity of ICT systems / data, including tamper-evident records | HMAC chain | Same gap as Art. 12(2)(c) — single-key, no DSSE envelope. |
| Art. 10 | Detection — anomalous activity | `core/security/security_correlation.py`, denial tracker | Partial. No pre-built detection profile aligned to DORA's "anomalous-activity" language. |
| Art. 11 | Response and recovery | `core/security/security_incident_response.py` | Module exists; no DORA-specific incident classification (major / significant / non-major). |
| Art. 12 | ICT business-continuity policies | Outside bernstein's scope (ops, not orchestrator). | Acknowledge in docs; do not over-claim. |
| Art. 13 | Learning and evolving (post-incident) | None as a reportable artefact | Gap. We have run-replay but not a "post-incident report" template aligned to DORA. |
| Art. 14-15 | Communication strategy + crisis management | Outside scope | Same. |
| Art. 28 | ICT third-party risk | None | **High-priority gap.** A bernstein customer is a DORA-regulated financial entity; bernstein itself sits in the third-party stack. The customer's CTPP (Critical Third Party Provider) compliance officer needs an Art. 28-shaped attestation pack from bernstein. We do not produce one. |

**Verdict:** DORA Art. 8 (asset inventory), Art. 9(3) (tamper-evidence envelope), Art. 28 (TPP attestation) are the three gaps that disqualify bernstein from the EU bank vendor-DD packet today. Art. 10/11/13 have substrate but no DORA-specific output format.

---

## 4. FINOS AI Governance Framework controls map

Reference: github.com/finos/ai-governance-framework (16 controls × 14 risks, Community Specification License, actively soliciting controls-implementation PRs).

The map below is the artefact RESRCH-002 §1 calls for. Two-column structure: AIGF row × bernstein support. Italicised cells are **gaps** (no current support).

### 4.1 AIGF risk inventory × bernstein

| AIGF risk ID | Risk title | Bernstein support today | Gap |
|--------------|-----------|--------------------------|-----|
| AIR-DA-001 | Inadequate data anonymisation | `dlp_scanner_v2.py`, `pii_output_gate.py`, `differential_privacy.py` | Partial — no AIGF-shaped report |
| AIR-DA-002 | Cross-border data transfer | `data_residency.py` per-tenant region policy | Documentable mapping ready |
| AIR-OP-001 | Tool-chain logic vulnerabilities | `capability_matrix.py` lethal-trifecta enforcement | **High-fit gap: this is the single strongest AIGF mapping bernstein has. Not currently surfaced as AIGF evidence.** |
| AIR-OP-002 | Inadequate record-keeping for AI decisions | `article12_bundle.py` + `audit.py` HMAC chain | High-fit; needs DSSE envelope + standalone verifier to fully clear |
| AIR-OP-003 | Lack of explainability | Deterministic Python orchestration (zero LLM tokens on coordination) — *structurally removes the explainability gap that LLM-coordinated frameworks have* | **High-fit gap: this is the second-strongest AIGF angle. Need an explicit "deterministic-orchestrator" technical note.** |
| AIR-OP-004 | Model supply-chain compromise | `sigstore_attestation.py` per-task; `agent_card_signer.py` + JWKS | Partial — release artefacts not Sigstore-signed |
| AIR-OP-005 | Hallucination in production | Out of scope (verifier gate is task-level not model-level). Be honest. | Document as out-of-scope; do not over-claim. |
| AIR-OP-006 | Inadequate human oversight | `approval.py`, `dual_approval.py`, `plan_approval.py`, `claude_permission_profiles.py` | Strong — needs AIGF-style write-up |
| AIR-OP-007 | Regulatory-violation risk via missing audit trails | Same as AIR-OP-002 | High-fit; same gaps |
| AIR-RC-001 | Bias amplification | Out of scope | Document as out-of-scope |
| AIR-RC-002 | Sensitive-data leakage | `dlp_scanner_v2.py`, `pii_output_gate.py`, `sensitive_data.py`, `secrets.py` | Strong |
| AIR-RC-003 | Prompt injection | `core/security/owasp_asi_detectors.py` (ASI-class detectors), `capability_matrix.py` | Strong |
| AIR-RC-004 | Unauthorised tool invocation | `command_allowlist.py`, `command_policy.py`, `claude_permission_profiles.py` | Partial — per-role agent-level deny-list missing |
| AIR-RC-005 | Inadequate third-party evidence (vendor-DD) | None as a packaged artefact | **Gap — DORA Art. 28 / SR 11-7 §VI overlap.** |

### 4.2 AIGF control inventory × bernstein

Cross-walked against the 16 controls listed in finos/ai-governance-framework as of 2026-05-09 (`CONTROLS.md` + the airgovframework.finos.org rendered site). Where the AIGF control title differs slightly from what the repo published, I cite the closest-match control.

| AIGF control | Bernstein support | Gap |
|--------------|--------------------|-----|
| CTRL-AUDIT-TRAIL | `audit.py` + `article12_bundle.py` | Need DSSE envelope + reciprocal AIGF citation in our docs |
| CTRL-DATA-LINEAGE | `core/persistence/lineage.py` schema v2 with `regulatory_class` + customer-signature | Needs AIGF-named-control mapping in `regulatory-lineage.md` |
| CTRL-MODEL-SUPPLY-CHAIN | `sigstore_attestation.py` | Releases not signed; need workflow |
| CTRL-TOOL-INVENTORY | Adapter registry (~40 adapters) + capability YAML | No exportable inventory in AIGF or DORA Art. 8 shape |
| CTRL-HUMAN-OVERSIGHT | `approval.py`, `dual_approval.py`, `plan_approval.py` | Strong, document the mapping |
| CTRL-ACCESS-CONTROL | `rbac.py`, `claude_permission_profiles.py`, `permission_*` modules | Strong, document |
| CTRL-DATA-RESIDENCY | `data_residency.py` | Strong, document |
| CTRL-PII-PROTECTION | `dlp_scanner_v2.py`, `pii_output_gate.py` | Strong, document |
| CTRL-PROMPT-INJECTION-DEFENCE | `owasp_asi_detectors.py`, `capability_matrix.py` (lethal-trifecta) | Strong; this is bernstein's strongest single AIGF angle |
| CTRL-INCIDENT-RESPONSE | `security_incident_response.py`, `quarantine.py` | Strong substrate; no DORA-shaped incident class |
| CTRL-SEGREGATION-OF-DUTIES | `rbac.py` + `claude_permission_profiles.py` | **Per-role agent deny-list missing** (see §5) |
| CTRL-RETENTION | `article12_bundle.py:RetentionPin` | Pin only; no immutable backend |
| CTRL-ENCRYPTION-AT-REST | `state_encryption.py`, `vault/`, `vault_injector.py` | Strong, document |
| CTRL-ENCRYPTION-IN-TRANSIT | mTLS (`cluster_tls`), TLS pinning | Strong, document |
| CTRL-DEPENDENCY-INTEGRITY | `sbom.py`, `license_scanner.py`, wheelhouse verify | Strong, document |
| CTRL-CHANGE-MANAGEMENT | WAL + audit chain + git provenance (`commit_provenance.py`) | Strong, document |

**Net of the cross-walk:** 13 of 16 AIGF controls have strong substrate. The three soft spots are (a) reciprocal AIGF naming in our own docs (a doc fix), (b) DSSE/Sigstore on releases (a workflow fix), (c) per-role agent deny-list (a small code task).

---

## 5. Role-based agent execution policies — the missing piece

Ticket §5 calls out the enterprise need: deny-list certain agents per role (e.g., `security` role cannot spawn cloud LLMs).

Current state, after reading `core/security/claude_permission_profiles.py`, `core/security/rbac.py`, `core/security/command_allowlist.py`, `core/agents/` and `core/planning/role_resolver.py`:

| Surface | What's there | What's missing |
|---------|--------------|----------------|
| Tool allow/deny per role | `claude_permission_profiles.py` ships profiles for `backend`/`frontend`/`qa`/`security`/`docs`/`reviewer`/`devops`. Each profile carries `allowed_tools`, `disallowed_tools`, `deny_patterns`. | Tool granularity only. Cannot say "security role cannot spawn the `claude_routine` (cloud) adapter" or "qa role cannot use Devin". |
| Adapter (agent) allow/deny per role | None. The adapter registry is global. | The exact enterprise requirement. |
| API-route RBAC | `rbac.py` admin/operator/viewer | OK at API layer; not at orchestrator-spawn time. |
| Per-task policy override | `policy_templates.py` allows org admins to merge-overlay `bernstein.yaml` per task | YAML-driven — useful but not the deny-list layer. |

**Concrete missing piece (S-effort, ~1-2 days):**

A `core/security/role_adapter_policy.py` that:

1. Reads a per-role adapter deny-list from `bernstein.yaml` (e.g., `roles.security.deny_adapters: [claude_routine, devin_terminal, codex_cloudflare]`).
2. Hooks into the orchestrator's adapter-selection step (the place that resolves `agent.adapter_id` for a task) and refuses to spawn a denied adapter for the task's effective role.
3. Emits a structured audit event (`event_type="role_adapter_denied"`) to the HMAC chain.
4. Adds a CLI verb `bernstein policy show --role security` that prints the effective adapter allowlist.

This is the single concrete code task that maps to AIGF CTRL-SEGREGATION-OF-DUTIES + SR 11-7 §V.4. It is a one-PR change with clear test surface (deny + bypass-attempt + audit-event).

---

## 6. SR 11-7 + ISO 42001 cross-walk

These are not in scope for ticket §1-§5 but the ticket explicitly names them. Short version below; not load-bearing for the priority list.

| SR 11-7 section | Bernstein angle | Status |
|-----------------|-----------------|--------|
| §III Model risk taxonomy | `eu_ai_act.py:RiskLevel` + per-task assessment log | Adjacent — we classify *tasks* not *models* |
| §IV Model documentation | `compliance/eu_ai_act.py:ComplianceEngine` Annex IV doc | Strong |
| §V Model implementation — segregation of duties | `rbac.py` + `claude_permission_profiles.py` | Partial (see §5 gap) |
| §VI External resources / vendor management | None | DORA Art. 28 overlap |
| §VII Model monitoring | HMAC chain + Article 12 bundle | Strong |
| §VIII Model risk reporting | `compliance_report.py` | Strong |

| ISO 42001 clause | Bernstein angle | Status |
|------------------|-----------------|--------|
| 4 Context of the organisation | Out of scope | OK |
| 5 Leadership / AI policy | Out of scope | OK |
| 6 Planning — AI risk assessment | `eu_ai_act.py` | Partial |
| 7.5.3 Control of documented information | `audit.py` + `article12_bundle.py` | Strong |
| 8 Operation — AI lifecycle | Adapter pipeline + lineage v2 | Strong |
| 9.1 Performance evaluation | `compliance_report.py`, `audit_pack.py` | Strong |
| 9.2 Internal audit | Self-generated SOC 2 pack template | Partial — see Backlog item 7 |
| 10 Improvement / nonconformities | `security_incident_response.py` | Substrate, no ISO-shaped report |

---

## 7. SOC 2 self-evidence template (user-side, not bernstein-side)

Per ticket §4 and the §1 verdict in `enterprise_discovery.md`: bernstein-the-vendor cannot get its own SOC 2 (solo, IL-based). The asset is **bernstein helps the user generate evidence for *their* SOC 2**.

The substrate already exists: `core/security/audit_pack.py:generate_audit_pack()` + `soc2-evidence-nightly.yml`. What's missing is a user-facing **template** that documents which trace fields populate which TSC control.

Sketch (skeleton — markdown-only, no code):

```text
# SOC 2 Type II — bernstein-generated evidence pack (user-side template)

## How to use this template
1. Run `bernstein audit pack --soc2 --output ./soc2-evidence/`.
2. The command emits `soc2-evidence/checklist.md` + per-control artefacts.
3. Map each row below to your auditor's request list.

## Trust Service Criteria mapping

| TSC control | Description | bernstein artefact (file) | bernstein source field |
| CC2.1       | Communicate internal-control responsibilities | `.sdd/audit/<date>.jsonl` | `event_type`, `actor`, `resource_*` |
| CC6.1       | Logical access controls | `.sdd/audit/*.jsonl` + `core/credential_scoping.py` (policy file) | `event_type=auth.*`, `outcome` |
| CC6.6       | Boundary protection | `.sdd/runtime/cluster_tls/*.log` | TLS validation log + cert SHA |
| CC6.7       | Capability-matrix run | `.sdd/runtime/spawn_capabilities/*.json` | `tools[]`, `violations[]` |
| CC6.8       | Wheelhouse verify | `.sdd/runtime/wheelhouse/verify-*.json` | `wheels_checked`, `all_valid` |
| CC7.2       | System monitoring | `.sdd/audit/*.jsonl` (HMAC chain tail) | `hmac` (tail digest) |
| CC9.1/9.2   | Processing integrity | `.sdd/runtime/wal/*.wal.jsonl` | Merkle root + HMAC |

## Auditor-facing summary
- Chain integrity: bernstein audit verify --since … --until …
- Retention: `bernstein compliance retention status` (Art. 12(3))
- Evidence freshness: each row is timestamped + flagged STALE if older than 30 days.
```

This template is a doc-only deliverable. The CLI surface that backs it ships today.

---

## 8. Prioritised actionable backlog (top 3)

### #1 — AIGF controls map + reciprocal citation (P0, S, ~1-2 days)

**What.** Add `docs/compliance/finos-aigf-mapping.md` matching §4 of this doc. Open an issue at github.com/finos/ai-governance-framework with the 200-word body in `enterprise_discovery.md` §6 step 1. Add reciprocal citation to bernstein's `docs/index.md`, `README.md`, `docs/security/AUDIT.md`.

**Why first.** Single highest-EV move per `enterprise_discovery.md` §3 #1 (60-70% non-member-PR merge rate; 25% follow-on engagement from a FINOS-member-firm engineer within 90 days). Zero code. Unblocks every downstream conversation.

**Regulator anchor.** EU AI Act Art. 12 + DORA Art. 9(3) + ISO 42001 cl. 9.2 + SR 11-7 §V cross-cut.

**FINOS AIGF unblock.** All 16 controls become formally citable from within bernstein docs.

**Effort.** S — operator can produce the AIGF-issue body, the bernstein-side mapping doc, and the reciprocal citation in a single PR.

**Acceptance.** (a) PR opened on `finos/ai-governance-framework` with a non-marketing technical body; (b) `docs/compliance/finos-aigf-mapping.md` lives in main; (c) `README.md` cites AIGF in the security paragraph; (d) HN/AIGF readers find the same mapping in both directions.

### #2 — DSSE/in-toto envelope on the audit chain + standalone verifier (P0, M, ~5-7 days)

**What.** Wrap each Article 12 bundle (and optionally each per-event chain checkpoint) in a DSSE envelope (in-toto attestation type) so a third-party auditor can verify the bundle without bernstein's HMAC key. Make the standalone verifier truly standalone: rewrite `scripts/verify_article12_bundle.py` to be pure-stdlib, no `bernstein.*` imports. Sign the envelope with Sigstore (keyless via Fulcio + Rekor) for releases.

**Why second.** Currently the bundle's "tamper-evidence" is single-key HMAC. EU AI Act Art. 12(2)(c) and DORA Art. 9(3) explicitly want third-party-verifiable monitoring. DSSE + Sigstore = the open standard for this. The current "standalone verifier" claim in `core/security/article12_bundle.py:497` is **not actually standalone** — `scripts/demo_article12_export.py` imports `bernstein.core.security.article12_bundle.verify_bundle`. This will fail an auditor's first reproducibility test.

**Regulator anchor.** EU AI Act Art. 12(2)(c), Art. 19(1) third-party reproducibility; DORA Art. 9(3) integrity; AIGF CTRL-AUDIT-TRAIL + CTRL-MODEL-SUPPLY-CHAIN.

**FINOS AIGF unblock.** Closes the "audit-trail-bypass" risk delta (AIR-OP-002 / AIR-OP-007). Also unblocks `enterprise_discovery.md` §6 step 4 (SLSA L1 + Sigstore release attestation).

**Effort.** M — DSSE envelope is an in-toto v1.0 spec mapping (~200 lines), Sigstore release workflow is a 4-line GitHub Action add, standalone verifier rewrite is ~150 lines (pure stdlib + a JWKS reader).

**Acceptance.** (a) `bernstein compliance eu-ai-act --article 12 --output bundle.zip` produces a bundle whose `manifest.json` carries an in-toto-v1 DSSE envelope; (b) `python scripts/verify_article12_bundle.py bundle.zip --jwks bernstein.jwks` runs on Python 3.10 stdlib, no bernstein imports; (c) `release-major-minor.yml` emits a Sigstore-signed `attest-build-provenance` artefact per release.

### #3 — Per-role adapter deny-list (P1, M, ~3-5 days)

**What.** Implement `core/security/role_adapter_policy.py` per §5 of this doc. Wire it into the orchestrator's adapter-selection step. Emit `event_type="role_adapter_denied"` audit events. Ship `bernstein policy show --role <name>`.

**Why third.** The single concrete code-side gap that maps directly to AIGF CTRL-SEGREGATION-OF-DUTIES, SR 11-7 §V.4, and ISO 42001 cl. 7.5.3. Closes the "security role can't spawn cloud LLMs" enterprise-need bullet in the ticket. Small, well-scoped, testable.

**Regulator anchor.** SR 11-7 §V.4 (segregation of duties); ISO 42001 cl. 7.5.3 (control of documented information / role-based access); AIGF CTRL-SEGREGATION-OF-DUTIES; AIR-RC-004 (unauthorised tool invocation).

**FINOS AIGF unblock.** Closes the only AIGF segregation-of-duties row that currently has only "partial" support.

**Effort.** M — one new module (~150-200 lines), one adapter-selection hook (~50 lines), one CLI verb, ~3-5 unit tests.

**Acceptance.** (a) `bernstein.yaml` accepts `roles.<name>.deny_adapters: [...]`; (b) orchestrator refuses to spawn a denied adapter for that role and emits an audit event; (c) `bernstein policy show --role security` lists the effective allowlist; (d) doc page at `docs/security/role-adapter-policy.md` cites AIGF CTRL-SEGREGATION-OF-DUTIES + SR 11-7 §V.4.

---

## 9. Anti-overclaim notes

For interview / CV / cover-letter prep:

- **OK to claim now:** "bernstein ships an HMAC-chained, deterministic Article 12 evidence bundle with explicit clause mapping; substrate covers 13 of 16 AIGF controls; ~95-file security/ tree includes lethal-trifecta enforcement, lineage v2 with customer signatures, Sigstore per-task attestation, SOC 2 evidence pack run weekly in CI."
- **Do NOT claim yet:** "Article 12 bundle is third-party verifiable" (it's HMAC-only — DSSE/Sigstore work pending), "FINOS AIGF reference implementation" (until the reciprocal mapping ships), "SOC 2 Type II" (operator constraint — solo/IL).
- **Caveats to state proactively:** the standalone verifier is currently not standalone (imports `bernstein.core.security.article12_bundle`); release artefacts are not Sigstore-signed; per-role agent deny-list is at tool granularity only.
- **The 31-adapter / MCP / worktree iso / HMAC audit framing from `feedback_bernstein_pypi_numbers` memory holds.** Lead with architecture facts, not download numbers.

---

## 10. References

- Source thesis: `pr/research/bernstein/enterprise_discovery.md`, §1 (verdict), §3 #1 (FINOS AIGF), §3 #4 (EU AI Act public comment), §6 (manual playbook).
- EU AI Act: Regulation (EU) 2024/1689, eur-lex.europa.eu/eli/reg/2024/1689.
- DORA: Regulation (EU) 2022/2554, eur-lex.europa.eu/eli/reg/2022/2554.
- FINOS AI Governance Framework: github.com/finos/ai-governance-framework, air-governance-framework.finos.org.
- ISO/IEC 42001:2023, AI Management System.
- US Federal Reserve SR 11-7, "Guidance on Model Risk Management".
- in-toto / DSSE: github.com/in-toto/attestation, github.com/secure-systems-lab/dsse.
- Sigstore: sigstore.dev; github.com/sigstore/cosign.
- Internal: `.sdd/backlog/closed/2026-05-07-feat-eu-ai-act-article-12-pack.md`.
