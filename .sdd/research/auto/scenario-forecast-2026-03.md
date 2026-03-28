# Bernstein Scenario Forecast (March 2026, 6-12 month horizon)

## Scenarios

| # | Scenario | p | Key Driver |
|---|----------|---|-----------|
| S1 | Breakout Niche Leader | 0.20 | First-run experience + catalog network effects |
| S2 | Steady Solo Tool | 0.40 | No adoption blocker fixed, passive CLI agent improvement |
| S3 | Competitor Subsumption | 0.20 | Claude Code ships native multi-agent, or CrewAI adds CLI |
| S4 | Ecosystem Evaporation | 0.10 | CLI agents fall out of favor, API-only pivot |
| S5 | Pivot to Platform | 0.10 | Evolution loop becomes the product, agent backend pluggable |

## Top-5 Anchors (Leverage)

1. First-run experience / time-to-value (0.92)
2. CLI agent ecosystem growth (0.85)
3. Self-evolution ROI visibility (0.78)
4. Agent catalog / marketplace (0.72)
5. Competitor CLI support (0.65)

## Highest-Leverage Tickets

1. #412 Rich Context Injection — shifts S1 by +0.08
2. #400-406 Agent Catalogs — shifts S1 by +0.06, enables network effects
3. #420-422 Performance — shifts S1 by +0.05, blocks scalability

## Missing Tickets (now created)

- #416 Zero-to-running demo (`bernstein demo`) — first-run experience
- #417 Evolution showcase (`bernstein evolve --status`) — make evolution visible
- #418 Agent conflict resolution — merge strategy for concurrent agents

## Early Indicators

- External PR within 8 weeks → p(S1) += 0.10
- Claude Code "parallel agents" in changelog → p(S3) += 0.15
- Zero contributors after 6 months → lock p(S2) to 0.55+
- Task success rate > 80% sustained → p(S1) += 0.05
- Evolution acceptance rate > 35% → p(S5) += 0.05

## Decision Points

- Month 3: Zero contributors + <100 stars → pivot to first-run experience
- Month 6: Claude Code ships multi-agent → accelerate platform pivot (S5)
- Month 9: Task success rate < 60% → add mixed CLI+API agent mode
