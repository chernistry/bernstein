"""Tests for the perspective + chain runner in :mod:`bernstein.core.review`.

Covers the smallest viable slice of issue #1223:

* YAML schema validates and rejects malformed input.
* Sequential chain of three fake adapters produces three verdicts in
  declared order, and each adapter sees the prior verdicts in the
  envelope it received.
* Parallel mode runs without leaking prior context between adapters.
"""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from bernstein.core.review import (
    ChainMode,
    PerspectiveAdapterCall,
    PerspectiveConfig,
    PerspectiveConfigError,
    PerspectiveSpec,
    PerspectiveVerdict,
    load_perspectives_yaml,
    run_perspectives,
)

# ---------------------------------------------------------------------------
# YAML schema
# ---------------------------------------------------------------------------


_GOOD = textwrap.dedent(
    """
    perspectives:
      - name: security
        adapter: claude
      - name: performance
        adapter: codex
      - name: ux
        adapter: gemini
    chain: sequential
    """
).strip()


class TestPerspectiveSchema:
    def test_parses_good_yaml(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        assert cfg.chain == ChainMode.SEQUENTIAL
        assert [p.name for p in cfg.perspectives] == [
            "security",
            "performance",
            "ux",
        ]
        assert [p.adapter for p in cfg.perspectives] == [
            "claude",
            "codex",
            "gemini",
        ]

    def test_chain_defaults_to_parallel(self) -> None:
        text = textwrap.dedent(
            """
            perspectives:
              - name: security
                adapter: claude
            """
        ).strip()
        cfg = load_perspectives_yaml(text)
        assert cfg.chain == ChainMode.PARALLEL

    def test_rejects_empty_file(self) -> None:
        with pytest.raises(PerspectiveConfigError, match="empty"):
            load_perspectives_yaml("")

    def test_rejects_top_level_list(self) -> None:
        with pytest.raises(PerspectiveConfigError, match="must be a mapping"):
            load_perspectives_yaml("- foo\n- bar\n")

    def test_rejects_unknown_chain_value(self) -> None:
        text = textwrap.dedent(
            """
            perspectives:
              - name: security
                adapter: claude
            chain: nonsense
            """
        ).strip()
        with pytest.raises(PerspectiveConfigError, match="chain"):
            load_perspectives_yaml(text)

    def test_rejects_duplicate_perspective_names(self) -> None:
        text = textwrap.dedent(
            """
            perspectives:
              - name: security
                adapter: claude
              - name: security
                adapter: codex
            """
        ).strip()
        with pytest.raises(PerspectiveConfigError, match="duplicate perspective name"):
            load_perspectives_yaml(text)

    def test_rejects_extra_fields(self) -> None:
        text = textwrap.dedent(
            """
            perspectives:
              - name: security
                adapter: claude
                weight: 0.5
            """
        ).strip()
        with pytest.raises(PerspectiveConfigError):
            load_perspectives_yaml(text)


# ---------------------------------------------------------------------------
# Runner - fake adapter stubs
# ---------------------------------------------------------------------------


def _fake_adapter_call(
    seen_envelopes: dict[str, str],
    seen_priors: dict[str, list[PerspectiveVerdict]],
) -> PerspectiveAdapterCall:
    """Build a fake adapter callable that records what it received.

    Each adapter returns a deterministic verdict string keyed off the
    perspective name so order can be asserted.
    """

    async def _call(
        spec: PerspectiveSpec,
        input_text: str,
        prior: list[PerspectiveVerdict],
    ) -> str:
        seen_envelopes[spec.name] = input_text
        seen_priors[spec.name] = prior.copy()
        return f"verdict[{spec.name}/{spec.adapter}]"

    return _call


class TestSequentialChain:
    def test_chain_of_three_produces_three_verdicts_in_order(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        seen_envelopes: dict[str, str] = {}
        seen_priors: dict[str, list[PerspectiveVerdict]] = {}
        adapter = _fake_adapter_call(seen_envelopes, seen_priors)

        verdicts = asyncio.run(run_perspectives(cfg, "+ added\n- removed\n", adapter_call=adapter))

        assert len(verdicts) == 3
        assert [v.perspective for v in verdicts] == [
            "security",
            "performance",
            "ux",
        ]
        assert [v.adapter for v in verdicts] == ["claude", "codex", "gemini"]
        assert [v.prior_count for v in verdicts] == [0, 1, 2]
        assert [v.content for v in verdicts] == [
            "verdict[security/claude]",
            "verdict[performance/codex]",
            "verdict[ux/gemini]",
        ]

    def test_each_adapter_saw_prior_verdicts_in_its_envelope(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        seen_envelopes: dict[str, str] = {}
        seen_priors: dict[str, list[PerspectiveVerdict]] = {}
        adapter = _fake_adapter_call(seen_envelopes, seen_priors)

        asyncio.run(run_perspectives(cfg, "+ diff line", adapter_call=adapter))

        # Head of chain has no prior context - diff is passed verbatim.
        assert "Prior reviewer verdicts" not in seen_envelopes["security"]
        assert "+ diff line" in seen_envelopes["security"]
        assert seen_priors["security"] == []

        # Second adapter sees one prior verdict (security).
        env_perf = seen_envelopes["performance"]
        assert "Prior reviewer verdicts" in env_perf
        assert "verdict[security/claude]" in env_perf
        assert "verdict[performance/" not in env_perf
        assert [pv.perspective for pv in seen_priors["performance"]] == [
            "security",
        ]

        # Third adapter sees both prior verdicts in declared order.
        env_ux = seen_envelopes["ux"]
        assert "verdict[security/claude]" in env_ux
        assert "verdict[performance/codex]" in env_ux
        # Prior block precedes the diff in the envelope.
        assert env_ux.index("verdict[security/claude]") < env_ux.index("## Diff under review")
        assert [pv.perspective for pv in seen_priors["ux"]] == [
            "security",
            "performance",
        ]


class TestParallelMode:
    def test_parallel_does_not_thread_prior_context(self) -> None:
        cfg = PerspectiveConfig(
            perspectives=[
                PerspectiveSpec(name="security", adapter="claude"),
                PerspectiveSpec(name="performance", adapter="codex"),
            ],
            chain=ChainMode.PARALLEL,
        )
        seen_envelopes: dict[str, str] = {}
        seen_priors: dict[str, list[PerspectiveVerdict]] = {}
        adapter = _fake_adapter_call(seen_envelopes, seen_priors)

        verdicts = asyncio.run(run_perspectives(cfg, "+ p", adapter_call=adapter))

        assert {v.perspective for v in verdicts} == {"security", "performance"}
        assert all(v.prior_count == 0 for v in verdicts)
        for env in seen_envelopes.values():
            assert "Prior reviewer verdicts" not in env
        assert seen_priors["security"] == []
        assert seen_priors["performance"] == []


class TestAdapterFailure:
    def test_runner_propagates_adapter_exceptions(self) -> None:
        cfg = PerspectiveConfig(
            perspectives=[PerspectiveSpec(name="security", adapter="claude")],
            chain=ChainMode.SEQUENTIAL,
        )

        async def boom(
            _spec: PerspectiveSpec,
            _input: str,
            _prior: list[PerspectiveVerdict],
        ) -> str:
            raise RuntimeError("adapter offline")

        with pytest.raises(RuntimeError, match="adapter offline"):
            asyncio.run(run_perspectives(cfg, "diff", adapter_call=boom))


# ---------------------------------------------------------------------------
# Chain position + declaration-order preservation
# ---------------------------------------------------------------------------


class TestChainPosition:
    def test_sequential_assigns_dense_chain_positions(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        adapter = _fake_adapter_call({}, {})

        verdicts = asyncio.run(run_perspectives(cfg, "diff", adapter_call=adapter))

        assert [v.chain_position for v in verdicts] == [0, 1, 2]

    def test_parallel_preserves_declaration_order_even_if_adapters_complete_out_of_order(
        self,
    ) -> None:
        """Slowest adapter is declared first; gather must still return it first."""
        cfg = PerspectiveConfig(
            perspectives=[
                PerspectiveSpec(name="security", adapter="claude"),
                PerspectiveSpec(name="performance", adapter="codex"),
                PerspectiveSpec(name="ux", adapter="gemini"),
            ],
            chain=ChainMode.PARALLEL,
        )

        # First adapter sleeps longest, last sleeps least → reverse
        # completion order under PARALLEL. Result list must still match
        # declaration order.
        delays = {"security": 0.03, "performance": 0.02, "ux": 0.01}

        async def slow_adapter(
            spec: PerspectiveSpec,
            _input: str,
            _prior: list[PerspectiveVerdict],
        ) -> str:
            await asyncio.sleep(delays[spec.name])
            return f"verdict[{spec.name}]"

        verdicts = asyncio.run(run_perspectives(cfg, "diff", adapter_call=slow_adapter))

        assert [v.perspective for v in verdicts] == [
            "security",
            "performance",
            "ux",
        ]
        assert [v.chain_position for v in verdicts] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Audit records - replay determinism
# ---------------------------------------------------------------------------


class TestAuditRecords:
    def test_audit_record_is_timestamp_free_and_replay_stable(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        adapter = _fake_adapter_call({}, {})

        first = asyncio.run(run_perspectives(cfg, "diff", adapter_call=adapter))
        second = asyncio.run(run_perspectives(cfg, "diff", adapter_call=adapter))

        first_records = [v.audit_record(chain=cfg.chain) for v in first]
        second_records = [v.audit_record(chain=cfg.chain) for v in second]

        # Two independent runs against the same fake adapter must produce
        # byte-identical audit records - that is the issue's
        # replay-determinism acceptance criterion.
        assert first_records == second_records
        # And the dict must NOT carry the wall-clock timestamp.
        for record in first_records:
            assert "timestamp" not in record

    def test_audit_record_shape(self) -> None:
        cfg = load_perspectives_yaml(_GOOD)
        adapter = _fake_adapter_call({}, {})

        verdicts = asyncio.run(run_perspectives(cfg, "diff", adapter_call=adapter))

        record = verdicts[1].audit_record(chain=cfg.chain)
        assert record == {
            "produced_by_adapter": "codex",
            "produced_with_perspective": "performance",
            "chain_position": 1,
            "prior_count": 1,
            "coordination": "sequential",
            "content": "verdict[performance/codex]",
        }

    def test_audit_record_parallel_marks_coordination(self) -> None:
        cfg = PerspectiveConfig(
            perspectives=[
                PerspectiveSpec(name="security", adapter="claude"),
                PerspectiveSpec(name="performance", adapter="codex"),
            ],
            chain=ChainMode.PARALLEL,
        )
        adapter = _fake_adapter_call({}, {})

        verdicts = asyncio.run(run_perspectives(cfg, "diff", adapter_call=adapter))
        records = [v.audit_record(chain=cfg.chain) for v in verdicts]

        assert all(r["coordination"] == "parallel" for r in records)
        assert all(r["prior_count"] == 0 for r in records)
        assert [r["chain_position"] for r in records] == [0, 1]
