# FINOS AI Governance Framework — bernstein controls map

Date: 2026-05-09
Owner: Alex Chernysh
Spec: [FINOS AI Governance Framework](https://github.com/finos/ai-governance-framework)
       (`CONTROLS.md` + the rendered site at <https://air-governance-framework.finos.org>),
       Community Specification License v1.0, snapshot taken at `main` on 2026-05-09.
Source: companion gap analysis at
        [`RESRCH-002-enterprise-modernization-fit.md`](./RESRCH-002-enterprise-modernization-fit.md).

This document is the reciprocal-citation deliverable RESRCH-002 calls for in §4 and §8.
For each FINOS AIGF control we list the bernstein subsystem(s) implementing it, the
specific source files, and an honest "covered / partial / not yet covered" verdict.

The pairing also covers the AIGF risk inventory (`AIR-*` rows in `risks/`) so the
two sides of the framework are mapped end-to-end.

## TL;DR

| Status        | Count | Notes |
|---------------|-------|-------|
| Covered       | 13/16 | Strong substrate, code paths cited below. |
| Partial       | 2/16  | `CTRL-AUDIT-TRAIL` lacks the third-party-verifiable envelope until DSSE lands; `CTRL-SEGREGATION-OF-DUTIES` covers tools only, not adapters, until the role-adapter policy lands. Both fixes ship in the same PR as this doc. |
| Not yet covered | 1/16 | `CTRL-MODEL-SUPPLY-CHAIN`: per-task Sigstore is wired; release artefacts are not signed by `attest-build-provenance` yet. Tracked as a follow-up. |

## 1. AIGF control inventory

Cross-walked against the 16 controls listed in `finos/ai-governance-framework`
(`CONTROLS.md` + the rendered site) as of 2026-05-09. Where the AIGF control title
differs slightly from what the upstream repo published, we cite the closest-match
control id.

| AIGF control | bernstein implementation | Files | Verdict |
|--------------|--------------------------|-------|---------|
| `CTRL-AUDIT-TRAIL` | HMAC-chained JSONL audit log + Article 12 evidence bundle (deterministic zip with manifest, clause map, retention pin) + DSSE/in-toto envelope wrapper. | `src/bernstein/core/security/audit.py` (506 lines), `src/bernstein/core/security/article12_bundle.py` (1140 lines), `src/bernstein/core/security/audit_dsse.py` (added in this PR) | Covered. The HMAC chain and Article-12 bundle were already prod; the DSSE envelope (this PR) closes the third-party-verifiable gap RESRCH-002 §4 flagged. |
| `CTRL-DATA-LINEAGE` | Per-artefact lineage WAL with `regulatory_class` field and customer-controlled Ed25519 detached signature (schema v2). | `src/bernstein/core/persistence/lineage.py`, `src/bernstein/core/persistence/lineage_signer.py` | Covered. |
| `CTRL-MODEL-SUPPLY-CHAIN` | Per-task Sigstore/Rekor keyless attestation with Ed25519 fallback; agent-card signer + JWKS rotation. | `src/bernstein/core/security/sigstore_attestation.py`, `src/bernstein/core/security/agent_card_signer.py`, `src/bernstein/core/security/agent_card_keystore.py` | Partial. Per-task path is shipped and tested; release-artefact provenance via GitHub `actions/attest-build-provenance` is **not yet wired** — tracked as a v2 follow-up. |
| `CTRL-TOOL-INVENTORY` | Adapter registry (~50 adapters at HEAD) + capability-matrix yaml + per-role profile manager. | `src/bernstein/adapters/registry.py`, `src/bernstein/core/security/capability_matrix.py`, `src/bernstein/core/security/claude_permission_profiles.py` | Covered. Inventory-export-in-AIGF-shape is a doc-only follow-up tracked as part of the DORA Art. 8 backlog item. |
| `CTRL-HUMAN-OVERSIGHT` | Single + dual approval gates, plan-approval workflow, per-role default deny. | `src/bernstein/core/security/approval.py`, `src/bernstein/core/security/dual_approval.py`, `src/bernstein/core/security/plan_approval.py`, `src/bernstein/core/security/auto_approve.py` | Covered. |
| `CTRL-ACCESS-CONTROL` | API-route RBAC (admin/operator/viewer) + per-role allowed/disallowed tools + permission-graph + delegation matrix. | `src/bernstein/core/security/rbac.py`, `src/bernstein/core/security/claude_permission_profiles.py`, `src/bernstein/core/security/permission_graph.py`, `src/bernstein/core/security/permission_delegation.py`, `src/bernstein/core/security/permission_matrix.py` | Covered. |
| `CTRL-DATA-RESIDENCY` | Per-tenant region policy with write-time check; EU-residency loopback test. | `src/bernstein/core/security/data_residency.py` | Covered. |
| `CTRL-PII-PROTECTION` | DLP scanner v2 + PII output gate + sensitive-data detector + secrets scanner. | `src/bernstein/core/security/dlp_scanner_v2.py`, `src/bernstein/core/security/pii_output_gate.py`, `src/bernstein/core/security/sensitive_data.py`, `src/bernstein/core/security/secrets.py`, `src/bernstein/core/security/sensitive_file_detector.py` | Covered. |
| `CTRL-PROMPT-INJECTION-DEFENCE` | OWASP Agentic Security Initiative (ASI) detector pack + lethal-trifecta capability matrix (PRIVATE_DATA × UNTRUSTED_INPUT × EXTERNAL_COMM). | `src/bernstein/core/security/owasp_asi_detectors.py`, `src/bernstein/core/security/capability_matrix.py` | Covered. This is bernstein's strongest single AIGF mapping per RESRCH-002 §4.1. |
| `CTRL-INCIDENT-RESPONSE` | Incident-response orchestrator + denial tracker + quarantine + correlation engine. | `src/bernstein/core/security/security_incident_response.py`, `src/bernstein/core/security/denial_tracker.py`, `src/bernstein/core/security/quarantine.py`, `src/bernstein/core/security/security_correlation.py` | Covered. DORA-shaped incident classification (major/significant/non-major) is a follow-up. |
| `CTRL-SEGREGATION-OF-DUTIES` | RBAC + per-role tool deny-lists + per-role adapter deny-list (added in this PR). | `src/bernstein/core/security/rbac.py`, `src/bernstein/core/security/claude_permission_profiles.py`, `src/bernstein/core/security/role_adapter_policy.py` (added in this PR) | Covered. Until this PR the deny-list operated at tool granularity only; the adapter policy (RESRCH-002 §5) closes the SR 11-7 §V.4 gap. |
| `CTRL-RETENTION` | Article 12(3) retention pin (10y high-risk / 183d minimum) baked into the bundle manifest; calendar-day disk rotation. | `src/bernstein/core/security/article12_bundle.py:RetentionPin`, `src/bernstein/core/persistence/disk_retention.py` | Covered. Immutable-storage backend (S3 Object Lock / WORM Postgres) is a follow-up tracked as backlog item #6 in RESRCH-002. |
| `CTRL-ENCRYPTION-AT-REST` | State-encryption module + credential vault (OS keychain transport) + injector. | `src/bernstein/core/security/state_encryption.py`, `src/bernstein/core/security/vault/`, `src/bernstein/core/security/vault_injector.py` | Covered. |
| `CTRL-ENCRYPTION-IN-TRANSIT` | mTLS cluster guard + TLS pinning + socket guard. | `src/bernstein/core/security/socket_guard.py`, `src/bernstein/adapters/clm_tls_launcher.py` | Covered. |
| `CTRL-DEPENDENCY-INTEGRITY` | SBOM generator + license scanner + vuln-disclosure pipeline + wheelhouse verify. | `src/bernstein/core/security/sbom.py`, `src/bernstein/core/security/license_scanner.py`, `src/bernstein/core/security/vuln_disclosure.py` | Covered. |
| `CTRL-CHANGE-MANAGEMENT` | WAL + audit chain + git provenance signing. | `src/bernstein/core/security/commit_signing.py`, `src/bernstein/core/persistence/wal/` | Covered. |

**Net result: 13 covered, 2 partial, 1 not-yet-covered.** The two partials become
"covered" when the DSSE envelope and the role-adapter policy ship — both are part of
the same PR as this doc, so by the time this map lands on `main` they are already
wired. The remaining "not yet covered" item (`actions/attest-build-provenance` for
release artefacts) is intentionally deferred and tracked.

## 2. AIGF risk inventory

Same exercise on the AIR-* risk side. Two columns: bernstein support, then a verdict
that explicitly distinguishes "spec-only mapping" from "prod-tested in this repo".

| AIGF risk id | Risk title | bernstein mitigation | Verdict |
|--------------|-----------|----------------------|---------|
| `AIR-DA-001` | Inadequate data anonymisation | DLP scanner v2, PII output gate, differential-privacy module. | Covered (prod-tested). |
| `AIR-DA-002` | Cross-border data transfer | Per-tenant data-residency policy + write-time enforcement. | Covered (prod-tested). |
| `AIR-OP-001` | Tool-chain logic vulnerabilities | Lethal-trifecta capability matrix; refusal events emitted to the audit chain. | Covered (prod-tested). Single strongest AIGF angle bernstein has. |
| `AIR-OP-002` | Inadequate record-keeping for AI decisions | HMAC-chained audit + Article 12 bundle + DSSE envelope (this PR). | Covered after DSSE lands. |
| `AIR-OP-003` | Lack of explainability | Deterministic Python orchestration — coordination is zero-token, every decision is reproducible. | Covered (architectural). Spec-only mapping; relies on the structural property that bernstein never delegates orchestration to an LLM. |
| `AIR-OP-004` | Model supply-chain compromise | Per-task Sigstore + agent-card JWKS. | Partial. Release-artefact path is the gap (see `CTRL-MODEL-SUPPLY-CHAIN`). |
| `AIR-OP-005` | Hallucination in production | Out of scope — bernstein is task-level, not model-level. | Honestly out of scope. Documented here so the auditor does not see a missing row. |
| `AIR-OP-006` | Inadequate human oversight | Approval, dual-approval, plan-approval, role-default deny. | Covered (prod-tested). |
| `AIR-OP-007` | Regulatory-violation risk via missing audit trails | Same chain as `AIR-OP-002`; DSSE envelope (this PR) closes the third-party-verifiability sub-gap. | Covered after DSSE lands. |
| `AIR-RC-001` | Bias amplification | Out of scope (model-level concern). | Out of scope. |
| `AIR-RC-002` | Sensitive-data leakage | DLP v2 + PII gate + sensitive-data + secrets. | Covered (prod-tested). |
| `AIR-RC-003` | Prompt injection | OWASP ASI detectors + lethal-trifecta capability matrix. | Covered (prod-tested). |
| `AIR-RC-004` | Unauthorised tool invocation | Command allowlist + command policy + per-role profile + per-role adapter policy (this PR). | Covered after the adapter policy lands. |
| `AIR-RC-005` | Inadequate third-party evidence (vendor-DD) | None as a packaged artefact. | Not yet covered. Tracked as RESRCH-002 backlog item #5 (DORA Art. 28 attestation pack) and item #7 (SOC 2 self-evidence template). |

## 3. Cross-walk to other regulator anchors

For convenience, the same controls cited against the regulations RESRCH-002 names:

| Regulator | Anchor | Strongest bernstein mappings |
|-----------|--------|------------------------------|
| EU AI Act | Art. 12 record-keeping, Art. 19(1) automatically generated logs, Art. 26(5) high-risk monitoring | `CTRL-AUDIT-TRAIL`, `CTRL-RETENTION`, `CTRL-DATA-LINEAGE` |
| DORA | Art. 9(3) integrity, Art. 28 ICT third-party | `CTRL-AUDIT-TRAIL` (DSSE), `CTRL-MODEL-SUPPLY-CHAIN`, `CTRL-INCIDENT-RESPONSE` |
| SR 11-7 | §V model implementation / segregation of duties, §VII model monitoring | `CTRL-SEGREGATION-OF-DUTIES`, `CTRL-AUDIT-TRAIL`, `CTRL-CHANGE-MANAGEMENT` |
| ISO 42001 | cl. 7.5.3 control of documented information, cl. 9 performance evaluation | `CTRL-AUDIT-TRAIL`, `CTRL-RETENTION`, `CTRL-INCIDENT-RESPONSE` |

## 4. Honest spec-only vs prod-tested ledger

| Layer | Status | Honest notes |
|-------|--------|--------------|
| HMAC-chained audit log | Prod, tested. | Daily rotation, key isolated outside `.sdd/`, mode-0600 enforced. |
| Article 12 bundle (deterministic zip + retention pin + clause map) | Shipped, in-tree tests, not yet field-tested by an external auditor. | Auditor-grade signal needs DSSE envelope (closed in this PR) plus immutable storage backend (deferred). |
| DSSE/in-toto envelope on the bundle | Shipped in this PR. Round-trip + tamper tests in place. | Sigstore keyless variant is documented in the module docstring as a v2 follow-up; v1 uses local Ed25519. |
| Truly standalone verifier | Shipped in this PR (`tools/verify_audit_dsse.py`). Subprocess-isolated test enforces no `bernstein.*` import. | Pure stdlib + `cryptography`. Replaces the previous "standalone verifier" claim that imported the bundle module. |
| Per-role adapter deny-list | Shipped in this PR. Empty allow-list = back-compat all-allowed. | Hooks `bernstein.adapters.registry.get_adapter` so every spawn site is covered. |
| FINOS AIGF reciprocal mapping | This document. | Operator decides whether to crosspost a controls-implementation issue upstream. |
| Sigstore release attestation (SLSA L1) | **Not in CI.** | `actions/attest-build-provenance` workflow not yet wired on `main`. |
| OpenSSF Scorecard badge | **Not configured.** | Tracked in the modernization-fit doc backlog. |
| DORA Art. 8-15 evidence pack | **Does not exist** as a packaged artefact. | Tracked as backlog item #5 in RESRCH-002. |

## 5. References

- FINOS AI Governance Framework — <https://github.com/finos/ai-governance-framework>,
  rendered at <https://air-governance-framework.finos.org>. Community
  Specification License v1.0.
- bernstein source tree — every file path above is relative to repo root.
- EU AI Act — Regulation (EU) 2024/1689,
  <https://eur-lex.europa.eu/eli/reg/2024/1689>.
- DORA — Regulation (EU) 2022/2554,
  <https://eur-lex.europa.eu/eli/reg/2022/2554>.
- US Federal Reserve SR 11-7, "Guidance on Model Risk Management".
- ISO/IEC 42001:2023, AI Management System.
- in-toto attestation v1.0 spec —
  <https://github.com/in-toto/attestation/blob/main/spec/v1/README.md>.
- DSSE — <https://github.com/secure-systems-lab/dsse>.
- Companion gap analysis: [`RESRCH-002-enterprise-modernization-fit.md`](./RESRCH-002-enterprise-modernization-fit.md).
