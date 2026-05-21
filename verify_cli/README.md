# bernstein-verify

Standalone auditor CLI for Bernstein lineage v1 compliance packs.

## What it does

Verifies that a `bernstein compliance pack` ZIP is internally consistent:

- Every lineage entry has a valid Ed25519 detached JWS signature (RFC 7515 + RFC 8037).
- The signature is by the agent identified in the entry, using the public key
  in the bundled Agent Card.
- Entries form a valid parent-hash DAG - no orphans, no duplicates.
- Bytes that hashed and got signed are reproduced via RFC 8785 JSON
  Canonicalisation Scheme (JCS) - byte-for-byte the same as Bernstein.

See `docs/decisions/009-lineage-v1.md` §9 for the design rationale.

## Why a separate wheel

This wheel is auditor-grade. **The whole point** is that a compliance officer
on an air-gapped laptop can do:

```
pip install bernstein-verify
bernstein-verify pack ./acme-compliance-2026-q2.zip
```

…without ever installing the orchestrator. The package depends on
`cryptography>=43` and `click>=8.1` and **nothing else**. It does NOT
import from `bernstein.*` at runtime - verified by
`tests/test_no_bernstein_install.py`.

No network calls. No remote registry lookups. Pure local verification.

## Usage

```
bernstein-verify pack  <bundle.zip>
bernstein-verify chain <artefact_path> [--lineage-dir DIR]
bernstein-verify forks <artefact_path> [--lineage-dir DIR]
```

Exit code: `0` PASS, `1` FAIL. JSON report on stderr; human summary on stdout.

## Air-gap guarantee

The package imports only `cryptography` + `click` + Python stdlib. No
`httpx`, no `requests`, no `urllib.request`. There is no code path
that opens a network socket. Run inside `unshare -n` (Linux) or any
no-network sandbox; verification proceeds identically.

## License

Apache-2.0.
