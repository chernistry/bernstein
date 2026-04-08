# WORKFLOW: Audit Log Integrity Verification on Startup
**Version**: 1.0
**Date**: 2026-04-08
**Author**: Workflow Architect
**Status**: Approved
**Implements**: ENT-003

---

## Overview

On every orchestrator startup, the system verifies the HMAC chain of the last N audit log entries (configurable, default 100) to detect tampering or corruption. If integrity is compromised, the orchestrator logs a warning and continues (non-blocking). This provides continuous tamper-evidence without requiring manual `bernstein audit verify` invocations.

---

## Actors
| Actor | Role in this workflow |
|---|---|
| Orchestrator | Triggers verification during `run()` startup sequence |
| audit_integrity module | Performs HMAC chain verification |
| .sdd/audit/ | Source of JSONL audit log files |
| .sdd/config/audit-key | HMAC key for chain verification |
| Logger | Emits warnings on integrity failure |

---

## Prerequisites
- `.sdd/` directory exists (orchestrator creates it if absent)
- Audit log JSONL files exist in `.sdd/audit/` (if audit logging was enabled on prior runs)
- HMAC key exists at `.sdd/config/audit-key` (auto-generated on first audit write)

---

## Trigger
Orchestrator `run()` method is called. Verification executes after WAL recovery and before the first tick loop iteration.

---

## Workflow Tree

### STEP 1: Locate audit directory
**Actor**: verify_on_startup (audit_integrity.py)
**Action**: Resolve `.sdd/audit/` from the provided sdd_dir
**Timeout**: Instant (filesystem check)
**Input**: `{ sdd_dir: Path }`
**Output on SUCCESS**: `audit_dir` exists -> GO TO STEP 2
**Output on FAILURE**:
  - `MISSING(audit_dir)`: Directory does not exist -> RETURN valid=True, entries_checked=0, warning="Audit directory does not exist; skipping integrity check". No cleanup needed.

**Observable states during this step**:
  - Operator sees: Log message at DEBUG level
  - Database: No state change
  - Logs: `[audit_integrity] checking .sdd/audit/`

---

### STEP 2: Load HMAC key
**Actor**: _load_audit_key (audit_integrity.py)
**Action**: Read `.sdd/config/audit-key`
**Timeout**: Instant (filesystem read)
**Input**: `{ audit_dir: Path }`
**Output on SUCCESS**: key bytes loaded -> GO TO STEP 3
**Output on FAILURE**:
  - `MISSING(key_file)`: Key file does not exist -> RETURN valid=True, entries_checked=0, warning="HMAC key not found; cannot verify audit integrity". No cleanup needed.

**Observable states during this step**:
  - Logs: Warning if key missing

---

### STEP 3: Load tail entries
**Actor**: _load_tail_entries (audit_integrity.py)
**Action**: Read the last N entries from newest JSONL files (reverse chronological)
**Timeout**: <1s for 100 entries (typical)
**Input**: `{ audit_dir: Path, count: int }`
**Output on SUCCESS**: List of (filename, line_no, parsed_entry) -> GO TO STEP 4
**Output on FAILURE**:
  - `EMPTY(entries)`: No entries found -> RETURN valid=True, entries_checked=0, warning="No audit entries found to verify"
  - `PARSE_ERROR(line)`: Individual JSON parse failures are recorded but do not abort — they become errors in the result

**Observable states during this step**:
  - Logs: Count of entries loaded

---

### STEP 4: Verify HMAC chain
**Actor**: verify_audit_integrity (audit_integrity.py)
**Action**: For each entry: (1) check prev_hmac links to previous entry's hmac, (2) recompute HMAC from payload and compare to stored hmac
**Timeout**: <100ms for 100 entries
**Input**: `{ entries: list, key: bytes }`
**Output on SUCCESS**: valid=True, errors=[] -> GO TO STEP 5a
**Output on FAILURE**:
  - `HMAC_MISMATCH`: Stored HMAC differs from recomputed -> error recorded, continue checking remaining entries
  - `CHAIN_BROKEN`: prev_hmac does not match previous entry's hmac -> error recorded, continue checking
  - `PARSE_ERROR`: Unparseable JSON line -> error recorded, continue checking

**Observable states during this step**:
  - Logs: Per-error warning lines

---

### STEP 5a: Report success (happy path)
**Actor**: Orchestrator run() method
**Action**: Log info-level message, continue startup
**Observable states**:
  - Logs: `[audit_integrity] Audit integrity check passed: N entries verified in Xms`

### STEP 5b: Report failure (integrity compromised)
**Actor**: Orchestrator run() method
**Action**: Log WARNING-level messages for each error. Startup continues (non-blocking).
**Observable states**:
  - Logs: `[audit_integrity] AUDIT INTEGRITY WARNING: N error(s) detected in the audit log. The HMAC chain may have been tampered with.`
  - Each individual error is logged at WARNING level

---

## State Transitions
```
[startup] -> (audit dir missing) -> [skip, continue startup]
[startup] -> (key missing) -> [skip, continue startup]
[startup] -> (no entries) -> [skip, continue startup]
[startup] -> (all entries valid) -> [log success, continue startup]
[startup] -> (integrity errors found) -> [log warnings, continue startup]
```

---

## Handoff Contracts

### Orchestrator.run() -> verify_on_startup()
**Function call**: `verify_on_startup(sdd_dir, count=N)`
**Input**: `{ sdd_dir: Path, count: int (default 100) }`
**Return**: `IntegrityCheckResult { valid: bool, entries_checked: int, entries_total: int, errors: list[str], warnings: list[str], checked_at: str, duration_ms: float }`
**On exception**: Caught by try/except in run(), logged as non-fatal, startup continues.

---

## Cleanup Inventory
No resources are created by this workflow. It is read-only.

---

## Test Cases

| Test | Trigger | Expected behavior |
|---|---|---|
| TC-01: No audit directory | sdd_dir with no audit/ | valid=True, entries_checked=0, warning about missing dir |
| TC-02: No HMAC key | audit/ exists, no config/audit-key | valid=True, entries_checked=0, warning about missing key |
| TC-03: Empty audit dir | audit/ exists, no .jsonl files | valid=True, entries_checked=0, warning about no entries |
| TC-04: Valid chain (full) | 10 entries, count=10 | valid=True, entries_checked=10, no errors |
| TC-05: Valid chain (partial) | 20 entries, count=5 | valid=True, entries_checked=5 (tail only) |
| TC-06: Tampered HMAC | Entry with modified hmac field | valid=False, error containing "HMAC mismatch" |
| TC-07: Broken chain | Entry with wrong prev_hmac | valid=False, error containing "chain broken" |
| TC-08: Multiple files | Entries across two date files | Correct tail entries loaded across files |
| TC-09: Orchestrator integration | run() with tampered audit | Warning logged, startup continues |
| TC-10: Orchestrator integration (crash) | verify_on_startup throws | Exception caught, logged, startup continues |

---

## Assumptions
| # | Assumption | Where verified | Risk if wrong |
|---|---|---|---|
| A1 | HMAC computation in audit_integrity.py matches audit.py | Verified: both use same _compute_hmac pattern | Chain always fails verification |
| A2 | Genesis HMAC sentinel "0"*64 matches audit.py | Verified: _GENESIS_HMAC = "0" * 64 in both | First entry always fails |
| A3 | Verification is fast enough to not delay startup | Verified: 100 entries < 100ms | Startup delay if log is huge |
| A4 | Verification failure is non-blocking | Verified: orchestrator catches exceptions | Startup blocked on corrupt audit |

## Spec vs Reality Audit Log
| Date | Finding | Action taken |
|---|---|---|
| 2026-04-08 | Initial spec created. Module exists, tests pass, but not wired into orchestrator.run() | Wiring integration in this session |
| 2026-04-08 | verify_on_startup was never called from orchestrator — gap closed | Added call in run() after WAL recovery |
