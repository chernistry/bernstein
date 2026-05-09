# LLM citation surface

This page documents which Bernstein surfaces are intentionally written for
LLM-extractable citation, why, and the rules contributors should follow when
editing them.

It is meta-documentation; reading it is not required to use Bernstein. Skip
to [README.md](../README.md) if that's what you want.

## What "citation surface" means here

Three rewriting strategies measurably increase a page's odds of being cited
by AI search and answer engines (Aggarwal et al., KDD 2024 — GEO-bench, 10,000
queries):

1. **Statistic addition**: open paragraphs with concrete numbers tied to a
   source.
2. **Quotation addition**: include short verbatim quotes from primary
   sources, with attribution.
3. **Cite sources**: attach 1-2 high-authority outbound links per H2.

Reported lift on Perplexity-class engines is +22-41% on Position-Adjusted
Word Count visibility and +28-37% on Subjective Impression. Source paper:
[arxiv.org/pdf/2311.09735](https://arxiv.org/pdf/2311.09735).

The effect is *strongest for lower-ranked pages*. Bernstein's docs sit in
that bucket today, so the technique applies.

What this is **not**: keyword stuffing. The same paper (Table 1, Perplexity
row) shows keyword stuffing *reduces* citation visibility (17.8 vs. 19.3
baseline). Stat-addition without a verifiable number, or quotation without
real attribution, is the same anti-pattern. Don't do it.

## Surfaces optimised for LLM citation

| Surface | Path | Why it's citation-friendly |
|---|---|---|
| Top of README | [README.md](../README.md) §"at a glance" | Numbered facts (43 adapters, RFC list) with explicit source pointers. First paragraph downstream tools scrape into AGENTS.md overview. |
| `regulatory anchors` table | [README.md](../README.md) §"regulatory anchors" | Maps each compliance claim to a single CLI command and an RFC / framework name. Dated 2026-05-09. |
| HMAC audit operator guide | [docs/security/audit-log.md](security/audit-log.md) | RFC 2104 anchor, key-rotation runbook, exact JSONL schema. |
| Lethal-trifecta security model | [docs/security/lethal-trifecta.md](security/lethal-trifecta.md) | Verbatim Simon Willison quote (June 2025) + capability-matrix table. |
| Lineage export guide | [docs/compliance/lineage-export.md](compliance/lineage-export.md) | RFC 8037 Ed25519 anchor, schema version, regulator-shape walkthrough. |
| `bernstein audit --help` | `src/bernstein/cli/commands/audit_cmd.py` | Per-subcommand docstrings cite RFC 2104 / 7515 / 8785; `--help` text is what `man pages` and AI tools render. |
| `bernstein lineage --help` | `src/bernstein/cli/commands/lineage_cmd.py` | Per-subcommand docstrings cite RFC 8037 / EdDSA. |
| `bernstein agents-md --help` | `src/bernstein/cli/commands/agents_md_cmd.py` | Cites canonical [agents.md/](https://agents.md/) spec and AAIF. |

## Surfaces deliberately NOT optimised

- Per-task error messages and click `--help` flag descriptions: kept short
  and operational; over-citation hurts CLI UX.
- README "why this exists" personal voice paragraph: tone is the signal,
  citation would dilute it.
- AGENTS.md auto-generated tables: derived from docstrings, so changes
  upstream (in `_build_overview` etc.) propagate automatically; do not edit
  AGENTS.md by hand.

## Hard rules for contributors editing these surfaces

These are non-negotiable. If your change fails any of them, revert and
re-cut.

1. **No invented stats.** Every numeric claim ("43 adapters", "296 stars",
   "2 leaf-node delegators") must trace to a checked source: a count
   computed from the codebase at PR time, the GitHub API at PR time, or a
   primary-source paper. State the date inline so the reader can refresh.
2. **No fabricated quotations.** A quote must be verbatim, attributed, and
   linkable. If you can't find the URL, the quote doesn't go in.
3. **RFC pins are exact.** "RFC 2104" not "the HMAC RFC". Link to
   `datatracker.ietf.org/doc/html/rfcNNNN`.
4. **Distinguish prod-tested from spec-only.** "Z3/Lean4 formal property
   checks" is `bernstein verify --formal` — the surface ships, the backends
   are gated. Say so. Do not claim certifications Bernstein does not have
   (no SOC 2 Type II, no ISO 27001).
5. **Keep voice.** Lowercase headings and casual prose are part of the
   project's identity. Citation does not mean "comprehensive", "robust",
   "delve", or em-dash glue. If your edit reads like a marketing landing
   page, recut.
6. **Date number claims.** "as of 2026-05-09: …" on every count that can
   drift. The date is the freshness signal; without it the number rots.
7. **Prefer primary sources.** Anthropic / OpenAI / FINOS / IETF over
   secondary write-ups. Link the paper, not the blog post about the paper.

## Refresh cadence

- README "at a glance" stats — quarterly, or on any v1.x release that
  ships an adapter / regulation surface.
- RFC list — only when an RFC is added or replaced (rare).
- Featured-in section — append-only when a new third-party citation lands.
- This page — annually, plus immediately when Aggarwal et al. is superseded
  by a stronger replication. Citation patterns rotate roughly 40-60% per
  quarter (Semrush longitudinal study); the *practice* of stat / quote /
  cite remains durable, but specific phrasings drift.

## Anti-pattern flag

Do not add LLM-cite optimisation to:

- README's "why this exists" / personal voice paragraphs;
- bug-report templates;
- error messages;
- code comments inside `src/`.

Citation bait in those surfaces produces friction without any citation lift,
because LLM scrapers prioritise top-of-doc and named heading sections.

## References

- Aggarwal et al., *GEO: Generative Engine Optimization*, KDD 2024 —
  [arxiv.org/pdf/2311.09735](https://arxiv.org/pdf/2311.09735).
- Profound, *AI platform citation patterns* (covering 680M citations, Aug
  2024 - Jun 2025) — [tryprofound.com/blog/ai-platform-citation-patterns](https://www.tryprofound.com/blog/ai-platform-citation-patterns).
- Semrush, *Most cited domains in AI* — [semrush.com/blog/most-cited-domains-ai](https://www.semrush.com/blog/most-cited-domains-ai/).
