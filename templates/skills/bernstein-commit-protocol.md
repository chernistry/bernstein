---
name: bernstein-commit-protocol
description: Git commit conventions and branch naming for Bernstein
whenToUse: When committing changes, creating branches, or opening pull requests
---

**Branch naming**: Use `agent/<session-id>` format (already set up if working in a worktree).

**Commit message format** (Conventional Commits):

```
<type>: <short description>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `style`

**Stage and commit**:

```bash
git add -A && git commit -m "feat: <brief summary>"
```

**Rules**:
- Default branch is `main` — NEVER push to or create a branch called `master`
- When pushing: `git push origin main`
- PRs target `main` as base branch
- Never skip hooks (`--no-verify`)
