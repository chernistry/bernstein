# WORKFLOW: SOC 2 Compliance Reporting
**Version**: 1.0
**Date**: 2026-04-08
**Author**: Workflow Architect
**Status**: Approved
**Implements**: ENT-004

---

## Overview

Transforms the raw JSONL audit export into a structured SOC 2 compliance package. The package includes: SOC 2 Type II trust service criteria control mappings (CC6.1, CC6.2, CC6.3, CC7.1, CC7.2, CC8.1, CC9.1, CC9.2), evidence summaries per control, HMAC chain verification results, Merkle root attestation, and a JSON-serializable compliance report. Triggered via `bernstein audit export --period Q1-2026`.

---

## Actors
| Actor | Role in this workflow |
|---|---|
| Operator | Runs the CLI export command |
| audit_cmd.py (CLI) | Parses arguments, validates period, invokes export |
| compliance.py | Assembles raw evidence bundle (audit logs, config, WAL, SBOM, Merkle seals) |
| soc2_report.py | Generates structured compliance report with control mappings |
| audit.py | Provides HMAC chain verification |
| merkle.py | Provides Merkle seal loading and verification |
| .sdd/ | Source of all evidence artifacts |

---

## Prerequisites
- `.sdd/` directory exists with audit data from prior runs
- Audit logging was enabled (compliance preset STANDARD or REGULATED, or explicit `audit_logging: true`)
- Period argument is a valid format (Q1-2026, 2026-03, or 2026)

---

## Trigger
CLI command: `bernstein audit export --period <PERIOD> [--format zip|dir] [--output DIR]`

---

## Workflow Tree

### STEP 1: Parse and validate period
**Actor**: audit_cmd.py export_cmd -> compliance.parse_period
**Action**: Parse period string into ISO 8601 start/end dates
**Timeout**: Instant
**Input**: `{ period: str }` (e.g. "Q1-2026")
**Output on SUCCESS**: `(start_date, end_date)` -> GO TO STEP 2
**Output on FAILURE**:
  - `INVALID_PERIOD`: Cannot parse -> CLI prints red error, exit code 1

**Observable states**:
  - Operator sees: Error message if invalid period

---

### STEP 2: Validate .sdd directory
**Actor**: audit_cmd.py export_cmd
**Action**: Check .sdd/ exists
**Timeout**: Instant
**Output on SUCCESS**: sdd_dir resolved -> GO TO STEP 3
**Output on FAILURE**:
  - `MISSING(sdd_dir)`: -> CLI prints "State directory not found", exit code 1

---

### STEP 3: Assemble raw evidence bundle
**Actor**: compliance.export_soc2_package
**Action**: Collect artifacts from .sdd/ into a bundle directory, filtered by period
**Timeout**: <5s (filesystem copies)
**Sub-steps**:
  1. **Audit logs** — Copy .jsonl files where filename date falls within period range
  2. **HMAC chain verification** — Instantiate AuditLog, run verify(), store result
  3. **Merkle seals** — Copy all seal JSON files
  4. **Compliance config** — Copy config files (excluding audit-key for security)
  5. **WAL** — Copy write-ahead log entries
  6. **SBOM** — Copy CycloneDX JSON files
  7. **Write verification.json** — HMAC chain and Merkle verification results
  8. **Compute file checksums** — SHA-256 of every file in the bundle
  9. **Generate SOC 2 compliance report** — Call `generate_soc2_report()` from soc2_report.py
  10. **Write manifest.json** — Package metadata with artifact list, checksums, and report reference

**Output on SUCCESS**: bundle_dir populated -> GO TO STEP 4
**Output on FAILURE**:
  - `EXCEPTION`: -> CLI prints error, exit code 1

**Observable states**:
  - Logs: `SOC 2 evidence package exported: <path>`

---

### STEP 4: Package output
**Actor**: compliance.export_soc2_package
**Action**: If format=zip, create zip archive and remove temp dir. If format=dir, return as-is.
**Timeout**: <5s
**Output on SUCCESS**: Path to zip or directory -> GO TO STEP 5
**Output on FAILURE**:
  - `ZIP_ERROR`: Compression fails -> exception propagated to CLI

---

### STEP 5: Display summary
**Actor**: audit_cmd.py export_cmd
**Action**: Print Rich table with period, format, output path
**Observable states**:
  - Operator sees: Green panel "SOC 2 Evidence Package" with period, format, output path

---

## State Transitions
```
[cli invoked] -> (invalid period) -> [error, exit 1]
[cli invoked] -> (missing .sdd) -> [error, exit 1]
[cli invoked] -> (valid inputs) -> [assemble bundle] -> [package] -> [display summary, exit 0]
```

---

## Handoff Contracts

### CLI -> export_soc2_package()
**Function call**: `export_soc2_package(sdd_dir, period, output_path, fmt)`
**Input**:
```python
{
    "sdd_dir": "Path — .sdd directory root",
    "period": "str — e.g. 'Q1-2026'",
    "output_path": "Path | None — defaults to sdd_dir/evidence/",
    "fmt": "str — 'zip' or 'dir'"
}
```
**Return**: `Path` — path to the exported zip or directory
**On ValueError**: Raised for invalid period (caught by CLI)

### export_soc2_package -> generate_soc2_report()
**Function call**: `generate_soc2_report(sdd_dir, period, start_date, end_date)`
**Input**:
```python
{
    "sdd_dir": "Path",
    "period": "str",
    "period_start": "str — ISO date",
    "period_end": "str — ISO date"
}
```
**Return**: `SOC2ComplianceReport` — serializable via `.to_dict()`

### generate_soc2_report -> AuditLog.verify()
**Function call**: `audit_log.verify()`
**Return**: `(valid: bool, errors: list[str])`

### generate_soc2_report -> load_latest_seal()
**Function call**: `load_latest_seal(merkle_dir)`
**Return**: `(seal_dict, seal_path) | None`

---

## Cleanup Inventory
| Resource | Created at step | Destroyed by | Destroy method |
|---|---|---|---|
| Bundle temp directory | Step 3 | Step 4 (zip mode only) | shutil.rmtree after zip creation |
| Zip file | Step 4 | Operator responsibility | Manual deletion |

---

## SOC 2 Control Mappings

| Control ID | Category | Title | Evidence Types |
|---|---|---|---|
| CC6.1 | Security | Logical Access Controls | audit_log, auth_config |
| CC6.2 | Security | Authentication Mechanisms | auth_config, cluster_auth |
| CC6.3 | Security | Authorization Controls | audit_log, permission_config |
| CC7.1 | Security | Change Management | wal, audit_log |
| CC7.2 | Security | System Monitoring | metrics, sla_monitoring |
| CC8.1 | Availability | Capacity Management | metrics, sla_monitoring |
| CC9.1 | Processing Integrity | Processing Accuracy | merkle_seal, hmac_verification |
| CC9.2 | Processing Integrity | Data Integrity | merkle_seal, wal, hmac_verification |

---

## Evidence Package Structure
```
soc2-Q1-2026/
  manifest.json           # Package metadata, artifact list, checksums
  verification.json       # HMAC chain + Merkle verification results
  soc2-report.json        # Structured compliance report with control mappings
  audit_logs/             # Period-filtered HMAC-chained JSONL files
  merkle_seals/           # Merkle tree seal JSON files
  compliance_config/      # Policy and config files (excludes audit-key)
  wal/                    # Write-ahead log entries
  sbom/                   # CycloneDX SBOM JSON files
```

---

## Test Cases

| Test | Trigger | Expected behavior |
|---|---|---|
| TC-01: Empty .sdd | No audit data | Minimal package, status=non_compliant |
| TC-02: With audit logs | Audit logs in period | Logs copied, evidence mapped to CC6.1/CC6.3/CC7.1 |
| TC-03: Period filtering | Logs in and out of range | Only in-range files included |
| TC-04: HMAC verification | Valid HMAC chain | hmac_chain_valid=true in report |
| TC-05: Merkle attestation | Merkle seal exists | Attestation included in report |
| TC-06: WAL evidence | WAL files exist | Mapped to CC7.1/CC9.2 |
| TC-07: Metrics evidence | Metrics files exist | Mapped to CC7.2/CC8.1 |
| TC-08: Compliant status | All evidence types present + valid HMAC | overall_status=compliant |
| TC-09: Zip format | --format zip | Zip created, temp dir removed |
| TC-10: Dir format | --format dir | Directory preserved |
| TC-11: Package hash | Any export | SHA-256 hash computed over report content |
| TC-12: Audit key excluded | Config dir has audit-key | audit-key not in compliance_config/ |
| TC-13: SOC 2 report included | Any export | soc2-report.json present in bundle |

---

## Assumptions
| # | Assumption | Where verified | Risk if wrong |
|---|---|---|---|
| A1 | Audit log filenames follow YYYY-MM-DD.jsonl convention | Verified: audit.py writes with this pattern | Period filtering fails |
| A2 | audit-key must never be included in evidence packages | Verified: export_soc2_package skips audit-key | Key leaked to auditors |
| A3 | All SOC 2 controls are relevant to Bernstein | Design decision | Over-reporting |
| A4 | Merkle seals are stored in audit/merkle/ | Verified: MERKLE_DIR = AUDIT_DIR / "merkle" | Seals not found |

## Open Questions
- Should the SOC 2 report include a human-readable text/PDF summary in addition to JSON?
- Should access.jsonl (API access logs) be included as CC6.2 evidence?

## Spec vs Reality Audit Log
| Date | Finding | Action taken |
|---|---|---|
| 2026-04-08 | Initial spec created. soc2_report.py exists but was not integrated into export_soc2_package | Integration added in this session |
| 2026-04-08 | export_soc2_package produced raw artifacts only, no structured compliance report | Added generate_soc2_report call and soc2-report.json to bundle |
