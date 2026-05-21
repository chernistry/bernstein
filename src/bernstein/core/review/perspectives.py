"""Per-adapter perspective assignment + chain coordination for reviews.

This module is the smallest viable slice of the perspective / chain feature
described in issue #1223. It is deliberately narrower than
:mod:`bernstein.core.quality.review_pipeline`:

* No multi-stage DAG, no aggregator strategies, no janitor wiring.
* One linear list of *perspectives*, each pinned to an adapter.
* Two coordination modes: ``parallel`` (independent) and ``sequential``
  (each adapter sees the prior verdicts in its prompt envelope).

YAML schema (``review-perspectives.yaml``):

.. code-block:: yaml

    perspectives:
      - name: security
        adapter: claude
      - name: performance
        adapter: codex
      - name: ux
        adapter: gemini
    chain: sequential   # or "parallel"

Adapter calls are abstracted via :class:`PerspectiveAdapterCall` so unit
tests can substitute fake stubs. A higher-level facade (CLI / janitor)
wires the existing CLI adapters into this protocol; that wiring is out of
scope here and tracked in follow-up tickets.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import starmap
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public enums + errors
# ---------------------------------------------------------------------------


class ChainMode(StrEnum):
    """Coordination mode for a perspective list."""

    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"


class PerspectiveConfigError(ValueError):
    """Raised when perspective YAML is missing, malformed, or invalid."""

    def __init__(self, source: Path | str, detail: str) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = Path(source) if not isinstance(source, Path) else source
        self.detail = detail


# ---------------------------------------------------------------------------
# Schema (Pydantic)
# ---------------------------------------------------------------------------


class PerspectiveSpec(BaseModel):
    """A single perspective assigned to an adapter.

    Attributes:
        name: Free-form perspective tag (``security``, ``performance``,
            ``ux``, ``correctness``, …). Used in audit + as a dimension
            label on the resulting verdict.
        adapter: Adapter identifier (``claude``, ``codex``, ``gemini``,
            …). Resolved by the caller against the existing adapter
            registry; the runner itself only passes it to the supplied
            :class:`PerspectiveAdapterCall`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=64)
    adapter: str = Field(min_length=1, max_length=64)


class PerspectiveConfig(BaseModel):
    """Top-level perspective configuration.

    Attributes:
        perspectives: Ordered list of perspectives. Order matters for
            ``sequential`` chains.
        chain: Coordination mode - ``parallel`` (default) or
            ``sequential``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    perspectives: list[PerspectiveSpec] = Field(min_length=1)
    chain: ChainMode = ChainMode.PARALLEL

    @field_validator("chain", mode="before")
    @classmethod
    def _coerce_chain(cls, value: object) -> object:
        """Accept the YAML strings ``parallel`` / ``sequential``.

        Pydantic's ``strict=True`` mode otherwise rejects the
        ``str`` → ``StrEnum`` coercion and emits an opaque
        ``Input should be an instance of ChainMode`` error.
        """
        if isinstance(value, str):
            try:
                return ChainMode(value)
            except ValueError as exc:
                allowed = ", ".join(m.value for m in ChainMode)
                raise ValueError(f"chain must be one of: {allowed}") from exc
        return value

    @field_validator("perspectives")
    @classmethod
    def _unique_perspective_names(cls, value: list[PerspectiveSpec]) -> list[PerspectiveSpec]:
        seen: set[str] = set()
        for spec in value:
            if spec.name in seen:
                raise ValueError(f"duplicate perspective name {spec.name!r}")
            seen.add(spec.name)
        return value


# ---------------------------------------------------------------------------
# Runtime types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerspectiveVerdict:
    """Verdict returned by one adapter under one perspective.

    Attributes:
        perspective: Name from the originating :class:`PerspectiveSpec`.
        adapter: Adapter identifier from the originating spec.
        content: Adapter's raw textual verdict / findings.
        prior_count: How many prior verdicts were visible to this adapter
            (``0`` for ``parallel`` and for the head of a ``sequential``
            chain). Recorded so the audit chain can replay the envelope.
        chain_position: Zero-based slot of this verdict in the
            declaration order of the originating
            :class:`PerspectiveConfig`. Always populated, including in
            ``parallel`` mode, so audit records keep a stable ordering
            key independent of completion time.
        timestamp: Unix epoch when the verdict was produced. Excluded
            from :meth:`audit_record` so replays remain bit-stable.
    """

    perspective: str
    adapter: str
    content: str
    prior_count: int = 0
    chain_position: int = 0
    timestamp: float = field(default_factory=time.time)

    def audit_record(self, *, chain: ChainMode) -> dict[str, object]:
        """Return a deterministic, JSON-safe audit record for this verdict.

        The returned mapping is intentionally narrow and excludes the
        non-deterministic ``timestamp`` field so that re-running the
        same chain with the same adapter responses yields byte-identical
        audit output (issue #1223 acceptance criterion: "replaying
        yesterday's chain run with the same seeds produces the same
        finding order and the same aggregator verdict").

        Args:
            chain: Coordination mode the verdict was produced under.
                Surfaced as ``coordination`` so downstream tooling can
                grep for ``coordination=sequential`` runs.

        Returns:
            A dict with keys: ``produced_by_adapter``,
            ``produced_with_perspective``, ``chain_position``,
            ``prior_count``, ``coordination``, ``content``.
        """
        return {
            "produced_by_adapter": self.adapter,
            "produced_with_perspective": self.perspective,
            "chain_position": self.chain_position,
            "prior_count": self.prior_count,
            "coordination": chain.value,
            "content": self.content,
        }


# Adapter callable signature. The runner only consumes this protocol; the
# CLI / janitor wiring that maps a name like ``"claude"`` onto a real
# adapter call lives elsewhere and is out of scope for this slice.
PerspectiveAdapterCall = Callable[
    [PerspectiveSpec, str, list[PerspectiveVerdict]],
    Awaitable[str],
]


# ---------------------------------------------------------------------------
# Envelope formatting (sequential chain context)
# ---------------------------------------------------------------------------


def _format_prior_envelope(prior: list[PerspectiveVerdict]) -> str:
    """Render prior verdicts as a markdown context block.

    The runner prepends this block to each adapter's input when running
    ``sequential``. ``parallel`` runs always pass an empty list, so this
    helper returns ``""`` for them.
    """
    if not prior:
        return ""
    lines: list[str] = ["## Prior reviewer verdicts", ""]
    for pv in prior:
        lines.extend((f"### {pv.perspective} (adapter={pv.adapter})", pv.content.strip(), ""))
    return "\n".join(lines).rstrip() + "\n"


def _build_envelope(diff: str, prior: list[PerspectiveVerdict]) -> str:
    """Compose the adapter input: prior verdicts (if any) + diff."""
    prior_block = _format_prior_envelope(prior)
    if not prior_block:
        return diff
    return f"{prior_block}\n## Diff under review\n\n{diff}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_perspectives(
    config: PerspectiveConfig,
    diff: str,
    *,
    adapter_call: PerspectiveAdapterCall,
) -> list[PerspectiveVerdict]:
    """Run *config* against *diff* and return verdicts in spec order.

    Args:
        config: Validated perspective configuration.
        diff: Unified diff (or other review payload) handed to every
            adapter. Truncation is the caller's responsibility.
        adapter_call: Callable that invokes one adapter and returns its
            raw textual verdict. The runner passes the spec, the
            envelope-formatted input, and the list of prior verdicts so
            the callable can decide how to thread context (most
            implementations only need ``input_text``).

    Returns:
        List of :class:`PerspectiveVerdict`, one per spec, in declaration
        order. For ``parallel`` mode each verdict has
        ``prior_count == 0``; for ``sequential`` mode the *i*-th verdict
        was produced with the *i* prior verdicts visible.

    Raises:
        Whatever ``adapter_call`` raises. The runner does not silently
        swallow failures - callers that need fallback behaviour wrap the
        call themselves. (Adapter-fallback-on-failure is deferred per
        issue #1223.)
    """
    if config.chain == ChainMode.SEQUENTIAL:
        return await _run_sequential(config, diff, adapter_call)
    return await _run_parallel(config, diff, adapter_call)


async def _run_sequential(
    config: PerspectiveConfig,
    diff: str,
    adapter_call: PerspectiveAdapterCall,
) -> list[PerspectiveVerdict]:
    """Run perspectives in declared order, threading prior verdicts."""
    out: list[PerspectiveVerdict] = []
    for position, spec in enumerate(config.perspectives):
        envelope = _build_envelope(diff, out)
        started = time.monotonic()
        content = await adapter_call(spec, envelope, out.copy())
        elapsed = time.monotonic() - started
        verdict = PerspectiveVerdict(
            perspective=spec.name,
            adapter=spec.adapter,
            content=content,
            prior_count=len(out),
            chain_position=position,
        )
        out.append(verdict)
        logger.info(
            "perspectives: chain=sequential perspective=%s adapter=%s prior=%d (%.2fs)",
            spec.name,
            spec.adapter,
            verdict.prior_count,
            elapsed,
        )
    return out


async def _run_parallel(
    config: PerspectiveConfig,
    diff: str,
    adapter_call: PerspectiveAdapterCall,
) -> list[PerspectiveVerdict]:
    """Run perspectives concurrently with no cross-context.

    The returned list always follows the declaration order from
    ``config.perspectives``, even if adapters complete out of order.
    ``asyncio.gather`` preserves input order in its result list, so we
    rely on that rather than re-sorting after the fact.
    """

    async def _call(position: int, spec: PerspectiveSpec) -> PerspectiveVerdict:
        started = time.monotonic()
        content = await adapter_call(spec, diff, [])
        elapsed = time.monotonic() - started
        logger.info(
            "perspectives: chain=parallel perspective=%s adapter=%s (%.2fs)",
            spec.name,
            spec.adapter,
            elapsed,
        )
        return PerspectiveVerdict(
            perspective=spec.name,
            adapter=spec.adapter,
            content=content,
            prior_count=0,
            chain_position=position,
        )

    return list(await asyncio.gather(*starmap(_call, enumerate(config.perspectives))))


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_perspectives_yaml(text: str, *, source: Path | str = "<string>") -> PerspectiveConfig:
    """Parse perspective YAML text into a :class:`PerspectiveConfig`.

    Args:
        text: YAML source.
        source: Path used in error messages.

    Returns:
        A validated :class:`PerspectiveConfig`.

    Raises:
        PerspectiveConfigError: On YAML or schema-validation failure.
    """
    try:
        raw_data: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PerspectiveConfigError(source, f"invalid YAML: {exc}") from exc

    if raw_data is None:
        raise PerspectiveConfigError(source, "perspective file is empty")
    if not isinstance(raw_data, dict):
        raise PerspectiveConfigError(
            source,
            f"top-level YAML must be a mapping, got {type(raw_data).__name__}",
        )

    try:
        return PerspectiveConfig.model_validate(raw_data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = first.get("loc", ())
        msg = first.get("msg", "validation error")
        path_str = ".".join(str(p) for p in loc) if loc else "<root>"
        raise PerspectiveConfigError(source, f"{path_str}: {msg}") from exc


def load_perspectives(path: Path | str) -> PerspectiveConfig:
    """Load and validate a perspective config from disk.

    Args:
        path: Filesystem path to the YAML file.

    Returns:
        Validated :class:`PerspectiveConfig`.

    Raises:
        PerspectiveConfigError: If the file is missing, unreadable, or
            invalid.
    """
    p = Path(path)
    if not p.is_file():
        raise PerspectiveConfigError(p, "file not found")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PerspectiveConfigError(p, f"cannot read file: {exc}") from exc
    return load_perspectives_yaml(text, source=p)


# Re-export Any to keep ruff TC002 happy when callers import this module
# without TYPE_CHECKING. The placeholder has no runtime cost.
_ = Any
