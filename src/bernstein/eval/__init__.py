"""Evaluation harness for measuring orchestration quality.

Provides multiplicative scoring, LLM-based code quality judging,
failure taxonomy, and golden benchmark task management.
"""

from __future__ import annotations

from bernstein.eval.baseline import EvalBaseline, load_baseline, save_baseline
from bernstein.eval.calibration import (
    BrierScore,
    CalibrationLogError,
    CalibrationRecord,
    CalibrationReport,
    ReliabilityBucket,
    compute_brier,
    compute_report,
    expected_calibration_error,
    load_log,
    log_decision,
    parse_duration,
    reliability_diagram_data,
)
from bernstein.eval.harness import EvalHarness, EvalResult, EvalTier
from bernstein.eval.incident_synthesizer import (
    IncidentEvalCase,
    IncidentSyncResult,
    IncidentSynthesizer,
    run_incident_eval_gate,
)
from bernstein.eval.scenario_generator import (
    GenerationResult,
    ScenarioGenerator,
    ScenarioRegistry,
    SyntheticCase,
    build_default_registry,
    generate_from_traces,
    is_disabled,
    list_scenarios,
    materialise,
)

__all__ = [
    "BrierScore",
    "CalibrationLogError",
    "CalibrationRecord",
    "CalibrationReport",
    "EvalBaseline",
    "EvalHarness",
    "EvalResult",
    "EvalTier",
    "GenerationResult",
    "IncidentEvalCase",
    "IncidentSyncResult",
    "IncidentSynthesizer",
    "ReliabilityBucket",
    "ScenarioGenerator",
    "ScenarioRegistry",
    "SyntheticCase",
    "build_default_registry",
    "compute_brier",
    "compute_report",
    "expected_calibration_error",
    "generate_from_traces",
    "is_disabled",
    "list_scenarios",
    "load_baseline",
    "load_log",
    "log_decision",
    "materialise",
    "parse_duration",
    "reliability_diagram_data",
    "run_incident_eval_gate",
    "save_baseline",
]
