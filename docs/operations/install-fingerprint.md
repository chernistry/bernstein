# install-fingerprint

operator-only doc for the install-rev fingerprint primitive shipped under
`src/bernstein/core/identity/`.  combines DOCS-001 (what the
fingerprint is, how to enable, kill switch, privacy promise) and OPS-001
(operator runbook for seed generation, storage, rotation, decode).

> tl;dr — bernstein writes a stable 16-char base32 token into yaml
> configs, trace jsonl, and role-prompt md footers **only when the
> operator opts in**.  the token is hmac-sha256 over the operator's
> seed; users without the seed cannot mint valid tokens.  no network
> egress, ever — the operator discovers installs via `gh search code
> 'bernstein-rev:'`.

## what it is

| field | value |
|-------|-------|
| algorithm | hmac-sha256(seed, nonce ‖ version_major) → leftmost 80 bits → lowercase base32 |
| width | 16 chars (no padding), e.g. `c4j2k7n8p3q5r9s7` |
| seed | operator's 256-bit secret in `BERNSTEIN_IDENTITY_SEED` (hex) |
| nonce | random 80-bit per-install, persisted at `~/.bernstein/install_nonce` |
| collision floor | <1 expected hit per 10y across 2B github-indexed files (birthday math, 80 bits) |
| network | none.  ever.  passive observation of public artefacts only. |

design rationale lives in
`.sdd/audit/2026-05-09-fingerprint-design.md` (gitignored, operator-only).

## where it gets emitted

three slots, evaluated independently per artefact:

| slot | path | shape |
|------|------|-------|
| primary (yaml) | `bernstein.yaml`, `.bernstein/workflows/*.yaml` | `# bernstein-rev: <token>` (head comment) |
| backup #1 (trace jsonl) | `.sdd/traces/<task_id>.jsonl` and `.sdd/traces/trace-<id>.json` | top-level `"_rev": "<token>"` field |
| backup #2 (role prompts) | rendered system-prompt md footer | `<!-- bernstein-rev: <token> -->` (last line) |

defence in depth: independent strip probabilities mean ~99% of
artefacts retain at least one slot after a typical copy-paste round.

## how to enable (operator-only)

emission is **off by default**.  every emit site checks
`IDENTITY_EMISSION_ENABLED` (module attribute on
`bernstein.core.identity.install_rev`) and `BERNSTEIN_DISABLE_IDENTITY`
(user env var).  flipping the module flag is the operator's call once
the seed is in place.

minimum sequence to enable in production:

1. **mint the seed once** — see [seed runbook](#seed-runbook) below.
2. **store the seed** in 1password / yubikey / `pass` — never commit it.
3. **set `BERNSTEIN_IDENTITY_SEED=<hex>`** in the operator's local env
   and in any ci secret store that runs verify/decode commands.
4. **flip `IDENTITY_EMISSION_ENABLED=True`** at the module head.
   review the diff, ship it, and now end-user installs that pull this
   release will start embedding tokens.
5. **verify** by running `bernstein identity show` on a fresh install
   — confirm the output is a 16-char base32 string, not the sentinel
   `0000000000000000`.

## how to discover installs (operator-only)

```bash
# union of all three slots — the easiest sweep
gh search code 'bernstein-rev:' --limit 1000

# yaml-only
gh search code 'bernstein-rev:' language:yaml --limit 1000

# trace-only
gh search code '"_rev":' '"trace_id"' language:json --limit 1000

# md-only
gh search code 'bernstein-rev:' extension:md --limit 1000
```

rate limit: 30 queries / minute authenticated, 1000 results per query
cap.  the operator paginates by `repo:` filter for larger sweeps.

dedup hits by `(token, repo, path)`.  roll up by `token` to count
distinct installs.  flag tokens that span >5 unrelated repos as
suspected forgery / replay (a single install rarely lands in many
repos).

## privacy promise (public-facing, do not regress)

* **no telemetry** — the bernstein process never opens a network
  connection to phone home install state.
* **no install identity in the token** — the nonce is opaque random
  bytes, not derived from machine-id, mac, hostname, or git config.
  even with infinite compute, an operator cannot deanonymise a token
  back to a person.
* **kill switch is documented and works** — `BERNSTEIN_DISABLE_IDENTITY=1`
  short-circuits every emit site, returning the fixed sentinel
  `0000000000000000`.  users who notice the comment and want it gone
  can also delete the line; it does not regenerate.
* **first-run disclosure** — `bernstein init` should print a one-line
  notice when emission is on (todo, separate ticket; the wiring is in,
  the line is the operator's call).

the ethical line is *passive observation of public artefacts the user
voluntarily published*, not *active phone-home from the user's box*.
this design sits firmly on the passive side.

## kill switch — user-facing

```bash
# suppress every emit site without code edits
export BERNSTEIN_DISABLE_IDENTITY=1
```

equivalently:

```bash
bernstein identity disable
# prints the export line to copy into shell rc
```

after setting this, `bernstein identity show` returns the sentinel
`0000000000000000`, every yaml render skips the comment, every trace
write skips the `_rev` field, and every role-prompt render skips the
footer.

---

## seed runbook

### generate the operator seed (once, ever)

```bash
# 256 bits of csprng entropy, hex-encoded
openssl rand -hex 32
# → e.g. 5d4a8a4b9...c4 (64 hex chars / 32 bytes)
```

store the output in **one** of:

* 1password vault (recommended — survives laptop loss)
* `pass` (gnu password manager) under `bernstein/identity-seed`
* yubikey hardware token (for cold-storage backup)

**never** commit the seed.  **never** paste it into a chat / issue /
ticket.  treat it like an ssh private key.

### load the seed into a session

```bash
# manual — single shell session
export BERNSTEIN_IDENTITY_SEED=$(op read 'op://operator/bernstein/identity-seed')

# or with pass
export BERNSTEIN_IDENTITY_SEED=$(pass bernstein/identity-seed)

# verify it loaded
bernstein identity show
```

`bernstein identity show` should now print a real 16-char token instead
of the sentinel.

### rotate the seed

rotate when:

* the operator's laptop is lost / compromised
* the seed is suspected to have leaked
* a calendar reminder fires (recommended cadence: annually)

rotation procedure:

1. mint a new seed (`openssl rand -hex 32`) and store alongside the
   old one — keep both for the cohort window (see step 4).
2. set `BERNSTEIN_IDENTITY_SEED=<new_hex>` in operator env / ci.
3. existing user installs continue to emit tokens computed from the
   *old* seed (their nonce is unchanged).  the operator's verify path
   needs to try both seeds during the cohort window.
4. once enough time has passed that all surviving installs have rolled
   to a release minted under the new seed (typically 6-12 months), the
   old seed can be archived and removed from the verify path.

decode utility usage:

```bash
# shape + sentinel check (works without the user's nonce)
bernstein identity decode c4j2k7n8p3q5r9s7
# → "valid" (exit 0) or "invalid" (exit 1) or seed-missing (exit 2)

# full hmac-strength verification (requires the user's nonce, e.g. via
# a debug bundle the user voluntarily shared)
bernstein identity verify c4j2k7n8p3q5r9s7 \
  --nonce 0123456789abcdef0123 \
  --version-major 1
```

### ci storage

set as a ci secret named `BERNSTEIN_IDENTITY_SEED` in the operator's
runner config.  jobs that need it (verify / decode workflows, sweep
cron) reference the secret.  user-facing build jobs do **not** need
the seed — emission only needs the seed when the seed is set, and ci
that builds artefacts for users should not be embedding the operator
seed into those artefacts.

### what the operator gets

a single `gh search code 'bernstein-rev:'` returns hits scoped to public
repos, with file path + raw url.  the operator's analytics:

* count of distinct tokens → distinct installs visible to public github
* per-token first-seen → install lifetime in the wild
* per-token unique repo count → forks / mirrors / replays heuristic

write the discovered hits to
`.sdd/audit/install-fingerprint-hits.jsonl` (operator-only, git-
ignored) for trend analysis.  schema:

```jsonl
{"discovered_at": "...", "token": "...", "repo": "...", "path": "...", "slot": "yaml-comment|trace-jsonl|md-footer", "raw_url": "..."}
```

### follow-ups (deferred, not in this wiring pr)

* **first-run disclosure** — print a one-line notice from
  `bernstein init` when emission is on.  needs a small flag in the
  wizard to suppress it for `--non-interactive`.
* **`bernstein identity sweep`** — `gh search code` wrapper that
  produces `install-fingerprint-hits.jsonl`.  out-of-scope for this pr;
  the discovery query is a manual one-liner anyway.
* **probe script** — `scripts/probe_fingerprint_falsepos.py` from
  design doc §4 to validate gh has zero false positives on the chosen
  encoding.  also out-of-scope; run it once before flipping
  `IDENTITY_EMISSION_ENABLED=True`.
