"""Tests for DeepSeek V4 adapter dispatch + EU-residency profile + pricing.

Covers FEAT ``deepseek-v4-flash-eu``:

* :class:`OllamaAdapter` dispatches ``deepseek-v4-flash`` and
  ``deepseek-v4-pro`` model names through ``_resolve_model``.
* Constructing the adapter with ``eu_residency=True`` flips the
  enforcement flag, and the public ``eu_residency`` property reflects it.
* :meth:`OllamaAdapter._is_self_hosted_endpoint` is default-closed: it
  accepts loopback / RFC-1918 / ``*.internal`` hosts and rejects public
  IPs, hosted-API hostnames, and unrecognised FQDNs.
* The pricing tables (``MODEL_COSTS_PER_1M_TOKENS`` and the blended
  ``_MODEL_COST_USD_PER_1K``) carry the new SKUs and ``estimate_cost``
  arithmetic matches the per-MTok numbers from the ticket.
* ``spawn`` raises a structured ``RESIDENCY_VIOLATION`` when called
  against a non-self-hosted endpoint with a residency-tagged model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.ollama import (
    _EU_RESIDENCY_MODELS,
    _MODEL_MAP,
    OLLAMA_BASE_URL,
    OllamaAdapter,
)
from bernstein.core.cost.cost import (
    _MODEL_COST_USD_PER_1K,
    MODEL_COSTS_PER_1M_TOKENS,
    _model_cost,
)
from bernstein.core.cost.cost_tracker import estimate_cost

# ---------------------------------------------------------------------------
# Model-name dispatch
# ---------------------------------------------------------------------------


class TestModelDispatch:
    """Bernstein abstract names map to the right local model IDs."""

    def test_deepseek_v4_flash_resolves_to_native_id(self) -> None:
        adapter = OllamaAdapter()
        assert adapter._resolve_model("deepseek-v4-flash") == "deepseek-v4-flash"

    def test_deepseek_v4_pro_resolves_to_native_id(self) -> None:
        adapter = OllamaAdapter()
        assert adapter._resolve_model("deepseek-v4-pro") == "deepseek-v4-pro"

    def test_unknown_model_passes_through(self) -> None:
        """Custom Ollama model IDs (e.g. user-tagged) are not rewritten."""
        adapter = OllamaAdapter()
        assert adapter._resolve_model("custom:7b") == "custom:7b"

    def test_model_map_contains_both_v4_skus(self) -> None:
        assert "deepseek-v4-flash" in _MODEL_MAP
        assert "deepseek-v4-pro" in _MODEL_MAP

    def test_eu_residency_set_includes_both_v4_skus(self) -> None:
        assert "deepseek-v4-flash" in _EU_RESIDENCY_MODELS
        assert "deepseek-v4-pro" in _EU_RESIDENCY_MODELS


# ---------------------------------------------------------------------------
# EU-residency profile flag
# ---------------------------------------------------------------------------


class TestEuResidencyFlag:
    """Adapter construction flips the residency-enforcement flag."""

    def test_default_flag_is_false(self) -> None:
        adapter = OllamaAdapter()
        assert adapter.eu_residency is False

    def test_explicit_true_flips_flag(self) -> None:
        adapter = OllamaAdapter(eu_residency=True)
        assert adapter.eu_residency is True

    def test_default_base_url(self) -> None:
        """Default base URL is the local loopback address."""
        adapter = OllamaAdapter()
        assert adapter._base_url == OLLAMA_BASE_URL
        assert adapter._is_self_hosted_endpoint(adapter._base_url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:11434",
            "http://0.0.0.0:11434",
            "http://10.0.0.5:8000",
            "http://192.168.1.100:11434",
            "http://172.16.0.1:8000",
            "http://172.31.255.1:8000",
            "http://vllm.internal:8000/v1",
            "http://gpu-host.local:11434",
            "http://ollama.svc.cluster.local:11434",
        ],
    )
    def test_self_hosted_urls_pass(self, url: str) -> None:
        """Loopback, RFC-1918, and ``*.internal`` hosts count as self-hosted."""
        adapter = OllamaAdapter()
        assert adapter._is_self_hosted_endpoint(url) is True, url

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.deepseek.com",
            "https://deepseek.com/v1",
            "https://api.openai.com/v1",
            "https://openrouter.ai/api/v1",
            "https://api.anthropic.com",
            "https://generativelanguage.googleapis.com",
            "https://api.together.xyz",
            "https://api.groq.com",
            # Public IP outside RFC-1918 - fail closed.
            "http://8.8.8.8:8000",
            "http://172.32.5.5:8000",  # 172.16/12 boundary check
            "http://172.15.0.1:8000",  # below 16, also out of range
            # Unrecognised public hostname - fail closed.
            "https://random-public-host.example.com",
            # Empty / malformed.
            "",
            "not-a-url",
        ],
    )
    def test_non_self_hosted_urls_rejected(self, url: str) -> None:
        adapter = OllamaAdapter()
        assert adapter._is_self_hosted_endpoint(url) is False, url


# ---------------------------------------------------------------------------
# Spawn rejects residency violations
# ---------------------------------------------------------------------------


class TestResidencyEnforcement:
    """The spawn() guard refuses non-self-hosted endpoints under residency."""

    def _model_cfg(self, model: str = "deepseek-v4-flash") -> ModelConfig:
        return ModelConfig(model=model, effort="normal")

    def test_v4_model_with_hosted_endpoint_raises(self, tmp_path: Path) -> None:
        """A residency-tagged model + hosted endpoint = RESIDENCY_VIOLATION."""
        adapter = OllamaAdapter(base_url="https://api.deepseek.com")
        with pytest.raises(RuntimeError, match="RESIDENCY_VIOLATION"):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=self._model_cfg(),
                session_id="backend-1",
            )

    def test_eu_flag_forces_check_on_non_v4_model(self, tmp_path: Path) -> None:
        """``eu_residency=True`` enforces the check even for non-V4 models."""
        adapter = OllamaAdapter(
            base_url="https://api.openrouter.ai",
            eu_residency=True,
        )
        with pytest.raises(RuntimeError, match="RESIDENCY_VIOLATION"):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="qwen2.5-coder", effort="normal"),
                session_id="backend-2",
            )

    def test_violation_error_is_structured(self, tmp_path: Path) -> None:
        """Error message names the model and the offending endpoint."""
        adapter = OllamaAdapter(base_url="https://api.deepseek.com")
        with pytest.raises(RuntimeError) as exc_info:
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=self._model_cfg("deepseek-v4-pro"),
                session_id="backend-3",
            )
        msg = str(exc_info.value)
        assert "RESIDENCY_VIOLATION" in msg
        assert "deepseek-v4-pro" in msg
        assert "deepseek.com" in msg


# ---------------------------------------------------------------------------
# Pricing table lookup
# ---------------------------------------------------------------------------


class TestPricingLookup:
    """The pricing tables carry the DeepSeek V4 SKUs at ticket-spec values."""

    def test_flash_per_mtok_input_price(self) -> None:
        """Ticket pins V4-Flash hosted input at ~$1.74/MTok."""
        assert MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-flash"]["input"] == 1.74

    def test_flash_per_mtok_output_price(self) -> None:
        assert MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-flash"]["output"] == pytest.approx(0.20)

    def test_pro_per_mtok_prices(self) -> None:
        pricing = MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-pro"]
        assert pricing["input"] == 4.50
        assert pricing["output"] == 1.50

    def test_blended_table_carries_both_skus(self) -> None:
        assert "deepseek-v4-flash" in _MODEL_COST_USD_PER_1K
        assert "deepseek-v4-pro" in _MODEL_COST_USD_PER_1K

    def test_blended_substring_lookup_picks_specific_sku(self) -> None:
        """``_model_cost`` substring lookup must hit the correct row."""
        assert _model_cost("deepseek-v4-flash") == _MODEL_COST_USD_PER_1K["deepseek-v4-flash"]
        assert _model_cost("deepseek-v4-pro") == _MODEL_COST_USD_PER_1K["deepseek-v4-pro"]

    def test_flash_cheaper_than_pro(self) -> None:
        """V4-Flash must come in cheaper than V4-Pro - that's the entire pitch."""
        assert _MODEL_COST_USD_PER_1K["deepseek-v4-flash"] < _MODEL_COST_USD_PER_1K["deepseek-v4-pro"]
        assert (
            MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-flash"]["input"]
            < MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-pro"]["input"]
        )

    def test_estimate_cost_matches_mtok_rate(self) -> None:
        """1M input tokens of V4-Flash should bill at exactly $1.74."""
        assert estimate_cost("deepseek-v4-flash", 1_000_000, 0) == pytest.approx(1.74)

    def test_estimate_cost_combines_input_and_output(self) -> None:
        """1M in + 1M out for V4-Flash = $1.74 + $0.20 = $1.94."""
        assert estimate_cost("deepseek-v4-flash", 1_000_000, 1_000_000) == pytest.approx(1.94)

    def test_v4_flash_undercuts_opus_input_by_at_least_2x(self) -> None:
        """The whole pitch: V4-Flash hosted input << Claude Opus input."""
        flash_input = MODEL_COSTS_PER_1M_TOKENS["deepseek-v4-flash"]["input"]
        opus_input = MODEL_COSTS_PER_1M_TOKENS["opus"]["input"]
        assert flash_input <= opus_input / 2.0
