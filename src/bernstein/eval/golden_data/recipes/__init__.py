"""Golden trace fixtures for the bundled recipe library.

Each ``<recipe-name>.trace.yaml`` captures the expected per-node
execution order and node kinds (agent role / command body) after
parameter substitution.  The eval harness loads these fixtures and
re-renders the recipe against the canned parameters; any drift in the
resolved workflow shape produces a regression failure.

The fixtures intentionally pin only the *shape* of the resolved
workflow — node ids, kinds, agent roles, and command templates with
parameters substituted in.  They do not pin the full prompt body
because prompt wording is allowed to evolve without breaking the
recipe contract.
"""
