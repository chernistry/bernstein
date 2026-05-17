"""Digital-twin orchestration simulator (issue #1374).

This subpackage provides a pure, side-effect-free simulator that runs a
Bernstein plan against historical traces and mock LLM stubs to answer
"what-if" questions before any real tokens are spent.

Public surface:

* :func:`simulate` - top-level entry point: ``simulate(plan, options)``
  returns a :class:`SimulationReport`.
* :class:`SimulationReport` - structured result with per-task predictions
  and aggregate bands (cost, abandonment, blast-radius, bottlenecks).
* :class:`SimulationOptions` - knobs (seed, budget cap, history depth).
* :class:`TaskPrediction` - per-task forecast: cost band, abandon prob,
  blast-radius score, latency estimate.

The simulator is read-only: it consults ``.sdd/traces/`` and
``.sdd/metrics/`` for calibration, never writes outside the operator-
supplied ``--out`` path, and never spawns a real agent.
"""

from __future__ import annotations

from bernstein.core.simulate.predictor import (
    DEFAULT_HISTORY_LIMIT,
    AbandonmentPredictor,
    BlastRadiusPredictor,
    CostPredictor,
    HistoricalTraces,
    LatencyPredictor,
    load_traces,
)
from bernstein.core.simulate.report import (
    AggregateBands,
    Bottleneck,
    CriterionProfileBias,
    DecisionEdge,
    SimulationOptions,
    SimulationReport,
    TaskPrediction,
    render_json,
    render_markdown,
)
from bernstein.core.simulate.runner import SimulationError, simulate

__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "AbandonmentPredictor",
    "AggregateBands",
    "BlastRadiusPredictor",
    "Bottleneck",
    "CostPredictor",
    "CriterionProfileBias",
    "DecisionEdge",
    "HistoricalTraces",
    "LatencyPredictor",
    "SimulationError",
    "SimulationOptions",
    "SimulationReport",
    "TaskPrediction",
    "load_traces",
    "render_json",
    "render_markdown",
    "simulate",
]
