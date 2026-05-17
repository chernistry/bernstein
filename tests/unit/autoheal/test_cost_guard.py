"""Unit tests for ``bernstein.core.autoheal.cost_guard``."""

from __future__ import annotations

import pytest

from bernstein.core.autoheal.cost_guard import (
    DEFAULT_BUDGET_USD,
    ENV_BUDGET,
    ENV_DISABLE_LLM,
    ENV_GLOBAL_BUDGET,
    llm_globally_disabled,
    should_allow_llm_call,
)


def test_within_budget_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BUDGET, raising=False)
    monkeypatch.delenv(ENV_GLOBAL_BUDGET, raising=False)
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.05, 0.10)
    assert d.allowed is True
    assert d.budget_usd == DEFAULT_BUDGET_USD
    assert d.reason == "within_budget"


def test_over_budget_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BUDGET, "1.00")
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.50, 0.60)
    assert d.allowed is False
    assert "would_exceed_budget" in d.reason


def test_at_budget_boundary_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BUDGET, "1.00")
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.30, 0.70)
    assert d.allowed is True


def test_explicit_budget_argument_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BUDGET, "10.00")
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.50, 0.60, budget_usd=1.00)
    assert d.allowed is False
    assert d.budget_usd == pytest.approx(1.0)


def test_global_disable_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DISABLE_LLM, "1")
    d = should_allow_llm_call(0.01, 0.0)
    assert d.allowed is False
    assert d.reason == "llm_disabled_via_env"


def test_negative_estimate_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(-1.0, 0.0)
    assert d.allowed is False
    assert d.reason == "negative_estimate"


def test_negative_spend_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.1, -1.0)
    assert d.allowed is False
    assert d.reason == "negative_spend"


def test_invalid_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BUDGET, "not-a-number")
    monkeypatch.delenv(ENV_GLOBAL_BUDGET, raising=False)
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.05, 0.0)
    assert d.allowed is True
    assert d.budget_usd == DEFAULT_BUDGET_USD


def test_global_budget_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BUDGET, raising=False)
    monkeypatch.setenv(ENV_GLOBAL_BUDGET, "2.00")
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(1.50, 1.00)  # 2.50 > 2.00 -> deny
    assert d.allowed is False


def test_llm_globally_disabled_only_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DISABLE_LLM, "")
    assert llm_globally_disabled() is False
    monkeypatch.setenv(ENV_DISABLE_LLM, "yes")
    assert llm_globally_disabled() is True


def test_decision_carries_full_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BUDGET, "1.00")
    monkeypatch.delenv(ENV_DISABLE_LLM, raising=False)
    d = should_allow_llm_call(0.25, 0.10)
    assert d.estimated_call_usd == pytest.approx(0.25)
    assert d.spent_usd == pytest.approx(0.10)
    assert d.budget_usd == pytest.approx(1.0)
