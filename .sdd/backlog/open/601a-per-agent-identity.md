# 601a — Per-Agent Identity with Least Privilege
**Role:** backend **Priority:** 1 **Scope:** medium

Each agent gets scoped credentials: file paths it may modify, APIs it may call, branches it may push to. Enforced by spawner at launch + guardrails post-execution. Only 21.9% of enterprises do this (Cisco 2026).
