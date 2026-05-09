"""Tests for the prefix-preserving tool-masking pass."""

from __future__ import annotations

import pytest

from bernstein.core.agents.tool_masking import mask_tools


def _entry(name: str, **extra: object) -> dict[str, object]:
    """Helper that builds a tool dict with ``name`` plus optional fields."""
    base: dict[str, object] = {"name": name, "description": f"desc-{name}"}
    base.update(extra)
    return base


class TestMaskToolsHappyPath:
    def test_no_denied_returns_input_unchanged(self) -> None:
        tools = [_entry("a"), _entry("b")]
        result = mask_tools(tools, denied=[])
        assert result.tools == tools
        assert result.masked_names == ()
        assert result.fallback_removed == ()

    def test_masking_keeps_entry_and_flips_unavailable_flag(self) -> None:
        tools = [_entry("a"), _entry("b")]
        result = mask_tools(tools, denied=["b"], reason="role does not allow writes")
        assert len(result.tools) == 2
        assert result.tools[0] == _entry("a")
        masked = result.tools[1]
        assert masked["name"] == "b"
        assert masked["unavailable"] is True
        assert masked["unavailable_reason"] == "role does not allow writes"
        assert "description" in masked  # original fields preserved
        assert result.masked_names == ("b",)
        assert result.fallback_removed == ()

    def test_masking_preserves_entry_order(self) -> None:
        """Critical for KV-cache locality: order of entries must not shift."""
        tools = [_entry(name) for name in ("alpha", "bravo", "charlie", "delta")]
        result = mask_tools(tools, denied=["bravo", "charlie"])
        names = [t["name"] for t in result.tools]
        assert names == ["alpha", "bravo", "charlie", "delta"]

    def test_per_tool_reason_mapping(self) -> None:
        tools = [_entry("read"), _entry("write"), _entry("delete")]
        result = mask_tools(
            tools,
            denied=["write", "delete"],
            reason={"write": "qa role", "delete": "always denied"},
        )
        masked = {t["name"]: t for t in result.tools if t.get("unavailable")}
        assert masked["write"]["unavailable_reason"] == "qa role"
        assert masked["delete"]["unavailable_reason"] == "always denied"

    def test_per_tool_reason_falls_back_to_default(self) -> None:
        """Names missing from the mapping use the documented default reason."""
        tools = [_entry("write")]
        result = mask_tools(tools, denied=["write"], reason={})
        assert result.tools[0]["unavailable_reason"] == "denied by agent identity card"

    def test_extra_keys_pass_through(self) -> None:
        """Cache-control / input-schema fields must survive masking untouched."""
        tools = [
            _entry("a", input_schema={"type": "object"}, cache_control={"type": "ephemeral"}),
        ]
        result = mask_tools(tools, denied=["a"])
        assert result.tools[0]["input_schema"] == {"type": "object"}
        assert result.tools[0]["cache_control"] == {"type": "ephemeral"}

    def test_dedupes_denied_set(self) -> None:
        tools = [_entry("a")]
        result = mask_tools(tools, denied=["a", "a", "a"])
        assert result.masked_names == ("a",)


class TestFallbackPath:
    def test_fallback_removes_entries_when_flag_not_supported(self) -> None:
        """For adapters that lack the ``unavailable`` field, entries are dropped."""
        tools = [_entry("a"), _entry("b"), _entry("c")]
        result = mask_tools(
            tools,
            denied=["b"],
            adapter_supports_unavailable_flag=False,
        )
        names = [t["name"] for t in result.tools]
        assert names == ["a", "c"]
        assert result.masked_names == ()
        assert result.fallback_removed == ("b",)


class TestEdgeCases:
    def test_empty_tools_returns_empty_result(self) -> None:
        result = mask_tools([], denied=["whatever"])
        assert result.tools == []
        assert result.masked_names == ()
        assert result.fallback_removed == ()

    def test_denied_name_not_in_tools_is_silently_ignored(self) -> None:
        result = mask_tools([_entry("a")], denied=["ghost"])
        assert result.tools == [_entry("a")]
        assert result.masked_names == ()

    def test_missing_name_key_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            mask_tools([{"description": "no name"}], denied=[])

    def test_disabled_when_called_with_no_denied_set(self) -> None:
        """Disabled-equivalent code path: ``denied=[]`` is the off switch."""
        tools = [_entry("a"), _entry("b")]
        result = mask_tools(tools, denied=[])
        # Output is byte-equal to input modulo dict copies.
        assert result.tools == tools
        # Each output dict is a fresh copy, not the same identity.
        assert result.tools[0] is not tools[0]

    def test_empty_string_in_denied_is_filtered(self) -> None:
        """Empty/falsy denied names must not match any real tool."""
        result = mask_tools([_entry("a")], denied=["", "a"])
        assert result.masked_names == ("a",)
