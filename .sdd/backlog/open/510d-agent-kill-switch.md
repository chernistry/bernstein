# 510d — Agent Kill Switch with Purpose Enforcement
**Role:** backend **Priority:** 0 **Scope:** small

Real-time circuit breaker: if agent exceeds scope (edits outside boundary), exceeds budget, or triggers guardrail violations → auto-terminate + quarantine changes. 60% of orgs cannot terminate misbehaving agents (Cisco 2026).
