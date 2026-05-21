"""Unit tests for the forward-looking synthetic scenario generator.

The contract under test:

1. Generators are **deterministic** under a fixed seed - every public
   entry point must round-trip to the same case ids on repeat invocation.
2. The default registry exposes the six v1 stock scenarios with the
   right severity tags.
3. Trace-parsing is **defensive** - malformed JSONL, oversized files
   and encoding errors never raise, and never produce non-schema cases.
4. Emitted YAML always round-trips through ``yaml.safe_load`` and
   carries the contract keys (``id``, ``scenario``, ``severity``,
   ``prompt``, ``expected_outcome``, ``source``).
5. The ``BERNSTEIN_SYNTHETIC_EVAL_OFF`` env var short-circuits every
   public entry point with no side effects.

Each test owns its tmp_path; no global state is mutated.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from bernstein.eval.scenario_generator import (
    DEFAULT_OUT_DIR,
    DISABLE_ENV,
    GenerationResult,
    ScenarioRegistry,
    SyntheticCase,
    build_default_registry,
    case_to_yaml,
    generate_from_traces,
    is_disabled,
    list_scenarios,
    materialise,
    materialise_and_write,
    parse_param_string,
    write_cases,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STOCK_IDS = (
    "cost_spike",
    "flaky_tests",
    "large_diff",
    "prompt_injection",
    "racing_workers",
    "slow_adapter",
)


def _write_trace(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(r) for r in records)
    path.write_text(payload + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry: stock content + invariants
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_default_registry_has_six_stock_scenarios(self) -> None:
        reg = build_default_registry()
        assert reg.ids() == list(_STOCK_IDS)

    def test_default_registry_is_freshly_built(self) -> None:
        # Two builds yield independent objects so callers cannot leak
        # state across requests.
        a = build_default_registry()
        b = build_default_registry()
        assert a is not b

    def test_register_rejects_duplicate(self) -> None:
        reg = ScenarioRegistry()
        a = build_default_registry().get("large_diff")
        reg.register(a)
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(a)

    def test_register_rejects_bad_id(self) -> None:
        reg = ScenarioRegistry()

        class Bad:
            id = "Bad Id"
            severity = "P2"
            axes: dict[str, tuple[Any, ...]] = {"x": (1,)}

            def materialise(self, params: Any, *, seed: int) -> Any:
                raise NotImplementedError

        with pytest.raises(ValueError, match="invalid scenario id"):
            reg.register(Bad())  # type: ignore[arg-type]

    def test_registry_contains(self) -> None:
        reg = build_default_registry()
        assert "large_diff" in reg
        assert "nonsense" not in reg
        assert 12345 not in reg  # type: ignore[operator]

    def test_get_unknown_raises(self) -> None:
        reg = build_default_registry()
        with pytest.raises(KeyError):
            reg.get("missing")

    def test_items_are_sorted(self) -> None:
        reg = build_default_registry()
        keys = [k for k, _ in reg.items()]
        assert keys == sorted(keys)


class TestStockSeverity:
    """Each stock generator must declare a fixed severity per the ticket."""

    @pytest.mark.parametrize(
        "scenario,severity",
        [
            ("prompt_injection", "P0"),
            ("large_diff", "P1"),
            ("racing_workers", "P1"),
            ("cost_spike", "P1"),
            ("slow_adapter", "P2"),
            ("flaky_tests", "P2"),
        ],
    )
    def test_severity(self, scenario: str, severity: str) -> None:
        reg = build_default_registry()
        assert reg.get(scenario).severity == severity


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_produces_same_ids(self) -> None:
        a = materialise("large_diff", count=3, seed=42)
        b = materialise("large_diff", count=3, seed=42)
        assert [c.id for c in a] == [c.id for c in b]

    def test_same_seed_produces_same_prompts(self) -> None:
        a = materialise("flaky_tests", count=5, seed=7)
        b = materialise("flaky_tests", count=5, seed=7)
        assert [c.prompt for c in a] == [c.prompt for c in b]

    def test_different_seeds_diverge(self) -> None:
        a = materialise("large_diff", count=3, seed=1)
        b = materialise("large_diff", count=3, seed=2)
        # Not every case must differ, but the *set* must differ in at
        # least one position.
        assert [c.id for c in a] != [c.id for c in b]

    def test_zero_count_returns_empty(self) -> None:
        cases = materialise("large_diff", count=0, seed=42)
        assert cases == []

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError):
            materialise("large_diff", count=-1, seed=42)

    def test_unknown_scenario_raises(self) -> None:
        with pytest.raises(KeyError):
            materialise("nonsense", count=1, seed=42)

    def test_negative_seed_rejected(self) -> None:
        # Materialise itself validates seed via the generator.
        with pytest.raises(ValueError):
            build_default_registry().get("large_diff").materialise({}, seed=-1)

    def test_explicit_params_pin_axes(self) -> None:
        cases = materialise(
            "large_diff",
            count=4,
            seed=11,
            params={"size_mb": 10, "ask_lines": 20},
        )
        for c in cases:
            assert c.params["size_mb"] == 10
            assert c.params["ask_lines"] == 20

    def test_id_is_content_addressed(self) -> None:
        cases = materialise("large_diff", count=2, seed=42)
        assert cases[0].id.startswith("syn-")
        assert len(cases[0].id) == len("syn-") + 12

    def test_distinct_seeds_produce_distinct_timestamps(self) -> None:
        a = materialise("large_diff", count=1, seed=1)
        b = materialise("large_diff", count=1, seed=2)
        # We do not assert a strict ordering - only that the
        # deterministic timestamp varies with seed (no time.time()
        # leakage).
        assert a[0].created_at != b[0].created_at

    def test_timestamp_is_stable_across_calls(self) -> None:
        a = materialise("flaky_tests", count=1, seed=99)
        b = materialise("flaky_tests", count=1, seed=99)
        assert a[0].created_at == b[0].created_at


# ---------------------------------------------------------------------------
# Param parsing
# ---------------------------------------------------------------------------


class TestParseParamString:
    def test_empty_string_returns_empty(self) -> None:
        assert parse_param_string("") == {}

    def test_single_int(self) -> None:
        assert parse_param_string("count=3") == {"count": 3}

    def test_float(self) -> None:
        assert parse_param_string("rate=0.25") == {"rate": 0.25}

    def test_bool_true(self) -> None:
        assert parse_param_string("on=true")["on"] is True

    def test_bool_false(self) -> None:
        assert parse_param_string("on=false")["on"] is False

    def test_string(self) -> None:
        assert parse_param_string("stage=planning") == {"stage": "planning"}

    def test_multiple(self) -> None:
        got = parse_param_string("a=1,b=foo,c=0.5")
        assert got == {"a": 1, "b": "foo", "c": 0.5}

    def test_whitespace_tolerated(self) -> None:
        assert parse_param_string(" a = 1 , b = foo ") == {"a": 1, "b": "foo"}

    def test_duplicate_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            parse_param_string("a=1,a=2")

    def test_missing_equals_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing"):
            parse_param_string("noequals")

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty key"):
            parse_param_string("=1")


# ---------------------------------------------------------------------------
# Materialise param validation
# ---------------------------------------------------------------------------


class TestMaterialiseValidation:
    def test_unknown_param_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown parameter"):
            materialise("large_diff", count=1, seed=42, params={"bogus": 1})

    def test_coerce_int_param(self) -> None:
        cases = materialise("large_diff", count=1, seed=42, params={"size_mb": "5"})
        assert cases[0].params["size_mb"] == 5
        assert isinstance(cases[0].params["size_mb"], int)

    def test_coerce_float_param(self) -> None:
        cases = materialise("flaky_tests", count=1, seed=42, params={"flake_rate": "0.4"})
        assert cases[0].params["flake_rate"] == pytest.approx(0.4)
        assert isinstance(cases[0].params["flake_rate"], float)


# ---------------------------------------------------------------------------
# YAML emission
# ---------------------------------------------------------------------------


class TestYamlEmission:
    def test_yaml_round_trips(self) -> None:
        case = materialise("large_diff", count=1, seed=42)[0]
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert loaded["id"] == case.id
        assert loaded["severity"] == case.severity
        assert loaded["scenario"] == "large_diff"
        assert loaded["source"] == "synthetic"

    def test_yaml_contains_required_keys(self) -> None:
        case = materialise("flaky_tests", count=1, seed=42)[0]
        text = case_to_yaml(case)
        for key in ("id:", "scenario:", "severity:", "prompt:", "expected_outcome:", "source:"):
            assert key in text

    def test_yaml_tags_preserved(self) -> None:
        case = materialise("large_diff", count=1, seed=42)[0]
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert "synthetic" in loaded["tags"]

    def test_yaml_id_is_syn_prefixed(self) -> None:
        case = materialise("cost_spike", count=1, seed=42)[0]
        assert case.id.startswith("syn-")

    def test_yaml_empty_tags_handled(self) -> None:
        case = SyntheticCase(
            id="syn-aaaaaaaaaaaa",
            scenario="x",
            severity="P2",
            prompt="hello",
            expected_outcome="ok",
        )
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert loaded["tags"] == []

    def test_yaml_special_chars_in_prompt(self) -> None:
        case = SyntheticCase(
            id="syn-aaaaaaaaaaaa",
            scenario="x",
            severity="P2",
            prompt='line with "quotes": yes',
            expected_outcome="ok",
        )
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert "quotes" in loaded["prompt"]

    def test_yaml_param_types(self) -> None:
        case = SyntheticCase(
            id="syn-aaaaaaaaaaaa",
            scenario="x",
            severity="P2",
            prompt="hello",
            expected_outcome="ok",
            params={"n": 5, "rate": 0.25, "name": "alpha", "enabled": True},
        )
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert loaded["params"]["n"] == 5
        assert loaded["params"]["rate"] == pytest.approx(0.25)
        assert loaded["params"]["enabled"] is True


class TestWriteCases:
    def test_write_emits_yaml_files(self, tmp_path: Path) -> None:
        cases = materialise("large_diff", count=3, seed=42)
        written = write_cases(cases, tmp_path)
        assert len(written) == 3
        for path in written:
            assert path.exists()
            assert path.read_text(encoding="utf-8").startswith("id:")

    def test_write_filenames_use_id(self, tmp_path: Path) -> None:
        cases = materialise("flaky_tests", count=2, seed=42)
        written = write_cases(cases, tmp_path)
        for case, path in zip(cases, written, strict=False):
            assert path.name == f"{case.id}.yaml"

    def test_write_is_idempotent(self, tmp_path: Path) -> None:
        cases = materialise("large_diff", count=2, seed=42)
        write_cases(cases, tmp_path)
        again = write_cases(cases, tmp_path)
        # The second call deduplicates against the existing on-disk
        # corpus.
        assert again == []

    def test_write_creates_out_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "synthetic"
        cases = materialise("large_diff", count=1, seed=42)
        written = write_cases(cases, out)
        assert len(written) == 1
        assert out.is_dir()

    def test_materialise_and_write(self, tmp_path: Path) -> None:
        cases, paths = materialise_and_write("flaky_tests", count=2, seed=42, out_dir=tmp_path)
        assert len(cases) == 2
        assert len(paths) == 2

    def test_write_skips_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, "1")
        cases = materialise("large_diff", count=1, seed=42)
        # materialise also short-circuits - explicitly check write path
        # by constructing one case manually.
        synthetic_case = SyntheticCase(
            id="syn-aaaaaaaaaaaa",
            scenario="x",
            severity="P2",
            prompt="hello",
            expected_outcome="ok",
        )
        _ = cases  # not used
        assert write_cases([synthetic_case], tmp_path) == []


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListScenarios:
    def test_list_six_scenarios(self) -> None:
        rows = list_scenarios()
        assert {r["id"] for r in rows} == set(_STOCK_IDS)

    def test_list_includes_axes(self) -> None:
        rows = list_scenarios()
        for row in rows:
            assert "axes" in row
            assert isinstance(row["axes"], dict)
            assert row["axes"]

    def test_list_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, "1")
        assert list_scenarios() == []


# ---------------------------------------------------------------------------
# Disable switch
# ---------------------------------------------------------------------------


class TestDisableSwitch:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, value)
        assert is_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_falsy_values(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, value)
        assert is_disabled() is False

    def test_unset_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(DISABLE_ENV, raising=False)
        assert is_disabled() is False

    def test_materialise_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, "1")
        assert materialise("large_diff", count=3, seed=42) == []

    def test_generate_from_traces_skips(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DISABLE_ENV, "1")
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert result.skipped_disabled is True
        assert result.created == []


# ---------------------------------------------------------------------------
# Trace ingestion
# ---------------------------------------------------------------------------


class TestTraceIngestion:
    def test_no_traces_emits_default_corpus(self, tmp_path: Path) -> None:
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert len(result.created) >= 1
        assert all(c.scenario == "prompt_injection" for c in result.created)

    def test_detects_large_diff_tag(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(
            traces / "0001.jsonl",
            [{"tag": "large_diff", "task_id": "T-1"}, {"event": "noise"}],
        )
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert any(c.scenario == "large_diff" for c in result.created)

    def test_detects_multiple_scenarios(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(
            traces / "0001.jsonl",
            [{"tag": "flaky"}, {"tag": "race_condition"}, {"tag": "cost_spike"}],
        )
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        scenarios = {c.scenario for c in result.created}
        assert {"flaky_tests", "racing_workers", "cost_spike"} <= scenarios

    def test_malformed_jsonl_lines_skipped(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        traces.mkdir(parents=True)
        (traces / "broken.jsonl").write_text(
            'not json\n{"tag": "large_diff"}\n{also bad\n',
            encoding="utf-8",
        )
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        # large_diff should still surface despite surrounding garbage.
        assert any(c.scenario == "large_diff" for c in result.created)

    def test_empty_trace_file_counted_as_invalid(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        traces.mkdir(parents=True)
        (traces / "empty.jsonl").write_text("", encoding="utf-8")
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert result.skipped_invalid_traces >= 1

    def test_oversize_trace_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch the module constant to a tiny limit so the test stays fast.
        import bernstein.eval.scenario_generator as sg

        monkeypatch.setattr(sg, "_MAX_TRACE_BYTES", 16)
        traces = tmp_path / ".sdd" / "traces"
        traces.mkdir(parents=True)
        (traces / "big.jsonl").write_text(
            '{"tag": "large_diff", "padding": "AAAAAAAAAAAAAAAAAAAAAA"}\n',
            encoding="utf-8",
        )
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        # No detection - file was skipped - fallback corpus only.
        assert all(c.scenario == "prompt_injection" for c in result.created)

    def test_from_traces_zero_only_uses_fallback(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tag": "large_diff"}])
        result = generate_from_traces(workdir=tmp_path, from_traces=0, seed=42)
        # No traces inspected; only fallback corpus emitted.
        assert all(c.scenario == "prompt_injection" for c in result.created)

    def test_negative_from_traces_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            generate_from_traces(workdir=tmp_path, from_traces=-1, seed=42)

    def test_idempotent_under_same_seed(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tag": "large_diff"}])
        r1 = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        r2 = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        # First pass creates, second pass dedupes against on-disk corpus.
        assert len(r1.created) >= 1
        assert r2.created == []
        assert r2.skipped_duplicates >= len(r1.created)

    def test_only_latest_n_traces_scanned(self, tmp_path: Path) -> None:
        import time as _time

        traces = tmp_path / ".sdd" / "traces"
        traces.mkdir(parents=True)
        # Older trace: cost_spike. Newer trace: large_diff.
        _write_trace(traces / "old.jsonl", [{"tag": "cost_spike"}])
        _time.sleep(0.02)
        _write_trace(traces / "new.jsonl", [{"tag": "large_diff"}])
        result = generate_from_traces(workdir=tmp_path, from_traces=1, seed=42)
        scenarios = {c.scenario for c in result.created}
        assert "large_diff" in scenarios
        assert "cost_spike" not in scenarios

    def test_encoding_error_is_handled(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        traces.mkdir(parents=True)
        # Invalid UTF-8 bytes.
        (traces / "bin.jsonl").write_bytes(b"\xff\xfe\xfd not utf-8\n")
        # Must not raise; the file is counted as invalid.
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert result.skipped_invalid_traces >= 1

    def test_emitted_yaml_files_validate(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tag": "large_diff"}, {"tag": "flaky"}])
        out_dir = tmp_path / "out"
        result = generate_from_traces(
            workdir=tmp_path,
            traces_dir=traces,
            out_dir=out_dir,
            from_traces=5,
            seed=42,
        )
        assert result.created
        for path in out_dir.glob("syn-*.yaml"):
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert loaded["source"] == "synthetic"
            assert loaded["id"].startswith("syn-")

    def test_default_out_dir_relative_to_workdir(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tag": "large_diff"}])
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        expected_root = tmp_path.joinpath(*DEFAULT_OUT_DIR)
        assert expected_root.is_dir()
        assert {p.name for p in expected_root.glob("syn-*.yaml")} == {f"{c.id}.yaml" for c in result.created}

    def test_records_without_known_tag_use_fallback(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tag": "unknown"}, {"event": "noise"}])
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        # Fallback corpus emits prompt_injection only.
        assert {c.scenario for c in result.created} == {"prompt_injection"}

    def test_tag_list_field_supported(self, tmp_path: Path) -> None:
        traces = tmp_path / ".sdd" / "traces"
        _write_trace(traces / "0001.jsonl", [{"tags": ["cost_spike", "irrelevant"]}])
        result = generate_from_traces(workdir=tmp_path, from_traces=5, seed=42)
        assert any(c.scenario == "cost_spike" for c in result.created)


# ---------------------------------------------------------------------------
# Synthetic case dataclass invariants
# ---------------------------------------------------------------------------


class TestSyntheticCaseDataclass:
    def test_frozen(self) -> None:
        case = materialise("large_diff", count=1, seed=42)[0]
        with pytest.raises((AttributeError, TypeError)):
            case.id = "syn-zzzzzzzzzzzz"  # type: ignore[misc]

    def test_to_dict_round_trips(self) -> None:
        case = materialise("flaky_tests", count=1, seed=42)[0]
        d = case.to_dict()
        assert d["id"] == case.id
        assert d["scenario"] == "flaky_tests"
        assert d["source"] == "synthetic"
        assert isinstance(d["tags"], list)

    def test_source_is_always_synthetic(self) -> None:
        for sid in _STOCK_IDS:
            cases = materialise(sid, count=1, seed=42)
            assert all(c.source == "synthetic" for c in cases)


# ---------------------------------------------------------------------------
# Generation result
# ---------------------------------------------------------------------------


class TestGenerationResult:
    def test_defaults(self) -> None:
        result = GenerationResult()
        assert result.created == []
        assert result.skipped_duplicates == 0
        assert result.skipped_disabled is False
        assert result.skipped_invalid_traces == 0

    def test_traces_dir_override(self, tmp_path: Path) -> None:
        # Custom traces dir that doesn't sit under workdir.
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        _write_trace(outside / "0001.jsonl", [{"tag": "large_diff"}])
        result = generate_from_traces(
            workdir=tmp_path,
            traces_dir=outside,
            from_traces=5,
            seed=42,
        )
        assert any(c.scenario == "large_diff" for c in result.created)


# ---------------------------------------------------------------------------
# Malformed YAML rejection
# ---------------------------------------------------------------------------


class TestMalformedYamlRejection:
    def test_round_trip_rejects_top_level_list(self) -> None:
        import bernstein.eval.scenario_generator as sg

        with pytest.raises(sg._MalformedYAMLError):
            sg._round_trip_yaml("- 1\n- 2\n")

    def test_round_trip_rejects_missing_keys(self) -> None:
        import bernstein.eval.scenario_generator as sg

        with pytest.raises(sg._MalformedYAMLError):
            sg._round_trip_yaml("id: syn-x\nseverity: P0\n")

    def test_round_trip_rejects_broken_yaml(self) -> None:
        import bernstein.eval.scenario_generator as sg

        with pytest.raises(sg._MalformedYAMLError):
            sg._round_trip_yaml(":\n: bad\n   : :\n")

    def test_round_trip_accepts_valid(self) -> None:
        import bernstein.eval.scenario_generator as sg

        case = materialise("large_diff", count=1, seed=42)[0]
        loaded = sg._round_trip_yaml(case_to_yaml(case))
        assert loaded["id"] == case.id


# ---------------------------------------------------------------------------
# Snapshot (golden-file style)
# ---------------------------------------------------------------------------


def test_snapshot_yaml_structure(snapshot) -> None:
    """Snapshot the YAML emitted for a fixed (scenario, seed) pair.

    Drift here means the YAML wire-format changed in a way external
    consumers can see. Update with ``pytest --snapshot-update`` when
    the change is intentional.
    """
    case = materialise("large_diff", count=1, seed=42)[0]
    text = case_to_yaml(case)
    # Normalise the timestamp line so the snapshot is byte-stable.
    lines = ["created_at: <stable>" if line.startswith("created_at:") else line for line in text.splitlines()]
    normalised = "\n".join(lines) + "\n"
    assert normalised == snapshot


def test_snapshot_prompt_injection_yaml(snapshot) -> None:
    case = materialise("prompt_injection", count=1, seed=7)[0]
    text = case_to_yaml(case)
    lines = ["created_at: <stable>" if line.startswith("created_at:") else line for line in text.splitlines()]
    normalised = "\n".join(lines) + "\n"
    assert normalised == snapshot


def test_snapshot_list_scenarios_structure(snapshot) -> None:
    rows = list_scenarios()
    # Reduce to id + severity + axes keys so any future tweak to axis
    # tuples updates the snapshot.
    summary = [{"id": r["id"], "severity": r["severity"], "axes": sorted(r["axes"].keys())} for r in rows]
    assert summary == snapshot


# ---------------------------------------------------------------------------
# Misc: prompt size cap + safe-format edge cases
# ---------------------------------------------------------------------------


class TestPromptCap:
    def test_long_prompt_truncated(self) -> None:
        import bernstein.eval.scenario_generator as sg

        reg = ScenarioRegistry()
        long_template = "X" * (sg._MAX_PROMPT_LEN + 100)
        gen = sg._BaseGenerator(
            id="long_one",
            severity="P2",
            axes={"x": (1,)},
            template=long_template,
            outcome="ok",
            tags=("synthetic",),
        )
        reg.register(gen)
        cases = materialise("long_one", count=1, seed=42, registry=reg)
        assert len(cases[0].prompt) == sg._MAX_PROMPT_LEN + 3  # "..." suffix


class TestSafeFormat:
    def test_missing_placeholder_becomes_question_mark(self) -> None:
        from bernstein.eval.scenario_generator import _safe_format

        out = _safe_format("a={x} b={y}", {"x": 1})
        assert out == "a=1 b=?"

    def test_double_brace_escapes(self) -> None:
        from bernstein.eval.scenario_generator import _safe_format

        out = _safe_format("literal {{x}} value", {"x": 9})
        assert out == "literal {x} value"

    def test_unclosed_brace_kept_literal(self) -> None:
        from bernstein.eval.scenario_generator import _safe_format

        out = _safe_format("oops {x", {"x": 9})
        assert "{" in out


# ---------------------------------------------------------------------------
# DEFAULT_OUT_DIR / module constants
# ---------------------------------------------------------------------------


def test_default_out_dir_constant() -> None:
    assert DEFAULT_OUT_DIR == ("eval", "golden_data", "synthetic")


def test_module_yaml_emission_is_self_consistent() -> None:
    case = materialise("racing_workers", count=1, seed=99)[0]
    body = case_to_yaml(case)
    parsed = yaml.safe_load(body)
    re_emitted = case_to_yaml(
        SyntheticCase(
            id=parsed["id"],
            scenario=parsed["scenario"],
            severity=parsed["severity"],
            prompt=parsed["prompt"],
            expected_outcome=parsed["expected_outcome"],
            params=parsed["params"],
            tags=tuple(parsed["tags"]),
            source=parsed["source"],
            seed=parsed["seed"],
            created_at=parsed["created_at"],
        )
    )
    # Re-emission is identical except for trailing whitespace handling.
    assert re_emitted == body


def test_no_external_llm_invocation() -> None:
    # The module is deterministic and offline. We sanity-check by
    # asserting no module-level callable named like an LLM client is
    # present.
    import bernstein.eval.scenario_generator as sg

    blocked = {"anthropic", "openai", "httpx", "requests"}
    suspect = [name for name in dir(sg) if name.lower() in blocked]
    assert suspect == []


# ---------------------------------------------------------------------------
# Cross-scenario sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sid", _STOCK_IDS)
def test_each_stock_scenario_round_trips_to_valid_yaml(sid: str) -> None:
    cases = materialise(sid, count=2, seed=13)
    assert len(cases) == 2
    for case in cases:
        text = case_to_yaml(case)
        loaded = yaml.safe_load(text)
        assert loaded["scenario"] == sid
        assert loaded["source"] == "synthetic"
        assert isinstance(loaded["prompt"], str)
        assert loaded["prompt"].strip()


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 99, 1234])
def test_materialise_repeats_for_random_seeds(seed: int) -> None:
    a = materialise("large_diff", count=3, seed=seed)
    b = materialise("large_diff", count=3, seed=seed)
    assert [c.id for c in a] == [c.id for c in b]


# ---------------------------------------------------------------------------
# Output text contains the chosen axis value
# ---------------------------------------------------------------------------


def test_prompt_reflects_chosen_param() -> None:
    cases = materialise("large_diff", count=1, seed=42, params={"size_mb": 10, "ask_lines": 20})
    assert "10 MB" in cases[0].prompt
    assert "20-line" in cases[0].prompt


def test_prompt_reflects_string_param() -> None:
    cases = materialise(
        "slow_adapter",
        count=1,
        seed=42,
        params={"endpoint": "adapter:codex", "latency_ms": 5000},
    )
    assert "adapter:codex" in cases[0].prompt
    assert "5000" in cases[0].prompt


# ---------------------------------------------------------------------------
# Bulk count sanity
# ---------------------------------------------------------------------------


def test_large_count_emits_unique_cases() -> None:
    cases = materialise("flaky_tests", count=25, seed=42)
    assert len(cases) == 25
    # The id should change at least often enough that we get >= 5 unique
    # ids in 25 draws.
    assert len({c.id for c in cases}) >= 5


def test_documented_example_from_issue() -> None:
    cases = materialise("large_diff", count=3, seed=42)
    assert len(cases) == 3
    ids = [c.id for c in cases]
    # Stable content-hash filenames per the acceptance criteria.
    assert all(re_match_syn(i) for i in ids)


def re_match_syn(s: str) -> bool:
    """Quick filter - keep as a plain function for stable test imports."""
    import re

    return bool(re.match(r"^syn-[0-9a-f]{12}$", s))


def test_textwrap_indent_not_breaking_prompt() -> None:
    # Multi-line prompts must survive YAML block scalar serialisation.
    case = SyntheticCase(
        id="syn-aaaaaaaaaaaa",
        scenario="x",
        severity="P2",
        prompt=textwrap.dedent(
            """
            line one
            line two
            line three
            """
        ).strip(),
        expected_outcome="ok",
    )
    text = case_to_yaml(case)
    loaded = yaml.safe_load(text)
    assert "line one" in loaded["prompt"]
    assert "line three" in loaded["prompt"]
