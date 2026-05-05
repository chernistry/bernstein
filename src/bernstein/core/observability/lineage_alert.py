"""Tamper-loud alert sinks for the lineage chain.

When the janitor runs lineage compaction it also re-verifies the WAL
hash chain and the customer signature on every record. Detecting a
break is treated as a security event, not a bookkeeping warning -- the
operator's SIEM is the response surface, not Bernstein's logs. This
module defines the protocol every sink implements and ships a default
``WebhookAlertSink`` for vanilla HTTP collectors (Splunk HEC, generic
SIEM webhook, syslog-over-HTTP).

Design notes
------------
* The sink protocol is intentionally narrow: a single ``emit(event)``
  call. Concrete sinks decide their transport.
* The default webhook sink retries 5xx responses with exponential
  backoff so a momentarily flaky SIEM does not lose a tamper alert.
  Network errors and 5xx after the retry budget are swallowed -- the
  janitor is fail-loud-but-non-blocking on a broken sink.
* The alert payload is a dataclass, not a free-form dict, so a
  reviewer auditing what the sink can leak sees the full surface.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_TIMEOUT_SECS: float = 5.0
DEFAULT_WEBHOOK_MAX_RETRIES: int = 3
DEFAULT_WEBHOOK_BACKOFF_SECS: float = 0.5


@dataclass(frozen=True)
class LineageTamperEvent:
    """Single tamper-detected event handed to a sink.

    ``run_id`` is the lineage run whose chain failed to verify;
    ``errors`` is the list of human-readable diagnostics surfaced by
    the verifier; ``record_count`` is how many records were inspected
    before the first failure (or in total when the whole chain was
    walked). ``detected_at`` is the wall-clock timestamp the janitor
    raised the event.
    """

    run_id: str
    errors: list[str]
    record_count: int
    detected_at: float
    source: str = "janitor"
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


@runtime_checkable
class LineageAlertSink(Protocol):
    """A pluggable destination for ``LineageTamperEvent`` notifications."""

    def emit(self, event: LineageTamperEvent) -> bool:
        """Deliver *event*. Return ``True`` on success, ``False`` on failure."""
        ...


class WebhookAlertSink:
    """HTTP webhook sink with bounded retries on 5xx.

    The sink fails closed -- on a permanent 4xx, transport error, or
    exhausted retries it logs the failure and returns ``False`` rather
    than raising, so a broken SIEM endpoint cannot crash the janitor.
    """

    __slots__ = ("_backoff_secs", "_headers", "_max_retries", "_timeout_secs", "url")

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_secs: float = DEFAULT_WEBHOOK_TIMEOUT_SECS,
        max_retries: int = DEFAULT_WEBHOOK_MAX_RETRIES,
        backoff_secs: float = DEFAULT_WEBHOOK_BACKOFF_SECS,
    ) -> None:
        self.url = url
        self._headers = dict(headers or {})
        self._headers.setdefault("Content-Type", "application/json")
        self._timeout_secs = timeout_secs
        self._max_retries = max(0, max_retries)
        self._backoff_secs = max(0.0, backoff_secs)

    def emit(self, event: LineageTamperEvent) -> bool:
        body = json.dumps(_event_to_dict(event), sort_keys=True).encode("utf-8")
        attempt = 0
        while True:
            try:
                req = urllib.request.Request(
                    self.url,
                    data=body,
                    headers=self._headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout_secs) as resp:
                    status = getattr(resp, "status", 200)
                    if 200 <= status < 300:
                        return True
                    logger.warning("lineage alert: webhook returned status %s", status)
                    return False
            except urllib.error.HTTPError as exc:
                if exc.code >= 500 and attempt < self._max_retries:
                    self._sleep(attempt)
                    attempt += 1
                    continue
                logger.warning("lineage alert: webhook HTTPError %s for %s", exc.code, self.url)
                return False
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self._max_retries:
                    self._sleep(attempt)
                    attempt += 1
                    continue
                logger.warning("lineage alert: webhook transport error: %s", exc)
                return False

    def _sleep(self, attempt: int) -> None:
        time.sleep(self._backoff_secs * (2**attempt))


class NullAlertSink:
    """Sink that drops every event. Used when no SIEM is configured."""

    def emit(self, event: LineageTamperEvent) -> bool:
        return True


def _event_to_dict(event: LineageTamperEvent) -> dict[str, Any]:
    return {
        "type": "lineage_tamper_detected",
        "run_id": event.run_id,
        "errors": list(event.errors),
        "record_count": event.record_count,
        "detected_at": event.detected_at,
        "source": event.source,
        "extra": dict(event.extra),
    }


def sink_from_config(
    *,
    enabled: bool,
    webhook_url: str | None,
    headers: dict[str, str] | None = None,
    timeout_secs: float = DEFAULT_WEBHOOK_TIMEOUT_SECS,
    max_retries: int = DEFAULT_WEBHOOK_MAX_RETRIES,
) -> LineageAlertSink:
    """Build a sink from bernstein.yaml-shaped config.

    Returns ``NullAlertSink`` when alerting is disabled or the
    webhook URL is missing. The orchestrator never raises here -- a
    misconfigured SIEM is loud-by-counter-and-audit, not a crash.
    """
    if not enabled or not webhook_url:
        return NullAlertSink()
    return WebhookAlertSink(
        webhook_url,
        headers=headers,
        timeout_secs=timeout_secs,
        max_retries=max_retries,
    )
