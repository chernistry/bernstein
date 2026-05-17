"""Multi-arm-bandit strategy selection for auto-heal.

Each named repair strategy (``ruff-format``, ``agents-md-sync``,
``typos-allowlist``, etc.) is one arm. Outcomes are Bernoulli: a heal
attempt either lands a green PR (success) or it does not (failure).

We use Beta-Bernoulli Thompson sampling. The state file
``.sdd/autoheal-bandit.json`` carries per-strategy ``alpha`` (1 + wins)
and ``beta`` (1 + losses) counters; on selection we draw one sample
per arm and pick the strategy with the highest draw.

Design notes
------------

* No external RNG required. Caller may pass a ``random.Random`` for
  deterministic tests; otherwise a module-level default is used. For
  *replay* the caller may set ``BERNSTEIN_AUTOHEAL_BANDIT_SEED`` so
  the picked arm is reproducible from an audit log.
* The state file is gitignored under ``.sdd/``. Persistence is best-effort
  and tolerant of missing or corrupt files (falls back to fresh priors).
* Strategy names are caller-defined; the bandit does not enforce any
  set, so adding a new strategy at runtime starts it with the
  uninformative prior ``Beta(1, 1)``.
* Shadow-mode promotion / retirement is enforced *outside* this module
  (see ``shadow_mode``). The bandit purely picks the next arm to try
  from the active set.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pathlib import Path

ENV_SEED: Final[str] = "BERNSTEIN_AUTOHEAL_BANDIT_SEED"
"""When set to a non-empty integer, ``select`` seeds the RNG from it.

This makes a heal pick reproducible from an audit log row that carries
the same seed in ``meta``. Empty / non-integer values fall back to a
fresh ``random.Random()``.
"""


@dataclass(slots=True)
class ArmState:
    """Beta prior for one bandit arm.

    ``alpha`` and ``beta`` start at 1.0 (uniform). A win increments
    ``alpha``; a loss increments ``beta``. The mean reward estimate is
    ``alpha / (alpha + beta)``; uncertainty shrinks with more pulls.
    """

    alpha: float = 1.0
    beta: float = 1.0

    @property
    def pulls(self) -> int:
        """Total observations on this arm (wins + losses)."""
        return round(self.alpha + self.beta - 2.0)

    @property
    def mean(self) -> float:
        """Posterior mean reward estimate."""
        return self.alpha / (self.alpha + self.beta)

    def sample(self, rng: random.Random) -> float:
        """Draw one Thompson sample from the posterior."""
        return rng.betavariate(self.alpha, self.beta)


@dataclass(slots=True)
class BanditState:
    """In-memory representation of all known arms."""

    arms: dict[str, ArmState] = field(default_factory=dict)

    def ensure(self, strategy: str) -> ArmState:
        """Get the arm for ``strategy``, creating it with Beta(1, 1) if new."""
        if strategy not in self.arms:
            self.arms[strategy] = ArmState()
        return self.arms[strategy]

    def record(self, strategy: str, *, success: bool) -> None:
        """Update the arm posterior after observing one outcome."""
        arm = self.ensure(strategy)
        if success:
            arm.alpha += 1.0
        else:
            arm.beta += 1.0

    def select(
        self,
        candidates: list[str],
        *,
        rng: random.Random | None = None,
    ) -> str:
        """Thompson-sample one arm out of ``candidates``.

        Raises ``ValueError`` if ``candidates`` is empty.

        Replay: if ``rng`` is None and ``BERNSTEIN_AUTOHEAL_BANDIT_SEED``
        is set to an integer, the local RNG is seeded with that value
        so the pick is reproducible.
        """
        if not candidates:
            raise ValueError("cannot select from empty candidate set")
        r = rng if rng is not None else _make_rng_from_env()
        best_strategy = candidates[0]
        best_draw = -1.0
        for strategy in candidates:
            arm = self.ensure(strategy)
            draw = arm.sample(r)
            if draw > best_draw:
                best_draw = draw
                best_strategy = strategy
        return best_strategy

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot."""
        return {
            "v": 1,
            "arms": {k: {"alpha": v.alpha, "beta": v.beta} for k, v in self.arms.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BanditState:
        """Inverse of ``to_dict``; tolerates missing / malformed fields."""
        out = cls()
        raw_arms = data.get("arms") if isinstance(data, dict) else None
        if not isinstance(raw_arms, dict):
            return out
        for name, body in raw_arms.items():
            if not isinstance(body, dict):
                continue
            alpha = body.get("alpha", 1.0)
            beta = body.get("beta", 1.0)
            try:
                a_f = float(alpha)
                b_f = float(beta)
            except (TypeError, ValueError):
                continue
            if a_f <= 0 or b_f <= 0:
                continue
            out.arms[str(name)] = ArmState(alpha=a_f, beta=b_f)
        return out


def _make_rng_from_env() -> random.Random:
    """Return an RNG seeded from ``ENV_SEED`` when present, else fresh."""
    raw = os.environ.get(ENV_SEED, "").strip()
    if not raw:
        return random.Random()
    try:
        seed = int(raw)
    except ValueError:
        return random.Random()
    return random.Random(seed)


def load_state(path: Path) -> BanditState:
    """Load bandit state from disk; return a fresh state on any error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return BanditState()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return BanditState()
    if not isinstance(parsed, dict):
        return BanditState()
    return BanditState.from_dict(parsed)


def save_state(state: BanditState, path: Path) -> None:
    """Write bandit state to disk atomically (write+rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = [
    "ENV_SEED",
    "ArmState",
    "BanditState",
    "load_state",
    "save_state",
]
