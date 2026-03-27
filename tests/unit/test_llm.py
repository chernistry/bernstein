"""Unit tests for bernstein.core.llm — LLMSettings and get_client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bernstein.core.llm import LLMSettings, get_client


# ---------------------------------------------------------------------------
# LLMSettings — env var loading
# ---------------------------------------------------------------------------


class TestLLMSettings:
    def test_all_fields_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "OPENROUTER_API_KEY_PAID",
            "OPENROUTER_API_KEY_FREE",
            "OXEN_API_KEY",
            "TOGETHERAI_USER_KEY",
            "G4F_API_KEY",
            "OPENAI_API_KEY",
            "TAVILY_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openrouter_api_key_paid is None
        assert s.openrouter_api_key_free is None
        assert s.oxen_api_key is None
        assert s.togetherai_user_key is None
        assert s.g4f_api_key is None
        assert s.openai_api_key is None
        assert s.tavily_api_key is None

    def test_reads_openrouter_keys_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY_PAID", "paid-key")
        monkeypatch.setenv("OPENROUTER_API_KEY_FREE", "free-key")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openrouter_api_key_paid == "paid-key"
        assert s.openrouter_api_key_free == "free-key"

    def test_reads_oxen_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OXEN_API_KEY", "oxen-secret")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.oxen_api_key == "oxen-secret"

    def test_reads_togetherai_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOGETHERAI_USER_KEY", "together-secret")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.togetherai_user_key == "together-secret"

    def test_reads_g4f_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("G4F_API_KEY", "g4f-secret")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.g4f_api_key == "g4f-secret"

    def test_reads_openai_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_api_key == "sk-test"

    def test_reads_tavily_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.tavily_api_key == "tvly-secret"

    def test_default_oxen_base_url(self) -> None:
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.oxen_base_url == "https://hub.oxen.ai/api"

    def test_default_g4f_base_url(self) -> None:
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.g4f_base_url == "https://g4f.space/v1"

    def test_openai_base_url_defaults_to_none(self) -> None:
        s = LLMSettings(_env_file=None)  # type: ignore[call-arg]
        assert s.openai_base_url is None


# ---------------------------------------------------------------------------
# get_client — provider routing and base_url selection
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: str | None) -> LLMSettings:
    """Build an LLMSettings with all keys cleared unless specified."""
    defaults: dict[str, str | None] = {
        "openrouter_api_key_paid": None,
        "openrouter_api_key_free": None,
        "oxen_api_key": None,
        "togetherai_user_key": None,
        "g4f_api_key": None,
        "openai_api_key": None,
        "openai_base_url": None,
        "tavily_api_key": None,
    }
    defaults.update(kwargs)
    return LLMSettings.model_construct(**defaults)  # type: ignore[arg-type]


class TestGetClient:
    # --- openrouter ---

    def test_openrouter_returns_client_with_correct_base_url(self) -> None:
        settings = _make_settings(openrouter_api_key_paid="paid-key")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("openrouter")
            mock_cls.assert_called_once_with(
                base_url="https://openrouter.ai/api/v1",
                api_key="paid-key",
            )

    def test_openrouter_raises_when_paid_key_missing(self) -> None:
        settings = _make_settings(openrouter_api_key_paid=None)
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="OPENROUTER_API_KEY_PAID"):
                get_client("openrouter")

    # --- openrouter_free ---

    def test_openrouter_free_uses_free_key_when_available(self) -> None:
        settings = _make_settings(
            openrouter_api_key_free="free-key",
            openrouter_api_key_paid="paid-key",
        )
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("openrouter_free")
            mock_cls.assert_called_once_with(
                base_url="https://openrouter.ai/api/v1",
                api_key="free-key",
            )

    def test_openrouter_free_falls_back_to_paid_key(self) -> None:
        settings = _make_settings(
            openrouter_api_key_free=None,
            openrouter_api_key_paid="paid-key",
        )
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("openrouter_free")
            mock_cls.assert_called_once_with(
                base_url="https://openrouter.ai/api/v1",
                api_key="paid-key",
            )

    def test_openrouter_free_raises_when_both_keys_missing(self) -> None:
        settings = _make_settings(
            openrouter_api_key_free=None,
            openrouter_api_key_paid=None,
        )
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="OpenRouter API key"):
                get_client("openrouter_free")

    # --- oxen ---

    def test_oxen_returns_client_with_default_base_url(self) -> None:
        settings = _make_settings(oxen_api_key="oxen-secret")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("oxen")
            mock_cls.assert_called_once_with(
                base_url="https://hub.oxen.ai/api",
                api_key="oxen-secret",
            )

    def test_oxen_raises_when_key_missing(self) -> None:
        settings = _make_settings(oxen_api_key=None)
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="OXEN_API_KEY"):
                get_client("oxen")

    # --- together ---

    def test_together_returns_client_with_correct_base_url(self) -> None:
        settings = _make_settings(togetherai_user_key="together-secret")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("together")
            mock_cls.assert_called_once_with(
                base_url="https://api.together.xyz/v1",
                api_key="together-secret",
            )

    def test_together_raises_when_key_missing(self) -> None:
        settings = _make_settings(togetherai_user_key=None)
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="TOGETHERAI_USER_KEY"):
                get_client("together")

    # --- g4f ---

    def test_g4f_returns_client_with_default_base_url(self) -> None:
        settings = _make_settings(g4f_api_key="g4f-secret")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("g4f")
            mock_cls.assert_called_once_with(
                base_url="https://g4f.space/v1",
                api_key="g4f-secret",
            )

    def test_g4f_raises_when_key_missing(self) -> None:
        settings = _make_settings(g4f_api_key=None)
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="G4F_API_KEY"):
                get_client("g4f")

    # --- openai (default fallback) ---

    def test_openai_default_fallback_when_unknown_provider(self) -> None:
        settings = _make_settings(openai_api_key="sk-test")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("openai")
            mock_cls.assert_called_once_with(
                base_url=None,
                api_key="sk-test",
            )

    def test_openai_with_custom_base_url(self) -> None:
        settings = _make_settings(
            openai_api_key="sk-test",
            openai_base_url="https://my-proxy.example.com/v1",
        )
        with patch("bernstein.core.llm.LLMSettings", return_value=settings), patch(
            "bernstein.core.llm.AsyncOpenAI"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            get_client("openai")
            mock_cls.assert_called_once_with(
                base_url="https://my-proxy.example.com/v1",
                api_key="sk-test",
            )

    def test_raises_when_unknown_provider_and_no_openai_key(self) -> None:
        settings = _make_settings(openai_api_key=None)
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            with pytest.raises(ValueError, match="Unknown or unconfigured provider"):
                get_client("nonexistent_provider")

    # --- return type ---

    def test_get_client_returns_asyncopenai_instance(self) -> None:
        from openai import AsyncOpenAI

        settings = _make_settings(openrouter_api_key_paid="paid-key")
        with patch("bernstein.core.llm.LLMSettings", return_value=settings):
            client = get_client("openrouter")
        assert isinstance(client, AsyncOpenAI)
