"""Bernstein autofix daemon - auto-repair CI failures on Bernstein PRs.

The autofix package watches a configured set of GitHub repositories for
failed checks on pull requests opened by a Bernstein session, claims
ownership via the ``bernstein-session-id`` trailer added by
``bernstein pr``, classifies each failure into a routing bucket, and
dispatches a fresh deterministic Bernstein run with a goal scoped to
the failing logs.

The package is intentionally split across several modules so each
concern can be unit-tested in isolation:

* :mod:`bernstein.core.autofix.config` - typed reader for the
  ``~/.config/bernstein/autofix.toml`` configuration file.
* :mod:`bernstein.core.autofix.classifier` - pure-function classifier
  that maps a failing-log blob to ``flaky``, ``config`` or ``security``
  and chooses a bandit arm (``sonnet`` / ``haiku`` / ``opus``).
* :mod:`bernstein.core.autofix.gh_logs` - wraps ``gh run view
  --log-failed`` and applies the configured byte budget.
* :mod:`bernstein.core.autofix.ownership` - reads PR metadata,
  validates the ``bernstein-session-id`` trailer, and enforces the
  ``bernstein-autofix`` label gate.
* :mod:`bernstein.core.autofix.dispatcher` - orchestrates a single
  attempt: cost-cap check, classifier lookup, audit-chain open, goal
  synthesis, dispatch, audit-chain close.
* :mod:`bernstein.core.autofix.metrics` - Prometheus counters that
  surface attempts and spend per repo.
* :mod:`bernstein.core.autofix.daemon` - process supervisor that
  exposes ``start``, ``stop``, ``status`` and ``attach`` semantics.
* :mod:`bernstein.core.autofix.tier3` - OpenRouter free-tier shadow-mode
  escalation that picks up when Tier-1 (deterministic contract-drift
  regen) and Tier-2 (Gemini auto-heal) both produced nothing on a
  failing class in the safe allowlist. Captures a unified-diff under
  ``.sdd/autoheal/tier3-shadow/`` plus lineage / decision-log /
  envelope rows; does not push unless
  ``BERNSTEIN_CI_SELF_DRIVE_PROMOTE_FROM_SHADOW=1`` is set.
"""

from __future__ import annotations

from bernstein.core.autofix.classifier import (
    Classification,
    classify_failure,
)
from bernstein.core.autofix.config import (
    AutofixConfig,
    RepoConfig,
    load_config,
)
from bernstein.core.autofix.dispatcher import (
    AttemptOutcome,
    AttemptRecord,
    Dispatcher,
)
from bernstein.core.autofix.gh_logs import (
    LogExtraction,
    extract_failed_log,
)
from bernstein.core.autofix.ladder import (
    AutofixOutcome,
    CIFailure,
    Rung,
    RungSelection,
    build_default_ladder,
    emit_ladder_event,
    fire_rung,
    select_rung,
)
from bernstein.core.autofix.metrics import (
    autofix_attempts_total,
    autofix_cost_usd_total,
)
from bernstein.core.autofix.ownership import (
    OwnershipDecision,
    PullRequestMetadata,
    decide_ownership,
)
from bernstein.core.autofix.telemetry_grounded import (
    GhaFailureSource,
    GroundedDispatchHook,
    GroundedDispatchResult,
    GroundingContext,
    GroundingRetriever,
    RecentJsonlLogRetriever,
    SentrySource,
    StubSource,
    TelemetryDispatchRecord,
    TelemetryEvent,
    TelemetrySettings,
    TelemetrySource,
    TelemetrySourceConfig,
    build_default_sources,
    build_grounded_goal,
    dispatch_telemetry_event,
    load_telemetry_settings,
    verify_webhook_signature,
)

__all__ = [
    "AttemptOutcome",
    "AttemptRecord",
    "AutofixConfig",
    "AutofixOutcome",
    "CIFailure",
    "Classification",
    "Dispatcher",
    "GhaFailureSource",
    "GroundedDispatchHook",
    "GroundedDispatchResult",
    "GroundingContext",
    "GroundingRetriever",
    "LogExtraction",
    "OwnershipDecision",
    "PullRequestMetadata",
    "RecentJsonlLogRetriever",
    "RepoConfig",
    "Rung",
    "RungSelection",
    "SentrySource",
    "StubSource",
    "TelemetryDispatchRecord",
    "TelemetryEvent",
    "TelemetrySettings",
    "TelemetrySource",
    "TelemetrySourceConfig",
    "autofix_attempts_total",
    "autofix_cost_usd_total",
    "build_default_ladder",
    "build_default_sources",
    "build_grounded_goal",
    "classify_failure",
    "decide_ownership",
    "dispatch_telemetry_event",
    "emit_ladder_event",
    "extract_failed_log",
    "fire_rung",
    "load_config",
    "load_telemetry_settings",
    "select_rung",
    "verify_webhook_signature",
]
