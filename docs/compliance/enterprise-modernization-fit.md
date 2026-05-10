# Enterprise modernization-fit gap analysis

Audience: a regulator-class buyer or compliance lead asking "is this thing
ready for an EU AI Act / DORA / SR 11-7 / FINOS AIGF assessment". This page
walks the substrate already shipped, the gaps that an auditor will probe in
the first hour, and the prioritised work list that closes them.

For the active control map, see
[FINOS AIGF mapping](finos-aigf-mapping.md). This page is the gap
analysis the AIGF map cross-references.

## TL;DR

| # | Capability | Regulator anchor | AIGF angle | Effort | Priority |
|---|-----------|------------------|-----------|--------|----------|
| 1 | AIGF controls map + reciprocal citation | EU AI Act Art. 12, DORA Art. 28, ISO 42001 cl. 9, SR 11-7 §V | tool-chain-logic, audit-trail-bypass | S (1-2 days, doc only) | P0 |
| 2 | DSSE / in-toto envelope on the audit chain | DORA Art. 9(3), EU AI Act Art. 12(2)(c) | inadequate-record-keeping, model-supply-chain | M (3-5 days) | P0 |
| 3 | Standalone (no-bernstein-import) verifier | EU AI Act Art. 19(1), DORA Art. 30 | regulator-reviewable-evidence | M (2-4 days) | P0 |
| 4 | Role-based agent execution policies (deny per role) | SR 11-7 §V.4, ISO 42001 cl. 7.5.3 | unauthorised-tool-invocation | M (3-5 days) | P1 |
| 5 | DORA Art. 8-15 ICT-asset register export | DORA Register-of-Information cycle | inadequate-third-party-evidence | M-L (5-7 days) | P1 |
| 6 | S3 Object Lock / immutable-storage adapter for the Article 12 bundle | EU AI Act Art. 12(3) | retention-bypass | M (3-5 days) | P1 |
| 7 | Self-generated SOC 2 evidence-pack template | SOC 2 TSC CC2.1 / CC6.1 / CC7.2 / CC9.2 | controls-evidence-mapping | S-M | P2 |

Items 1, 2, 3, and 4 have all shipped as of v1.10.5. The list below is the
current snapshot, not a roadmap.

Spec-only vs prod-tested today (honest):

| Layer | Status |
|-------|--------|
| HMAC-chained audit log | Prod, tested. |
| Article 12 bundle (deterministic zip + retention pin + clause map) | Shipped, in-tree tests, not yet field-tested by an external auditor. |
| Article 12 bundle "standalone" verifier | Shipped via `tools/verify_audit_dsse.py`. Pure stdlib + `cryptography`; no `bernstein.*` import. |
| DSSE / in-toto envelope on the bundle | Shipped. Round-trip + tamper tests in place. |
| Sigstore release attestation (SLSA L3) | Wired in CI via `actions/attest-build-provenance@v2`. |
| FINOS AIGF mapping | Shipped at [`finos-aigf-mapping.md`](finos-aigf-mapping.md). |
| OpenSSF Scorecard badge | Not configured. |
| DORA Art. 8-15 evidence pack | Does not exist as a packaged artefact. |
| ISO 42001 / SR 11-7 mapping | Documented as cross-walk inside this page. |
| Per-role agent deny-list | Shipped. Empty allow-list = back-compat all-allowed; hooks `bernstein.adapters.registry.get_adapter` so every spawn site is covered. |

---

## 1. Current state — what's already shipped

Inventory of regulator-relevant code at v1.10.5:

| Module | Function | Maturity |
|--------|----------|----------|
| `core/security/audit.py` | HMAC-chained JSONL audit log, key isolated outside `.sdd/`, mode-0600 enforced. | Prod |
| `core/security/audit_integrity.py` | Chain verification, gap detection. | Prod |
| `core/security/audit_export.py` | JSONL slice export by time range. | Prod |
| `core/security/audit_pack.py` | SOC 2 evidence checklist (CC1.1, CC1.2, CC2.1, CC6.1, CC6.6, CC6.7, CC6.8, CC7.2, CC7.4) — Markdown + JSON. | Prod, run weekly via `soc2-evidence-nightly.yml`. |
| `core/security/article12_bundle.py` | EU AI Act Article 12 evidence bundle: events.jsonl + data_catalog.json + clause_map.json + manifest.json, deterministic zip, retention pin (10y high-risk / 183d minimum). | Prod, unit-tested, deterministic. |
| `core/security/audit_dsse.py` | DSSE / in-toto v1 envelope wrapper for the Article 12 bundle. | Prod, tamper-tested. |
| `core/security/eu_ai_act.py` | Task-level risk classifier (minimal/limited/high/unacceptable), keyword heuristics, per-task assessment log. | Prod |
| `core/security/compliance_policies.py` | Compliance-as-code policy library: SOC 2, ISO 27001, PCI-DSS, NIST 800-53. HIPAA via separate module. | Prod |
| `core/security/soc2_report.py` | SOC 2 TSC mapping with evidence summaries + Merkle-root attestation. | Prod |
| `core/security/sigstore_attestation.py` | Per-task Sigstore/Rekor keyless attestation with Ed25519 fallback. | Shipped, fallback-tested. |
| `core/security/capability_matrix.py` | Lethal-trifecta enforcement (PRIVATE_DATA × UNTRUSTED_INPUT × EXTERNAL_COMM). | Prod |
| `core/security/claude_permission_profiles.py` | Per-role allowedTools / denyPatterns for Claude Code agents. | Prod |
| `core/security/role_adapter_policy.py` | Per-role adapter deny-list at orchestrator-spawn time. | Prod |
| `core/security/rbac.py` | API-route RBAC (admin/operator/viewer). | Prod |
| `core/security/data_residency.py` | Per-tenant region policy + write-time check. | Prod |
| `core/security/sbom.py` | SBOM generation. | Prod |
| `core/security/lineage_kms.py` | KMS adapter dispatch for lineage v2 customer signatures. File / env / HSM (PKCS#11 / AWS KMS / GCP Cloud KMS / Azure Key Vault subclass shape). | Prod |
| `core/persistence/lineage.py` | Per-artefact lineage trail with `regulatory_class` + customer-Ed25519 signature (schema v2). | Prod |
| `core/persistence/lineage_signer.py` | Customer-controlled detached signing key. | Prod |
| `core/persistence/disk_retention.py` | Local retention enforcer (calendar-day rotation). | Prod |
| `tools/verify_audit_dsse.py` | Standalone DSSE verifier — Python stdlib + `cryptography` only, no `bernstein.*` import. | Prod, subprocess-isolated test enforces no bernstein import. |
| `.github/workflows/soc2-evidence-nightly.yml` | Weekly SOC 2 evidence pack run + artefact upload. | Prod |

The compliance-adjacent surface is real: a 95-file `core/security/` tree of
the order of 8000 lines of Python. The remaining gap is documentation +
specific integration features that let a regulator-class buyer pick this up.

---

## 2. EU AI Act Article 12 conformance — clause-by-clause gap

Reference: Regulation (EU) 2024/1689, Art. 12 ("Record-keeping") + Art. 19(1)
("Automatically generated logs").

| Clause | Requirement | Bernstein artefact | Gap |
|--------|-------------|--------------------|-----|
| 12(1) | Automatic recording of events ("logs") over the lifetime of the system | `events.jsonl` inside the bundle, daily-rotated `<sdd>/audit/*.jsonl` | None on the recording side. **Lifetime-coverage gap**: there is no documented expectation that a single bernstein deployment covers the full lifetime of one agent fleet — this has to be operator-stated. |
| 12(2)(a) | Identification of situations that may result in the AI system presenting a risk within Art. 79(1) or in a substantial modification | Bundle pulls `event_type` + `outcome`; `eu_ai_act.py:assess_task()` classifies tasks. | **Substantial-modification detection** is not implemented. The log does not flag the case where a task changes the agent fleet's effective capabilities (new tool, new MCP server). |
| 12(2)(b) | Facilitation of post-market monitoring | `data_catalog.json` aggregates per-resource activity counts. | None at the artefact level. **Gap**: no defined "post-market monitoring report" cadence — the bundle is on-demand only. |
| 12(2)(c) | Monitoring of operation of high-risk AI systems referred to in Art. 26(5) | `chain_anchor` + HMAC chain + DSSE envelope (Ed25519). | Cleared by the DSSE envelope. A third-party auditor with the public key can now verify integrity without bernstein's HMAC key. |
| 12(3) | Logs kept ≥6 months unless otherwise provided; 10y for high-risk under Art. 19(1) | `RetentionPin` in `manifest.json` enforces 10y / 183d at bundle-build time. | **Storage-enforceability gap**: pin is metadata only. Article 12(3) needs an immutable backend (S3 Object Lock, WORM Postgres, `chattr +i`) to make the retention enforceable. The operator can `rm` the bundle and the pin disappears with it. |
| 19(1) | Providers keep automatically generated logs for at least 6 months | Same as 12(3). | Same gap. Plus: there is no **bundle index** the operator can hand to an auditor showing "every bundle for this system over the last six months", which is what the auditor wants. |

**Verdict:** Bernstein satisfies 12(1), 12(2)(b), and 12(2)(c) cleanly.
12(2)(a), 12(3), and 19(1) have specific gaps an auditor will probe in the
first hour of a conformity assessment.

---

## 3. DORA Art. 8-15 ICT-risk evidence gap

Reference: Regulation (EU) 2022/2554, Articles 8-15 (ICT risk management
framework + Register of Information).

| Article | Requirement (paraphrased) | Bernstein artefact | Gap |
|---------|---------------------------|--------------------|-----|
| Art. 8 | ICT-asset inventory + classification | None | **Not produced.** Bernstein has agent registry + adapter list (44 adapters at v1.10.5), but no "inventory export" suitable for the DORA Register-of-Information cycle. |
| Art. 9(3) | Integrity of ICT systems / data, including tamper-evident records | HMAC chain + DSSE envelope. | Cleared by DSSE. |
| Art. 10 | Detection — anomalous activity | `core/security/security_correlation.py`, denial tracker. | Partial. No pre-built detection profile aligned to DORA's "anomalous-activity" language. |
| Art. 11 | Response and recovery | `core/security/security_incident_response.py`. | Module exists; no DORA-specific incident classification (major / significant / non-major). |
| Art. 12 | ICT business-continuity policies | Outside bernstein's scope (ops, not orchestrator). | Acknowledge in docs; do not over-claim. |
| Art. 13 | Learning and evolving (post-incident) | None as a reportable artefact. | Future work: a "post-incident report" template aligned to DORA. |
| Art. 14-15 | Communication strategy + crisis management | Outside scope. | Same. |
| Art. 28 | ICT third-party risk | None | **High-priority gap.** A bernstein customer is a DORA-regulated financial entity; bernstein itself sits in the third-party stack. The customer's CTPP compliance officer needs an Art. 28-shaped attestation pack from bernstein. Not produced today. |

**Verdict:** DORA Art. 8 (asset inventory) and Art. 28 (TPP attestation) are
the two gaps that disqualify bernstein from the EU bank vendor-DD packet
today. Art. 9(3) is cleared by the DSSE envelope. Art. 10/11/13 have
substrate but no DORA-specific output format.

---

## 4. FINOS AI Governance Framework controls map

Reference: github.com/finos/ai-governance-framework (16 controls × 14 risks,
Community Specification License).

The detailed map lives at [`finos-aigf-mapping.md`](finos-aigf-mapping.md).
Net of the cross-walk: **16 of 16 AIGF controls are covered** after the
Sigstore release attestation, DSSE envelope, and per-role adapter deny-list
landed.

The strongest single AIGF angle is `CTRL-PROMPT-INJECTION-DEFENCE` against
`AIR-OP-001` (tool-chain logic vulnerabilities) — bernstein's lethal-trifecta
matrix runs at the engine layer rather than the prompt layer, which is the
property an auditor wants to see for that risk row. Second-strongest is
`AIR-OP-003` (lack of explainability): bernstein's deterministic Python
orchestration is structurally exempt from the explainability gap that
LLM-coordinated frameworks have.

---

## 5. Role-based agent execution policies

The enterprise need: deny-list certain agents per role (e.g., a `security`
role must not be able to spawn cloud-LLM adapters).

What ships at v1.10.5:

| Surface | Implementation |
|---------|----------------|
| Tool allow/deny per role | `claude_permission_profiles.py` ships profiles for `backend`/`frontend`/`qa`/`security`/`docs`/`reviewer`/`devops`. Each profile carries `allowed_tools`, `disallowed_tools`, `deny_patterns`. |
| Adapter (agent) allow/deny per role | `role_adapter_policy.py`. Reads a per-role adapter deny-list from `bernstein.yaml` (e.g., `roles.security.deny_adapters: [claude_routine, devin_terminal, codex_cloudflare]`); hooks into the orchestrator's adapter-selection step and refuses to spawn a denied adapter for the task's effective role; emits a `role_adapter_denied` audit event to the HMAC chain. |
| API-route RBAC | `rbac.py` admin/operator/viewer. |
| Per-task policy override | `policy_templates.py` allows org admins to merge-overlay `bernstein.yaml` per task. |

Use `bernstein policy show --role <name>` to print the effective adapter
allowlist for a role.

This maps directly to AIGF `CTRL-SEGREGATION-OF-DUTIES` and SR 11-7 §V.4.

---

## 6. SR 11-7 + ISO 42001 cross-walk

| SR 11-7 section | Bernstein angle | Status |
|-----------------|-----------------|--------|
| §III Model risk taxonomy | `eu_ai_act.py:RiskLevel` + per-task assessment log. | Adjacent — task-level not model-level. |
| §IV Model documentation | `compliance/eu_ai_act.py:ComplianceEngine` Annex IV doc. | Strong. |
| §V Model implementation — segregation of duties | `rbac.py` + `claude_permission_profiles.py` + `role_adapter_policy.py`. | Strong. |
| §VI External resources / vendor management | None. | DORA Art. 28 overlap. |
| §VII Model monitoring | HMAC chain + Article 12 bundle + DSSE envelope. | Strong. |
| §VIII Model risk reporting | `compliance_report.py`. | Strong. |

| ISO 42001 clause | Bernstein angle | Status |
|------------------|-----------------|--------|
| 4 Context of the organisation | Out of scope. | OK |
| 5 Leadership / AI policy | Out of scope. | OK |
| 6 Planning — AI risk assessment | `eu_ai_act.py`. | Partial. |
| 7.5.3 Control of documented information | `audit.py` + `article12_bundle.py`. | Strong. |
| 8 Operation — AI lifecycle | Adapter pipeline + lineage v2. | Strong. |
| 9.1 Performance evaluation | `compliance_report.py`, `audit_pack.py`. | Strong. |
| 9.2 Internal audit | Self-generated SOC 2 pack template. | Partial. |
| 10 Improvement / nonconformities | `security_incident_response.py`. | Substrate, no ISO-shaped report. |

---

## 7. SOC 2 self-evidence template (user-side)

Bernstein-the-vendor cannot get its own SOC 2 in the current operator shape
(solo, IL-based). The asset is **bernstein helps the user generate evidence
for *their* SOC 2**.

The substrate already exists: `core/security/audit_pack.py:generate_audit_pack()`
+ `soc2-evidence-nightly.yml`. Future work: a user-facing template that
documents which trace fields populate which TSC control.

Sketch (skeleton — markdown, no code):

```text
# SOC 2 Type II — bernstein-generated evidence pack (user-side template)

## How to use this template
1. Run `bernstein audit pack --soc2 --output ./soc2-evidence/`.
2. The command emits `soc2-evidence/checklist.md` + per-control artefacts.
3. Map each row below to your auditor's request list.

## Trust Service Criteria mapping

| TSC control | Description | bernstein artefact (file) | bernstein source field |
| CC2.1       | Communicate internal-control responsibilities | `.sdd/audit/<date>.jsonl` | `event_type`, `actor`, `resource_*` |
| CC6.1       | Logical access controls | `.sdd/audit/*.jsonl` + credential scoping policy | `event_type=auth.*`, `outcome` |
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

The CLI surface that backs this template ships today.

---

## 8. Anti-overclaim notes

For interview / vendor-DD prep:

- **OK to claim now:** "bernstein ships an HMAC-chained, deterministic
  Article 12 evidence bundle wrapped in a DSSE / in-toto envelope; the
  bundle is verifiable by a stdlib-only standalone verifier; the substrate
  covers 16 of 16 AIGF controls; the security tree includes lethal-trifecta
  enforcement, lineage v2 with customer signatures, Sigstore per-task
  attestation, Sigstore release attestation, and a SOC 2 evidence pack run
  weekly in CI."
- **Do NOT claim yet:** "Article 12 retention is regulator-enforceable" (the
  pin is metadata, not an immutable backend), "DORA Art. 28 attestation
  pack" (does not exist), "SOC 2 Type II for bernstein-the-vendor"
  (operator constraint).
- **Caveats to state proactively:** the immutable-storage backend for the
  Article 12 bundle is not shipped (S3 Object Lock / WORM Postgres is
  future work), the DORA Art. 8 asset register is not produced, and the
  ISO-shaped post-incident report template does not exist.

---

## 9. References

- EU AI Act: Regulation (EU) 2024/1689, eur-lex.europa.eu/eli/reg/2024/1689.
- DORA: Regulation (EU) 2022/2554, eur-lex.europa.eu/eli/reg/2022/2554.
- FINOS AI Governance Framework: github.com/finos/ai-governance-framework.
- ISO/IEC 42001:2023, AI Management System.
- US Federal Reserve SR 11-7, "Guidance on Model Risk Management".
- in-toto / DSSE: github.com/in-toto/attestation,
  github.com/secure-systems-lab/dsse.
- Sigstore: sigstore.dev; github.com/sigstore/cosign.
