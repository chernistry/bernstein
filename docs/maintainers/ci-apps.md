# CI apps & integrations — one-time operator playbook

Forward-looking install guide for free OSS-tier GitHub Apps and platform features
that benefit the `sipyourdrink-ltd/bernstein` repo. Each section is a single
operator action: click, authorize, done. Apply in any order; nothing here is a
blocker for day-to-day development.

Tracking issue: [#1273](https://github.com/sipyourdrink-ltd/bernstein/issues/1273).

---

## 1. Enable CodeQL "default setup"

Result: GitHub-hosted CodeQL scanning + Copilot Autofix suggestions on
code-scanning alerts. Zero workflow YAML to maintain.

Steps:
- GitHub repo → **Settings** → **Code security** → **Code scanning** → **Set up** → **Default**.
- Pick the languages GitHub detects (Python is auto-suggested).
- Confirm.

Risk: CodeQL produces some false positives on first scan. Autofix proposes
patches as PR suggestions — it never auto-merges. Triage as normal review work.

---

## 2. Install CodeRabbit GitHub App

Free Pro tier for OSS repos. URL: <https://github.com/apps/coderabbitai>.

Steps:
- Click **Install** → authorize on `sipyourdrink-ltd/bernstein`.
- No repo secret required.
- Optional: add `.coderabbit.yaml` at repo root later — defaults are fine for now.

Risk: adds 1 reviewer comment per PR. Rate-limit is 4 reviews/hr/PR; bursty
force-pushes will queue.

---

## 3. Install Gemini Code Assist GitHub App

Free tier: 240 review sessions/day (2026). URL:
<https://github.com/marketplace/gemini-code-assist>.

Steps:
- **Install** → authorize on `sipyourdrink-ltd/bernstein`.
- Auth flows through the maintainer's Google account; no repo secret needed.

Risk: doubles AI-reviewer noise alongside CodeRabbit. Worth keeping for
cross-check on security-sensitive PRs; consider disabling per-PR if signal/noise
degrades.

---

## 4. Enable GitHub Actions Insights tab

Free, no install. Path: **Repo → Insights → Actions**.

Use as a 30-day "main CI green/red" gauge and per-workflow runtime trend. No
configuration needed — the tab populates from existing workflow runs.

---

## 5. Configure PyPI Trusted Publishing (OIDC)

Replaces the long-lived `PYPI_API_TOKEN` secret with short-lived OIDC tokens
minted per release run.

Steps:
- Visit <https://pypi.org/manage/account/publishing/>.
- Add a publisher: PyPI project `bernstein` → workflow `auto-release.yml`
  (or whichever workflow publishes) → environment `pypi`.
- After the next successful release run confirms OIDC works, delete the
  `PYPI_API_TOKEN` repo secret.

Risk: first-time setup requires an existing PyPI account that owns the
`bernstein` project. Keep the API token around until one OIDC release succeeds.

---

## 6. Enable GitHub merge queue

Free for org-owned public repos in 2026.

Steps: **Repo → Settings → Branches** → edit `main` branch protection rule →
enable **Merge queue**.

Caveats:
- Pair with `required_status_checks.strict: false` — merge queue is
  incompatible with "require branches to be up to date".
- Required workflows must trigger on `merge_group`:
  `on: merge_group: types: [checks_requested]`.
- Verify after [#1277](https://github.com/sipyourdrink-ltd/bernstein/pull/1277)
  lands — that PR adds the `merge_group` trigger to required workflows.

---

## 7. (Optional) StepSecurity public dashboard

URL: <https://app.stepsecurity.io>.

Steps:
- Sign in with GitHub → grant read access.
- `bernstein` appears in the dashboard automatically.

Result: egress baseline review and policy suggestions, visible once the
`harden-runner` audit mode from PR HD-6 lands and runs collect data.

Risk: external UI; the egress data stays publicly visible.

---

## 8. (Optional) Renovate vs Dependabot evaluation

Not yet. Dependabot stays the primary dependency-update bot today.

Re-evaluate in ~1 quarter against Renovate's group/dashboard features if
Dependabot PR noise becomes a problem. No action required now.
