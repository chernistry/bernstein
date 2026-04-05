---
description: Start a Bernstein orchestration run with a goal
---

Start a multi-agent orchestration run. Bernstein decomposes the goal into tasks, spawns CLI agents in parallel, verifies their output, and merges results.

Usage: /bernstein:run $ARGUMENTS

The argument is the goal string describing what you want built or fixed. Bernstein handles task decomposition, agent assignment, and verification automatically.
