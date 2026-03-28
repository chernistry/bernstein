# 510a — Semantic Caching Layer
**Role:** backend **Priority:** 1 **Scope:** medium

Cache semantically similar LLM requests. When a new task is similar to a previously completed one, reuse the plan/approach without calling the LLM. 30-50% API call reduction. xMemory cuts tokens from 9K to 4.7K per query.
