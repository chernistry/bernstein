# competitors

The README's [detailed comparison](../README.md#detailed-comparison) covers
both generations side by side: previous-generation Python multi-agent
frameworks (CrewAI, AutoGen, LangGraph) where the orchestrator is the LLM
itself, and current-generation CLI orchestrators (claude-flow, Archon,
vibe-kanban, claude-squad, Composio AO) where the orchestrator drives
terminal coding agents.

Bernstein's wedge is the **auditability column** — HMAC-chained audit,
signed agent cards, per-artefact lineage, air-gap deploy profile — plus
Python-library shape and the widest adapter coverage (44 CLI agents). None
of the bigger projects has the audit-chain stack; that's the column the
regulated-buyer cares about.

We are not winning on stars or polish. The honest read:

- Want the polished Go TUI for parallel Claude on a Mac → claude-squad.
- Want the swarm framing with the broadest MCP tool surface → claude-flow.
- Want a kanban board UI → vibe-kanban.
- Want the workflow-YAML primitive with web UI and chat integration → Archon.
- Need a regulator-ready audit export and on-prem behind a firewall → Bernstein.

Numbers and capability snapshots in the README tables were captured
2026-05-12 and drift over time. Run `gh api repos/<slug> --jq .stargazers_count`
to refresh.
