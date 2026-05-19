# Per-ticket transcript bundle

For every tracker ticket Bernstein touches, the orchestrator can emit a
single signed archive that ties together every agent transcript, every
trace, every lineage entry, the resulting commits, the resulting PR, and
the failure-taxonomy comments. Auditors unwrap the archive once and see
the full activity for that ticket.

The bundle is a packaging primitive layered over data that already
exists under `.sdd/`. It does not own its inputs; it indexes them and
binds them with a versioned manifest plus a detached Ed25519 signature
that reuses the lineage signer.

## When to use it

| Trigger                                               | Action                                                         |
|-------------------------------------------------------|----------------------------------------------------------------|
| SOX-2026 or SOC 2 Type II ticket-level audit request  | Generate the bundle and hand it to the auditor.                |
| Incident post-mortem ("what did the agents see?")     | Generate the bundle and attach to the incident record.         |
| Vendor evidence request                               | Generate, sign, and hand off the JWS + archive.                |
| Local debugging of one ticket                         | Generate without signing -- the manifest stays usable.         |

## CLI

```
bernstein bundle ticket <tracker> <ticket_id> --out <path>
bernstein bundle ticket <tracker> <ticket_id> --out <path> \
  --sign-key <pem> --sign-kid <kid>
bernstein bundle verify <archive> <signature> --card <agent_card.json>
```

Examples:

```
# Assemble (no signing -- local dev)
bernstein bundle ticket github ENG-42 --out ENG-42.tar.gz

# Assemble + sign with the lineage steward key
bernstein bundle ticket github ENG-42 --out ENG-42.tar.gz \
  --sign-key keys/lineage.pem --sign-kid lineage-2026

# Auditor host: verify before extraction
bernstein bundle verify ENG-42.tar.gz ENG-42.tar.gz.jws \
  --card auditor/agent_card.json
```

`verify` exits 0 on success and non-zero on any verification failure
(tampered file, tampered manifest, wrong key, missing signature). It
never raises -- a tampered archive simply prints an error message.

## Archive layout

```
manifest.json                       -- versioned manifest (schema_version=1)
transcripts/<agent>-<session>.jsonl -- per-agent per-turn transcripts
traces/<session>-trace.jsonl        -- per-agent trace JSONL
lineage/<file>.jsonl                -- ticket-filtered lineage records
audit/<file>.jsonl                  -- tracker-audit JSONL slice
git/commits.json                    -- resolved commit SHAs for the ticket
git/diff.patch                      -- unified patch over those commits
pr/pr_<number>.json                 -- PR payload, when known
```

Every file listed in `manifest.files[*].arcname` is recorded with its
`size_bytes` and hex `sha256`. The detached JWS in
`<archive>.jws` covers the JCS-canonical encoding of `manifest.json`.
Because every bundled file's sha256 is in the manifest, the signature
transitively covers the contents of every bundled file.

## Manifest schema (`schema_version = 1`)

| Field               | Type                | Notes                                       |
|---------------------|---------------------|---------------------------------------------|
| `schema_version`    | int                 | Bump on any backwards-incompatible change.  |
| `created_at`        | ISO-8601 UTC string | Producer wall-clock at assembly time.       |
| `bernstein_version` | str                 | Producer's `bernstein.__version__`.         |
| `tracker`           | str                 | e.g. `github`, `jira`, `linear`.            |
| `ticket_id`         | str                 | Tracker-scoped ticket id, e.g. `ENG-42`.    |
| `files`             | list                | One `ManifestEntry` per bundled file.       |
| `pr_number`         | int or null         | Resulting PR number, when known.            |
| `commits`           | list of str         | Resolved commit SHAs for this ticket.       |

`ManifestEntry`:

| Field        | Type | Notes                                                 |
|--------------|------|-------------------------------------------------------|
| `arcname`    | str  | POSIX path inside the archive.                        |
| `size_bytes` | int  | Uncompressed file size.                               |
| `sha256`     | str  | Hex sha-256 over the bundled file's bytes.            |
| `section`    | str  | Logical section (`transcripts`, `traces`, `git` ...). |

## Selection strategy

A file is included when **either** of the following holds:

1. Its filename contains the ticket id verbatim.
2. It is a `.jsonl` or `.json` file and at least one record carries both
   the tracker string and the ticket id as values of any field.

Files larger than 2 MiB are skipped by the content probe so the bundle
build does not slurp pathological logs into memory. The filename match
still applies regardless of size.

Callers that want a different strategy can pass an explicit
`BundleSelector` to `TicketBundle`. The Python surface
(`bernstein.core.observability.ticket_bundle`) exposes the dataclasses
for tests and for downstream code that wants to pre-filter.

## Auditor question map

| Auditor question                                             | File in the bundle                                |
|--------------------------------------------------------------|---------------------------------------------------|
| Which agent saw what tool call?                              | `transcripts/<agent>-<session>.jsonl`             |
| What spans did the agent produce?                            | `traces/<session>-trace.jsonl`                    |
| Was a write blocked by the lineage gate?                     | `lineage/*.jsonl`                                 |
| When did the tracker open/close this ticket?                 | `audit/*.jsonl`                                   |
| What code changed because of this ticket?                    | `git/diff.patch`, `git/commits.json`              |
| Which PR landed this work?                                   | `pr/pr_<number>.json`                             |
| Has anyone tampered with the artefact set?                   | `manifest.json` + `<archive>.jws`                 |

## Out of scope

- Cross-ticket bundles (a separate ticket; the schema would shift to a
  list of ticket ids).
- Auto-upload to an external evidence store -- callers pipe the archive
  themselves.
- HTML rendered viewer -- the existing trace viewer can be extended in a
  follow-up ticket.

## Related modules

- `src/bernstein/core/observability/ticket_bundle.py` -- assembler.
- `src/bernstein/cli/commands/bundle_cmd.py` -- CLI wrapper.
- `src/bernstein/core/lineage/identity.py` -- shared Ed25519 signer.
- `src/bernstein/cli/run_archive.py` -- existing run-archive helper that
  inspired the manifest pattern.
