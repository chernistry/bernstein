# 513c — Multi-Cell Cluster Federation
**Role:** backend **Priority:** 2 **Scope:** large

Federate multiple Bernstein instances across machines. Lead orchestrator distributes to cell orchestrators. Shared state via Redis/Postgres (already have backends). Needed when agent count exceeds single-machine capacity. 67% growth in agent count per org.
