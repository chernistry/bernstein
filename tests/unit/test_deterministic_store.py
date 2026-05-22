"""Strict deterministic-replay contract for ``DeterministicStore`` (issue #1832).

These tests pin the hermetic-replay behaviour:

* a cache miss in strict replay mode raises a typed error carrying the
  prompt key and model (never ``None``);
* the replay key folds in provider, temperature, and max_tokens, so a
  parameter drift is a miss, not a silent hit;
* hit/miss/strict-violation counters back the operator coverage line.

No network, no agents - pure store logic over a fixture JSONL file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.orchestration.deterministic import (
    DeterministicStore,
    ReplayMissError,
    _prompt_key,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_MODEL = "claude-3-5-sonnet"
_PROMPT = "decompose this objective into tasks"
_RESPONSE = "1. task one\n2. task two"


def _write_recording(
    run_dir: Path,
    *,
    prompt: str = _PROMPT,
    model: str = _MODEL,
    response: str = _RESPONSE,
    provider: str = "openrouter_free",
    temperature: float = 0.7,
    max_tokens: int = 4000,
    count: int = 1,
) -> Path:
    """Write a ``count``-line ``llm_calls.jsonl`` recording and return its path.

    The recording is produced via :func:`_prompt_key` so the on-disk key
    matches whatever the production keying does, keeping the fixture
    honest if the key recipe changes.

    ``count`` repeats the same entry that many times. Because ``get_replay``
    consumes one recorded response per hit (per-key FIFO, issue #1846), a key
    that must serve N hits needs N recorded lines.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1 to record at least one call, got {count}")
    run_dir.mkdir(parents=True, exist_ok=True)
    calls_path = run_dir / "llm_calls.jsonl"
    key = _prompt_key(prompt, model, provider=provider, temperature=temperature, max_tokens=max_tokens)
    entry = {
        "ts": 1.0,
        "key": key,
        "model": model,
        "provider": provider,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt_len": len(prompt),
        "response": response,
    }
    calls_path.write_text((json.dumps(entry) + "\n") * count, encoding="utf-8")
    return calls_path


# ---------------------------------------------------------------------------
# Strict replay: known vs unknown key
# ---------------------------------------------------------------------------


class TestStrictReplayLookup:
    def test_known_key_returns_recorded_response(self, tmp_path: Path) -> None:
        """A prompt present in the recording replays its stored response."""
        run_dir = tmp_path / "runs" / "rec-1"
        _write_recording(run_dir)

        store = DeterministicStore(run_dir, replay=True, strict=True)

        result = store.get_replay(_PROMPT, _MODEL)
        assert result == _RESPONSE

    def test_unknown_key_raises_replay_miss_error(self, tmp_path: Path) -> None:
        """A prompt absent from the recording raises a typed miss error."""
        run_dir = tmp_path / "runs" / "rec-2"
        _write_recording(run_dir)

        store = DeterministicStore(run_dir, replay=True, strict=True)

        with pytest.raises(ReplayMissError):
            store.get_replay("a prompt that was never recorded", _MODEL)

    def test_miss_error_carries_key_and_model_not_none(self, tmp_path: Path) -> None:
        """The error exposes the prompt key and model (never ``None``)."""
        run_dir = tmp_path / "runs" / "rec-3"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        with pytest.raises(ReplayMissError) as excinfo:
            store.get_replay("unseen prompt", "some-other-model")

        err = excinfo.value
        assert err.model == "some-other-model"
        assert err.key is not None
        assert err.key == _prompt_key("unseen prompt", "some-other-model")

    def test_miss_error_message_names_run_and_rerecord(self, tmp_path: Path) -> None:
        """The miss message tells the operator how to re-record."""
        run_dir = tmp_path / "runs" / "rec-help"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        with pytest.raises(ReplayMissError) as excinfo:
            store.get_replay("unseen prompt", _MODEL)

        msg = str(excinfo.value)
        assert "re-record" in msg.lower()
        assert "BERNSTEIN_REPLAY_RUN_ID" in msg


# ---------------------------------------------------------------------------
# Key widening: provider / temperature / max_tokens
# ---------------------------------------------------------------------------


class TestKeyWidening:
    def test_different_temperature_is_a_miss(self, tmp_path: Path) -> None:
        """Same (model, prompt) but a different temperature is a miss."""
        run_dir = tmp_path / "runs" / "temp-drift"
        _write_recording(run_dir, temperature=0.7)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        # Exact temperature hits.
        assert store.get_replay(_PROMPT, _MODEL, temperature=0.7) == _RESPONSE

        # Drifted temperature misses.
        with pytest.raises(ReplayMissError):
            store.get_replay(_PROMPT, _MODEL, temperature=0.0)

    def test_different_provider_is_a_miss(self, tmp_path: Path) -> None:
        """Same (model, prompt) but a different provider is a miss."""
        run_dir = tmp_path / "runs" / "provider-drift"
        _write_recording(run_dir, provider="openrouter_free")
        store = DeterministicStore(run_dir, replay=True, strict=True)

        assert store.get_replay(_PROMPT, _MODEL, provider="openrouter_free") == _RESPONSE

        with pytest.raises(ReplayMissError):
            store.get_replay(_PROMPT, _MODEL, provider="openrouter")

    def test_different_max_tokens_is_a_miss(self, tmp_path: Path) -> None:
        """Same (model, prompt) but a different max_tokens is a miss."""
        run_dir = tmp_path / "runs" / "tokens-drift"
        _write_recording(run_dir, max_tokens=4000)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        assert store.get_replay(_PROMPT, _MODEL, max_tokens=4000) == _RESPONSE

        with pytest.raises(ReplayMissError):
            store.get_replay(_PROMPT, _MODEL, max_tokens=256)

    def test_prompt_key_changes_with_each_response_determining_input(self) -> None:
        """The key recipe is sensitive to every response-determining input."""
        base = _prompt_key(_PROMPT, _MODEL, provider="openrouter_free", temperature=0.7, max_tokens=4000)
        assert base != _prompt_key("other prompt", _MODEL, provider="openrouter_free", temperature=0.7, max_tokens=4000)
        assert base != _prompt_key(_PROMPT, "other-model", provider="openrouter_free", temperature=0.7, max_tokens=4000)
        assert base != _prompt_key(_PROMPT, _MODEL, provider="openrouter", temperature=0.7, max_tokens=4000)
        assert base != _prompt_key(_PROMPT, _MODEL, provider="openrouter_free", temperature=0.0, max_tokens=4000)
        assert base != _prompt_key(_PROMPT, _MODEL, provider="openrouter_free", temperature=0.7, max_tokens=256)


# ---------------------------------------------------------------------------
# Non-strict escape hatch
# ---------------------------------------------------------------------------


class TestNonStrictEscapeHatch:
    def test_non_strict_miss_returns_none(self, tmp_path: Path) -> None:
        """With strict=False a miss returns ``None`` (the old fall-through)."""
        run_dir = tmp_path / "runs" / "lax"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=False)

        assert store.get_replay("unseen prompt", _MODEL) is None

    def test_non_strict_hit_still_returns_response(self, tmp_path: Path) -> None:
        """Non-strict mode still serves recorded hits."""
        run_dir = tmp_path / "runs" / "lax-hit"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=False)

        assert store.get_replay(_PROMPT, _MODEL) == _RESPONSE

    def test_replay_defaults_to_strict(self, tmp_path: Path) -> None:
        """``replay=True`` defaults to strict mode."""
        run_dir = tmp_path / "runs" / "default"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True)

        assert store.is_strict is True
        with pytest.raises(ReplayMissError):
            store.get_replay("unseen prompt", _MODEL)

    def test_recording_mode_never_raises(self, tmp_path: Path) -> None:
        """A recording-mode store returns ``None`` and never raises on miss."""
        run_dir = tmp_path / "runs" / "recording"
        store = DeterministicStore(run_dir, replay=False)

        assert store.get_replay("anything", _MODEL) is None


# ---------------------------------------------------------------------------
# Coverage counters / coverage line
# ---------------------------------------------------------------------------


class TestCoverageCounters:
    def test_hits_and_misses_counted(self, tmp_path: Path) -> None:
        """Hits and strict violations are tallied for the coverage line."""
        run_dir = tmp_path / "runs" / "cov"
        # Two recorded lines for the key: get_replay consumes one per hit
        # (per-key FIFO, issue #1846), so two hits need two recordings.
        _write_recording(run_dir, count=2)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        store.get_replay(_PROMPT, _MODEL)  # hit (consumes recording 1)
        store.get_replay(_PROMPT, _MODEL)  # hit (consumes recording 2)
        with pytest.raises(ReplayMissError):
            store.get_replay("miss", _MODEL)  # strict violation

        assert store.hits == 2
        assert store.strict_violations == 1

    def test_non_strict_miss_increments_miss_counter(self, tmp_path: Path) -> None:
        """A non-strict miss bumps ``misses`` (not ``strict_violations``)."""
        run_dir = tmp_path / "runs" / "cov-lax"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=False)

        store.get_replay(_PROMPT, _MODEL)  # hit
        store.get_replay("miss", _MODEL)  # non-strict miss

        assert store.hits == 1
        assert store.misses == 1
        assert store.strict_violations == 0

    def test_coverage_line_reports_zero_misses_on_full_replay(self, tmp_path: Path) -> None:
        """A fully covered replay reports miss-count 0 in the coverage line."""
        run_dir = tmp_path / "runs" / "cov-full"
        _write_recording(run_dir)
        store = DeterministicStore(run_dir, replay=True, strict=True)

        store.get_replay(_PROMPT, _MODEL)

        line = store.coverage_line()
        assert "hits=1" in line
        assert "misses=0" in line
        assert "strict_violations=0" in line


# ---------------------------------------------------------------------------
# Stale recordings (key widening invalidates old llm_calls.jsonl)
# ---------------------------------------------------------------------------


class TestStaleRecordings:
    def test_legacy_narrow_key_recording_is_a_miss(self, tmp_path: Path) -> None:
        """An old recording keyed on only ``model\\x00prompt`` is a miss now.

        Pre-#1832 recordings hashed only ``model\\x00prompt``. Under the
        widened key those rows no longer match, so strict replay treats
        them as misses (the documented behaviour change).
        """
        import hashlib

        run_dir = tmp_path / "runs" / "legacy"
        run_dir.mkdir(parents=True, exist_ok=True)
        legacy_key = hashlib.sha256(f"{_MODEL}\x00{_PROMPT}".encode()).hexdigest()
        entry = {"ts": 1.0, "key": legacy_key, "model": _MODEL, "prompt_len": len(_PROMPT), "response": _RESPONSE}
        (run_dir / "llm_calls.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")

        store = DeterministicStore(run_dir, replay=True, strict=True)

        with pytest.raises(ReplayMissError):
            store.get_replay(_PROMPT, _MODEL)


# ---------------------------------------------------------------------------
# Env-driven escape hatch (BERNSTEIN_REPLAY_ALLOW_LIVE_MISS)
# ---------------------------------------------------------------------------


class TestAllowLiveMissEnv:
    def test_load_replay_store_is_strict_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``load_replay_store`` builds a strict store unless opted out."""
        from bernstein.core.orchestration.deterministic import ALLOW_LIVE_MISS_ENV, load_replay_store

        monkeypatch.delenv(ALLOW_LIVE_MISS_ENV, raising=False)
        _write_recording(tmp_path / "runs" / "run-x")

        store = load_replay_store("run-x", tmp_path)
        assert store.is_strict is True

    def test_load_replay_store_honours_escape_hatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The env flag downgrades the loaded store to non-strict."""
        from bernstein.core.orchestration.deterministic import ALLOW_LIVE_MISS_ENV, load_replay_store

        monkeypatch.setenv(ALLOW_LIVE_MISS_ENV, "1")
        _write_recording(tmp_path / "runs" / "run-y")

        store = load_replay_store("run-y", tmp_path)
        assert store.is_strict is False


# ---------------------------------------------------------------------------
# Integration: orchestrator-style replay wiring is hermetic end-to-end
# ---------------------------------------------------------------------------


class TestReplayWiringHermetic:
    """Reproduce the orchestrator's BERNSTEIN_REPLAY_RUN_ID wiring and drive
    ``call_llm`` through it with a provider double.

    This is the integration guarantee from issue #1832: a fully-covered
    replay makes zero live provider calls, and an unknown prompt raises and
    aborts without ever touching a provider.
    """

    @pytest.fixture(autouse=True)
    def _clear_active_store(self):
        from bernstein.core.orchestration.deterministic import set_active_store

        set_active_store(None)
        yield
        set_active_store(None)

    def _wire_replay_store(self, sdd_dir: Path, run_id: str):
        """Mimic orchestrator._run_orchestrator's replay wiring exactly."""
        from bernstein.core.orchestration.deterministic import (
            DeterministicStore,
            allow_live_miss,
            set_active_store,
        )

        store = DeterministicStore(
            sdd_dir / "runs" / run_id,
            replay=True,
            strict=not allow_live_miss(),
        )
        set_active_store(store)
        return store

    @pytest.mark.asyncio
    async def test_full_replay_makes_zero_live_calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A replay covering every prompt completes with 0 live calls, misses=0."""
        from unittest.mock import patch

        from bernstein.core.llm import call_llm

        from bernstein.core.orchestration.deterministic import ALLOW_LIVE_MISS_ENV

        monkeypatch.delenv(ALLOW_LIVE_MISS_ENV, raising=False)
        sdd_dir = tmp_path / ".sdd"
        _write_recording(sdd_dir / "runs" / "run-cov", prompt="p1", model="gpt-4", response="r1")

        store = self._wire_replay_store(sdd_dir, "run-cov")

        live_calls = {"n": 0}

        async def _spy(*_a: object, **_k: object) -> str:
            live_calls["n"] += 1
            return "LIVE"

        with patch("bernstein.core.llm._call_api_provider", new=_spy):
            out = await call_llm("p1", "gpt-4", provider="openrouter_free")

        assert out == "r1"
        assert live_calls["n"] == 0
        assert store.hits == 1
        assert store.misses == 0
        assert store.strict_violations == 0
        assert "misses=0" in store.coverage_line()

    @pytest.mark.asyncio
    async def test_unknown_prompt_raises_and_aborts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An injected unknown prompt raises ReplayMissError; no live call."""
        from unittest.mock import patch

        from bernstein.core.llm import call_llm

        from bernstein.core.orchestration.deterministic import ALLOW_LIVE_MISS_ENV

        monkeypatch.delenv(ALLOW_LIVE_MISS_ENV, raising=False)
        sdd_dir = tmp_path / ".sdd"
        _write_recording(sdd_dir / "runs" / "run-miss", prompt="p1", model="gpt-4", response="r1")

        store = self._wire_replay_store(sdd_dir, "run-miss")

        live_calls = {"n": 0}

        async def _spy(*_a: object, **_k: object) -> str:
            live_calls["n"] += 1
            return "LIVE"

        with patch("bernstein.core.llm._call_api_provider", new=_spy), pytest.raises(ReplayMissError):
            await call_llm("a brand new prompt never recorded", "gpt-4", provider="openrouter_free")

        assert live_calls["n"] == 0
        assert store.strict_violations == 1
