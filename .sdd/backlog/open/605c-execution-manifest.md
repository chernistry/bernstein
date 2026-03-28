# 605c — Reproducible Execution Manifest
**Role:** backend **Priority:** 2 **Scope:** small

Each run records frozen snapshot: model ID, prompt template hash, tool versions, git commit, config. `bernstein replay` recreates exact environment. EU AI Act high-risk requirement.
