"""Detector for promptware-style cross-agent command-and-control payloads.

This module scans tool output a Bernstein-managed agent has produced (and that
a downstream agent will consume) for imperative natural-language tasking that
matches the public Agent Commander promptware C2 corpus. Detection runs
inline on tool-output ingest; the hot path uses regex bag matching plus a
density score and a Bayesian threshold by output-size bucket. No LLM is in
the hot path.

The classifier emits a :class:`PromptwareScore` between ``0.0`` and ``1.0``
together with a deterministic list of reason strings. The bands used by
callers are:

* ``score > 0.7`` -> WARN log line.
* ``score > 0.9`` -> structured lifecycle event so plugins can subscribe and
  abort the next-agent spawn.

The detector is intentionally simple and predictable. Operators tune the
band thresholds and may flip the feature on or off via the
``BERNSTEIN_PROMPTWARE_DETECTOR`` environment variable (the wiring layer
reads the flag; this module always classifies on request).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

__all__ = [
    "ABORT_THRESHOLD",
    "ENV_FLAG",
    "WARN_THRESHOLD",
    "PromptwareDetector",
    "PromptwareScore",
    "PromptwareVerdict",
    "SizeBucket",
    "bucket_for_size",
    "is_enabled",
]


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public thresholds (acceptance criteria from the spec)
# ---------------------------------------------------------------------------

WARN_THRESHOLD: Final[float] = 0.7
"""Score strictly above which callers should emit a WARN log."""

ABORT_THRESHOLD: Final[float] = 0.9
"""Score strictly above which callers should post a lifecycle abort event."""

ENV_FLAG: Final[str] = "BERNSTEIN_PROMPTWARE_DETECTOR"
"""Environment flag controlling whether the detector runs on ingest."""


def is_enabled(env: dict[str, str] | None = None) -> bool:
    """Return ``True`` when the detector is opted in via env flag.

    The default is off so deployments measure precision before turning it
    into a hard guardrail. Truthy values are case-insensitive: ``on``,
    ``1``, ``true``, ``yes``.
    """
    source = env if env is not None else dict(os.environ)
    raw = source.get(ENV_FLAG, "").strip().lower()
    return raw in {"on", "1", "true", "yes"}


# ---------------------------------------------------------------------------
# Size buckets and Bayesian priors (per spec: "Bayesian threshold per output
# size class"). The priors are calibrated against the in-repo corpus.
# ---------------------------------------------------------------------------


class SizeBucket(StrEnum):
    """Output-size bucket used to pick the Bayesian prior.

    Buckets are ordered shortest to longest. A short snippet that screams
    "execute X" is far more suspicious than the same string buried inside
    a megabyte log; the prior shifts accordingly.
    """

    TINY = "tiny"  # <= 256 bytes
    SMALL = "small"  # <= 4 KiB
    MEDIUM = "medium"  # <= 64 KiB
    LARGE = "large"  # > 64 KiB


_BUCKET_BOUNDARIES: tuple[tuple[int, SizeBucket], ...] = (
    (256, SizeBucket.TINY),
    (4 * 1024, SizeBucket.SMALL),
    (64 * 1024, SizeBucket.MEDIUM),
)


def bucket_for_size(byte_length: int) -> SizeBucket:
    """Map a byte length to a :class:`SizeBucket`."""
    for upper, bucket in _BUCKET_BOUNDARIES:
        if byte_length <= upper:
            return bucket
    return SizeBucket.LARGE


# Prior probability that a piece of tool output of this size is promptware.
# Tuned so a single strong imperative phrase in a tiny snippet trips WARN.
_BAYES_PRIORS: Final[dict[SizeBucket, float]] = {
    SizeBucket.TINY: 0.22,
    SizeBucket.SMALL: 0.12,
    SizeBucket.MEDIUM: 0.06,
    SizeBucket.LARGE: 0.03,
}


# ---------------------------------------------------------------------------
# Regex patterns. Each pattern carries a stable id (for telemetry) and a
# per-hit weight in [0.0, 1.0] used by the Bayesian update.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Pattern:
    pattern_id: str
    regex: re.Pattern[str]
    weight: float
    reason: str


_PATTERNS: Final[tuple[_Pattern, ...]] = (
    _Pattern(
        pattern_id="imperative.you_must",
        regex=re.compile(r"(?i)\byou\s+must\b"),
        weight=0.75,
        reason="imperative phrase 'you must'",
    ),
    _Pattern(
        pattern_id="imperative.ignore_previous",
        regex=re.compile(r"(?i)\bignore\s+(?:all\s+|the\s+)?previous\b"),
        weight=0.90,
        reason="prompt-injection phrase 'ignore previous'",
    ),
    _Pattern(
        pattern_id="imperative.run_following",
        regex=re.compile(r"(?i)\brun\s+the\s+following\b"),
        weight=0.80,
        reason="imperative phrase 'run the following'",
    ),
    _Pattern(
        pattern_id="imperative.execute",
        regex=re.compile(
            r"(?i)\bexecute\s+(?:(?:the|this|that|these|following|resulting|attached)\s+)*"
            r"(?:command|script|code|shell|payload|now|it)\b",
        ),
        weight=0.75,
        reason="imperative verb 'execute' targeting a payload",
    ),
    _Pattern(
        pattern_id="imperative.exfiltrate",
        regex=re.compile(r"(?i)\bexfiltrate\b"),
        weight=0.90,
        reason="explicit exfiltration verb",
    ),
    _Pattern(
        pattern_id="imperative.send_to",
        regex=re.compile(r"(?i)\bsend\s+(?:it|them|this|the\s+\w+|to)\s+to\s+https?://"),
        weight=0.75,
        reason="instruction to send data to a remote URL",
    ),
    _Pattern(
        pattern_id="imperative.disregard_instructions",
        regex=re.compile(
            r"(?i)\b(?:disregard|forget|override)\s+(?:your\s+|the\s+|all\s+)?(?:earlier\s+|prior\s+|previous\s+)?(?:instructions?|system\s+prompt|guardrails?)\b"
        ),
        weight=0.90,
        reason="instruction to disregard guardrails",
    ),
    _Pattern(
        pattern_id="imperative.you_are_now",
        regex=re.compile(r"(?i)\byou\s+are\s+now\b"),
        weight=0.70,
        reason="role-hijack phrase 'you are now'",
    ),
    _Pattern(
        pattern_id="imperative.next_agent",
        regex=re.compile(r"(?i)\b(?:to\s+the\s+next|next)\s+agent\b"),
        weight=0.70,
        reason="cross-agent tasking phrase 'next agent'",
    ),
    _Pattern(
        pattern_id="imperative.curl_pipe_shell",
        regex=re.compile(r"(?i)curl\s+[^\n]*\|\s*(?:sh|bash|zsh)\b"),
        weight=0.85,
        reason="curl-pipe-shell payload",
    ),
    _Pattern(
        pattern_id="imperative.base64_payload",
        regex=re.compile(r"(?i)base64\s+(?:-d|--decode|-D)"),
        weight=0.65,
        reason="base64 decode payload",
    ),
    _Pattern(
        pattern_id="imperative.fetch_and_run",
        regex=re.compile(
            r"(?i)\b(?:download|fetch|get)\b[^\n]{0,40}\b(?:and\s+(?:run|execute)|then\s+(?:run|execute))\b"
        ),
        weight=0.85,
        reason="fetch-and-run instruction",
    ),
    _Pattern(
        pattern_id="density.imperative_count",
        regex=re.compile(
            r"(?im)^\s*(?:please\s+)?(?:do|run|execute|send|fetch|download|post|upload|delete|stop)\b[^\n]{0,80}$"
        ),
        weight=0.40,
        reason="multiple imperative lines",
    ),
)


# Plain URL detection - used for density features.
_URL_RX: Final[re.Pattern[str]] = re.compile(r"https?://[^\s'\"<>)]+")

# Shell-command-like tokens used for density features.
_COMMAND_TOKEN_RX: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:^|[\s`])(?:curl|wget|bash|sh|zsh|python3?|node|rm|mv|cp|chmod|chown|scp|rsync|nc|netcat|nmap|sudo|apt|brew|pip|npm)\b",
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class PromptwareVerdict(StrEnum):
    """Coarse-grained verdict used by callers that do not want a float."""

    BENIGN = "benign"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"


@dataclass(frozen=True, slots=True)
class PromptwareScore:
    """Output of :meth:`PromptwareDetector.classify`.

    Attributes:
        score: Probability in ``[0.0, 1.0]`` that the input is promptware.
        verdict: Coarse verdict derived from the score and thresholds.
        reasons: Ordered, deduplicated reason strings explaining the score.
        matched_pattern_ids: Stable pattern identifiers that fired, in the
            order they fired. Used by telemetry as labels.
        size_bucket: The :class:`SizeBucket` used to pick the prior.
        url_density: URLs per 1000 bytes of input.
        command_density: Command-like tokens per 1000 bytes of input.
        text_length: Byte length of the input.
    """

    score: float
    verdict: PromptwareVerdict
    reasons: tuple[str, ...] = field(default_factory=tuple)
    matched_pattern_ids: tuple[str, ...] = field(default_factory=tuple)
    size_bucket: SizeBucket = SizeBucket.SMALL
    url_density: float = 0.0
    command_density: float = 0.0
    text_length: int = 0

    @property
    def is_warn(self) -> bool:
        """``True`` iff callers should emit a WARN log line."""
        return self.score > WARN_THRESHOLD

    @property
    def is_abort(self) -> bool:
        """``True`` iff callers should post a lifecycle abort event."""
        return self.score > ABORT_THRESHOLD

    def to_dict(self) -> dict[str, object]:
        """Serialise for structured logging and lifecycle payloads."""
        return {
            "score": round(self.score, 4),
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "matched_pattern_ids": list(self.matched_pattern_ids),
            "size_bucket": self.size_bucket.value,
            "url_density": round(self.url_density, 4),
            "command_density": round(self.command_density, 4),
            "text_length": self.text_length,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class PromptwareDetector:
    """Regex + density + Bayesian classifier for cross-agent C2 payloads.

    The detector is stateless and thread-safe. Construct one instance and
    share it across worker threads; classification holds no locks and
    allocates only the result object plus interim Python primitives.
    """

    def __init__(
        self,
        *,
        warn_threshold: float = WARN_THRESHOLD,
        abort_threshold: float = ABORT_THRESHOLD,
    ) -> None:
        if not 0.0 <= warn_threshold <= abort_threshold <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0.0 <= warn <= abort <= 1.0; "
                f"got warn={warn_threshold} abort={abort_threshold}",
            )
        self._warn_threshold = warn_threshold
        self._abort_threshold = abort_threshold

    @property
    def warn_threshold(self) -> float:
        """Inclusive lower bound for the WARN band."""
        return self._warn_threshold

    @property
    def abort_threshold(self) -> float:
        """Inclusive lower bound for the abort band."""
        return self._abort_threshold

    # ------------------------------------------------------------------ classify

    def classify(self, text: str) -> PromptwareScore:
        """Classify a piece of tool-output text as promptware or benign.

        Args:
            text: The tool-output payload as a string. Non-strings should
                be decoded by the caller.

        Returns:
            A :class:`PromptwareScore` with the probability, verdict, and
            a deterministic ordered list of reason strings.
        """
        if not text:
            return PromptwareScore(
                score=0.0,
                verdict=PromptwareVerdict.BENIGN,
                reasons=(),
                matched_pattern_ids=(),
                size_bucket=SizeBucket.TINY,
                url_density=0.0,
                command_density=0.0,
                text_length=0,
            )

        byte_length = len(text.encode("utf-8", errors="replace"))
        bucket = bucket_for_size(byte_length)
        prior = _BAYES_PRIORS[bucket]

        reasons: list[str] = []
        matched_ids: list[str] = []
        log_odds = _logit(prior)

        for pattern in _PATTERNS:
            hits = len(pattern.regex.findall(text))
            if hits == 0:
                continue
            matched_ids.append(pattern.pattern_id)
            reasons.append(pattern.reason)
            # Convert pattern weight to a likelihood ratio. A weight of 0.9
            # implies P(hit|malicious) / P(hit|benign) = 9.0; a weight of
            # 0.5 implies LR=1; below 0.5 the pattern reduces the score.
            likelihood_ratio = pattern.weight / max(1.0 - pattern.weight, 1e-6)
            log_odds += _log(likelihood_ratio)
            # Multiple hits reinforce evidence with diminishing returns.
            if hits > 1:
                log_odds += _log(likelihood_ratio) * (1.0 - 0.5 ** (hits - 1))

        url_density = (len(_URL_RX.findall(text)) * 1000.0) / max(byte_length, 1)
        command_density = (len(_COMMAND_TOKEN_RX.findall(text)) * 1000.0) / max(byte_length, 1)

        if url_density >= 2.0:
            reason = f"high URL density ({url_density:.2f} per 1k bytes)"
            reasons.append(reason)
            matched_ids.append("density.url")
            log_odds += 0.6
        if command_density >= 2.0:
            reason = f"high command-token density ({command_density:.2f} per 1k bytes)"
            reasons.append(reason)
            matched_ids.append("density.command")
            log_odds += 0.6

        score = _sigmoid(log_odds)
        # Clamp to [0, 1] defensively despite the sigmoid range.
        score = max(0.0, min(1.0, score))
        verdict = self._verdict_for(score)

        # Deterministic ordering: reasons are appended in the order that
        # patterns fire, which is the order they appear in ``_PATTERNS``.
        # Dedupe-preserve order so identical reasons don't repeat.
        deduped_reasons = tuple(_dedupe_keep_order(reasons))
        deduped_ids = tuple(_dedupe_keep_order(matched_ids))

        return PromptwareScore(
            score=score,
            verdict=verdict,
            reasons=deduped_reasons,
            matched_pattern_ids=deduped_ids,
            size_bucket=bucket,
            url_density=url_density,
            command_density=command_density,
            text_length=byte_length,
        )

    # ------------------------------------------------------------------ helpers

    def _verdict_for(self, score: float) -> PromptwareVerdict:
        if score > self._abort_threshold:
            return PromptwareVerdict.MALICIOUS
        if score > self._warn_threshold:
            return PromptwareVerdict.SUSPICIOUS
        return PromptwareVerdict.BENIGN


# ---------------------------------------------------------------------------
# Math helpers (kept private so callers cannot accidentally rebind them)
# ---------------------------------------------------------------------------


def _logit(p: float) -> float:
    """Map probability to log-odds, with clamping for stability."""
    eps = 1e-9
    clamped = max(min(p, 1.0 - eps), eps)
    return _log(clamped / (1.0 - clamped))


def _sigmoid(x: float) -> float:
    """Numerically stable logistic sigmoid."""
    from math import exp

    if x >= 0:
        z = exp(-x)
        return 1.0 / (1.0 + z)
    z = exp(x)
    return z / (1.0 + z)


def _log(x: float) -> float:
    """Natural log with a small floor so zero is never passed in."""
    from math import log as math_log

    return math_log(max(x, 1e-12))


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out
