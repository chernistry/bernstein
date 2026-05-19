"""Integration wiring for auto-heal to adjacent Bernstein subsystems.

This module is the single seam between the auto-heal v2 pipeline and
the four observability surfaces operators rely on:

* ``core.observability.decision_log``  - structured decision rows
  (kind = ``autoheal_strategy``) so heal actions appear in
  ``bernstein decisions tail``.
* ``eval.calibration``  - predicted-prob + observed-outcome pairs so
  the weekly Brier report includes autoheal calibration.
* ``core.autoheal.lineage_writer``  - lineage v2 child body payload
  for the audit chain.
* ``core.autoheal.audit_log``  - flat operator-readable ledger.

Each helper degrades gracefully: the import is attempted lazily and
all writes are best-effort. Failing to record observability data must
never break the heal pipeline.

Design notes
------------
* The wire is intentionally thin (zero domain logic): callers pass
  already-decided fields and we map them to the target schemas.
* Both decision and calibration writes share a single
  ``decision_id`` so cross-store joins are possible.
* Path overrides come from env vars so operators can route the
  artefacts to alternative storage (S3 sidecar, Postgres tail, etc.)
  via a thin filesystem-shim.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from bernstein.core.autoheal.audit_log import HealRecord, coerce_outcome, now_record
from bernstein.core.autoheal.audit_log import append as audit_append

logger = logging.getLogger(__name__)


ENV_DECISION_LOG_PATH = "BERNSTEIN_AUTOHEAL_DECISION_LOG_PATH"
ENV_CALIBRATION_LOG_PATH = "BERNSTEIN_AUTOHEAL_CALIBRATION_LOG_PATH"
ENV_AUDIT_LOG_PATH = "BERNSTEIN_AUTOHEAL_LOG_PATH"


@dataclass(frozen=True, slots=True)
class WireResult:
    """Aggregate outcome of one wire pass.

    Each flag captures whether the corresponding sidecar accepted the
    write. ``decision_id`` is the join key used across the three
    surfaces; it is also empty when the decision log is disabled.
    """

    decision_id: str
    decision_log_written: bool
    calibration_written: bool
    audit_written: bool


def _path_from_env(env: str, default: Path) -> Path:
    """Resolve a logging path with env override."""
    raw = os.environ.get(env)
    if raw is None or not raw.strip():
        return default
    return Path(raw.strip())


def record_heal(
    *,
    run_id: str,
    head_sha: str,
    strategy: str,
    cls: str,
    confidence: float,
    outcome: str,
    cost_usd: float = 0.0,
    llm_calls: int = 0,
    patch_sha: str = "",
    rationale: str = "",
    candidates: tuple[str, ...] = (),
    sdd_dir: Path | None = None,
) -> WireResult:
    """Write one heal action to all three observability sidecars.

    Args:
        run_id: GitHub Actions ``workflow_run`` id that triggered heal.
        head_sha: Failing commit SHA.
        strategy: Repair strategy applied (winner of the bandit pick).
        cls: Coarse safety class (``safe`` / ``heuristic`` / ``risky`` /
            ``unknown``).
        confidence: Bayesian posterior at decision time, ``[0, 1]``.
        outcome: One of the ``audit_log.Outcome`` literals.
        cost_usd: Total dollars spent.
        llm_calls: Count of LLM round-trips.
        patch_sha: Git SHA of the heal patch, or ``""`` if no push.
        rationale: One-line operator-readable explanation.
        candidates: All strategies considered (incl. the winner). Used
            for decision-log alternatives.
        sdd_dir: Override the ``.sdd/`` root (defaults to
            ``$BERNSTEIN_SDD_DIR`` or ``.sdd``).

    Returns:
        A :class:`WireResult` summarising which sidecars succeeded.
        Never raises; failed writes are logged at WARNING and reflected
        in the result flags.
    """
    sdd_root = sdd_dir if sdd_dir is not None else _sdd_root()

    decision_id = _try_record_decision(
        kind="autoheal_strategy",
        chosen=strategy,
        rationale=rationale or f"autoheal {cls} -> {strategy}",
        confidence=max(0.0, min(1.0, confidence)),
        candidates=candidates,
        run_id=run_id,
        head_sha=head_sha,
    )

    calibration_ok = _try_record_calibration(
        decision_id=decision_id,
        predicted_prob=max(0.0, min(1.0, confidence)),
        observed_outcome=outcome == "applied",
        strategy=strategy,
        cls=cls,
    )

    audit_ok = _try_append_audit(
        record=now_record(
            run_id=run_id,
            head_sha=head_sha,
            strategy=strategy,
            cls=cls,
            confidence=confidence,
            outcome=coerce_outcome(outcome),
            cost_usd=cost_usd,
            llm_calls=llm_calls,
            patch_sha=patch_sha,
            decision_id=decision_id,
            rationale=rationale,
        ),
        sdd_root=sdd_root,
    )

    return WireResult(
        decision_id=decision_id,
        decision_log_written=bool(decision_id),
        calibration_written=calibration_ok,
        audit_written=audit_ok,
    )


def _sdd_root() -> Path:
    root_env = os.environ.get("BERNSTEIN_SDD_DIR")
    return Path(root_env) if root_env else Path(".sdd")


def _try_record_decision(
    *,
    kind: str,
    chosen: str,
    rationale: str,
    confidence: float,
    candidates: tuple[str, ...],
    run_id: str,
    head_sha: str,
) -> str:
    """Append one decision-log row; returns the decision_id or ``""``."""
    try:
        from bernstein.core.observability import decision_log as dl
    except Exception as exc:  # pragma: no cover - import-only safety net
        logger.warning("autoheal: decision_log import failed: %s", exc)
        return ""

    losers: list[dl.Alternative] = [
        dl.Alternative(id=c, score=0.0, reason="bandit_loser") for c in candidates if c and c != chosen
    ]
    override_path = _path_from_env(ENV_DECISION_LOG_PATH, dl.DEFAULT_PATH)
    try:
        rec = dl.record_decision(
            kind=kind,
            chosen=chosen,
            rationale=rationale,
            confidence=confidence,
            alternatives=tuple(losers),
            policy_path=("autoheal.bandit", "autoheal.categorizer"),
            inputs={"run_id": run_id, "head_sha": head_sha},
            path=override_path,
        )
    except Exception as exc:
        logger.warning("autoheal: decision_log write failed: %s", exc)
        return ""
    if rec is None:
        return ""
    return rec.decision_id


def _try_record_calibration(
    *,
    decision_id: str,
    predicted_prob: float,
    observed_outcome: bool,
    strategy: str,
    cls: str,
) -> bool:
    """Append one calibration row; True iff the write succeeded."""
    try:
        from bernstein.eval import calibration
    except Exception as exc:  # pragma: no cover - import-only safety net
        logger.warning("autoheal: calibration import failed: %s", exc)
        return False
    override_path = _path_from_env(ENV_CALIBRATION_LOG_PATH, calibration.DEFAULT_LOG_PATH)
    try:
        calibration.log_decision(
            decision_kind="autoheal_strategy",
            policy_path=f"autoheal.bandit/{cls}",
            predicted_prob=predicted_prob,
            observed_outcome=observed_outcome,
            decision_id=decision_id or None,
            metadata={"strategy": strategy, "cls": cls},
            log_path=override_path,
        )
    except Exception as exc:
        logger.warning("autoheal: calibration write failed: %s", exc)
        return False
    return True


def _try_append_audit(*, record: HealRecord, sdd_root: Path) -> bool:
    """Append one row to the operator-readable audit ledger."""
    raw_override = os.environ.get(ENV_AUDIT_LOG_PATH, "").strip()
    dest = Path(raw_override) if raw_override else sdd_root / "autoheal-history.jsonl"
    try:
        audit_append(record, dest)
    except Exception as exc:
        logger.warning("autoheal: audit ledger write failed: %s", exc)
        return False
    return True


__all__ = [
    "ENV_AUDIT_LOG_PATH",
    "ENV_CALIBRATION_LOG_PATH",
    "ENV_DECISION_LOG_PATH",
    "WireResult",
    "record_heal",
]
