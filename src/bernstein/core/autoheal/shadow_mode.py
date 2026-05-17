"""Shadow-mode quarantine for new auto-heal repair strategies.

A new strategy starts in ``shadow`` status. While in shadow:

* The strategy is invoked exactly like a live one.
* Outcomes (would-have-passed-CI yes/no) are logged.
* Nothing is pushed.

After ``PROMOTION_THRESHOLD`` observations the verdict is computed:

* >= ``PROMOTION_MIN_WINS`` successes -> promote to ``active`` (now allowed
  to push patches).
* <= ``RETIREMENT_MAX_WINS`` successes -> retire (mark as broken, never
  invoke again until a human resets it).
* otherwise -> ``review`` (operator decides).

Status persists in ``.sdd/autoheal-shadow.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

ShadowStatus = Literal["shadow", "active", "retired", "review"]


PROMOTION_THRESHOLD: int = 5
PROMOTION_MIN_WINS: int = 4
RETIREMENT_MAX_WINS: int = 1


@dataclass(slots=True)
class ShadowRecord:
    """Tally of outcomes for one strategy under shadow evaluation."""

    status: ShadowStatus = "shadow"
    observations: int = 0
    wins: int = 0
    losses: int = 0

    def record(self, *, success: bool) -> None:
        """Record one observation and re-evaluate status if quorum reached."""
        if self.status in ("active", "retired"):
            # Active/retired strategies do not need shadow updates.
            return
        self.observations += 1
        if success:
            self.wins += 1
        else:
            self.losses += 1
        if self.observations >= PROMOTION_THRESHOLD:
            if self.wins >= PROMOTION_MIN_WINS:
                self.status = "active"
            elif self.wins <= RETIREMENT_MAX_WINS:
                self.status = "retired"
            else:
                self.status = "review"

    def is_allowed_to_push(self) -> bool:
        """Only ``active`` strategies may push real patches."""
        return self.status == "active"


@dataclass(slots=True)
class ShadowState:
    """All shadow records keyed by strategy name."""

    strategies: dict[str, ShadowRecord] = field(default_factory=dict)

    def ensure(self, strategy: str, *, initial: ShadowStatus = "shadow") -> ShadowRecord:
        """Get or create a record for ``strategy``."""
        if strategy not in self.strategies:
            self.strategies[strategy] = ShadowRecord(status=initial)
        return self.strategies[strategy]

    def is_allowed_to_push(self, strategy: str) -> bool:
        """Short-cut: is the given strategy promoted to active?"""
        rec = self.strategies.get(strategy)
        if rec is None:
            return False
        return rec.is_allowed_to_push()

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot."""
        return {
            "v": 1,
            "strategies": {
                name: {
                    "status": rec.status,
                    "observations": rec.observations,
                    "wins": rec.wins,
                    "losses": rec.losses,
                }
                for name, rec in self.strategies.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowState:
        """Inverse of ``to_dict``; rejects malformed entries silently."""
        out = cls()
        raw = data.get("strategies") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return out
        for name, body in raw.items():
            if not isinstance(name, str) or not isinstance(body, dict):
                continue
            status = body.get("status", "shadow")
            if status not in ("shadow", "active", "retired", "review"):
                continue
            try:
                obs = int(body.get("observations", 0))
                wins = int(body.get("wins", 0))
                losses = int(body.get("losses", 0))
            except (TypeError, ValueError):
                continue
            if obs < 0 or wins < 0 or losses < 0:
                continue
            out.strategies[name] = ShadowRecord(
                status=status,
                observations=obs,
                wins=wins,
                losses=losses,
            )
        return out


def load(path: Path) -> ShadowState:
    """Load shadow state; return a fresh state on any error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return ShadowState()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ShadowState()
    if not isinstance(parsed, dict):
        return ShadowState()
    return ShadowState.from_dict(parsed)


def save(state: ShadowState, path: Path) -> None:
    """Atomic write of shadow state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


__all__ = [
    "PROMOTION_MIN_WINS",
    "PROMOTION_THRESHOLD",
    "RETIREMENT_MAX_WINS",
    "ShadowRecord",
    "ShadowState",
    "ShadowStatus",
    "load",
    "save",
]
