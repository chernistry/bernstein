"""custom-adapter — claude-mock adapter for offline CI dev.

Worked example of the bernstein adapter extension point. The package
contributes a :class:`ClaudeMockAdapter` that returns deterministic,
canned responses without spawning any subprocess. Useful for:

* offline CI where real model calls would consume budget.
* contract / golden-file tests where output must be byte-stable.
* demos that need bernstein's full orchestration loop without latency.

The adapter registers itself via the ``bernstein.adapters`` entry-point
group and shows up under the slug ``claude_mock``.
"""

from __future__ import annotations

from custom_adapter._adapter import ClaudeMockAdapter

__all__ = ["ClaudeMockAdapter"]
