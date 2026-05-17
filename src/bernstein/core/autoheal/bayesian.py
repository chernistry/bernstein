"""Per-class Bayesian confidence for auto-heal classification.

The categorizer assigns each failing job a coarse safety class
(``safe`` / ``heuristic`` / ``risky`` / ``unknown``). The Bayesian
layer turns that into a calibrated numeric prior that informs
downstream gating (e.g. "auto-merge only if posterior >= 0.85").

Model
-----
For each (job_name, class) we maintain a Beta(alpha, beta) prior over
``P(autoheal-of-this-job-succeeds | classified-as-class)``. The prior
defaults to Beta(2, 2) for ``safe`` (weakly optimistic), Beta(1, 2)
for ``heuristic``, and Beta(1, 5) for ``risky`` / ``unknown``
(strongly pessimistic).

After each heal attempt, the (job, class) Beta gets updated by 1 in
either ``alpha`` (success) or ``beta`` (failure). The posterior mean
``alpha / (alpha + beta)`` is the confidence.

This module is pure-Python and the persisted state lives at
``.sdd/autoheal-bayes.json``. The state is gitignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

SafetyClass = Literal["safe", "heuristic", "risky", "unknown"]


_DEFAULT_PRIORS: dict[str, tuple[float, float]] = {
    "safe": (2.0, 2.0),
    "heuristic": (1.0, 2.0),
    "risky": (1.0, 5.0),
    "unknown": (1.0, 5.0),
}


@dataclass(slots=True)
class ConfidenceState:
    """Posteriors keyed by ``"<class>:<job_name>"``.

    The class is part of the key so the same job classified later as a
    different class (rule churn) gets a fresh prior, not a poisoned one.
    """

    posteriors: dict[str, tuple[float, float]] = field(default_factory=dict)

    @staticmethod
    def _key(cls: SafetyClass, job_name: str) -> str:
        return f"{cls}:{job_name}"

    def confidence(self, cls: SafetyClass, job_name: str) -> float:
        """Return the posterior-mean success probability."""
        a, b = self.posteriors.get(self._key(cls, job_name), _DEFAULT_PRIORS[cls])
        return a / (a + b)

    def update(self, cls: SafetyClass, job_name: str, *, success: bool) -> None:
        """Bayesian update: +1 to alpha on success, +1 to beta on failure."""
        key = self._key(cls, job_name)
        a, b = self.posteriors.get(key, _DEFAULT_PRIORS[cls])
        if success:
            a += 1.0
        else:
            b += 1.0
        self.posteriors[key] = (a, b)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot. Tuple keys are flattened to strings."""
        return {
            "v": 1,
            "posteriors": {k: list(v) for k, v in self.posteriors.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfidenceState:
        """Inverse of ``to_dict``; rejects invalid entries silently."""
        out = cls()
        raw = data.get("posteriors") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            if not isinstance(k, str):
                continue
            if not isinstance(v, list) or len(v) != 2:
                continue
            try:
                a = float(v[0])
                b = float(v[1])
            except (TypeError, ValueError):
                continue
            if a <= 0 or b <= 0:
                continue
            out.posteriors[k] = (a, b)
        return out


def load(path: Path) -> ConfidenceState:
    """Load Bayesian state; return a fresh state on any error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return ConfidenceState()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ConfidenceState()
    if not isinstance(parsed, dict):
        return ConfidenceState()
    return ConfidenceState.from_dict(parsed)


def save(state: ConfidenceState, path: Path) -> None:
    """Atomic write of Bayesian state to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = [
    "ConfidenceState",
    "SafetyClass",
    "load",
    "save",
]
