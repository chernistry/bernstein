"""Tests for MCP tool name + schema normalization (MCP-003).

Covers ``mcp_tool_normalization`` pure logic:

* ``normalize_tool_name`` across camelCase, PascalCase, kebab, dotted,
  acronym, and already-snake inputs (plus the empty short-circuit).
* ``validate_tool_schema`` - type / properties / required / items checks.
* ``validate_tool_params`` + ``_type_matches`` - presence + type gating,
  including the bool-is-not-integer edge.
* ``ToolNormalizer`` registry: register / lookup both directions /
  ``normalize_call`` validation path / unregistered passthrough.
* ``McpToolError.to_dict`` + ``McpToolException`` wrapping.
* ``_decode_tool_result`` JSON-object decoding + error paths.

All deterministic - no I/O.
"""

from __future__ import annotations

import pytest

from bernstein.core.protocols.mcp.mcp_tool_normalization import (
    McpToolError,
    McpToolException,
    ToolNormalizer,
    _decode_tool_result,
    _type_matches,
    normalize_tool_name,
    validate_tool_params,
    validate_tool_schema,
)


class TestNormalizeToolName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("searchIssues", "search_issues"),
            ("SearchIssues", "search_issues"),
            ("get-user-profile", "get_user_profile"),
            ("myServer.SearchIssues", "my_server_search_issues"),
            ("already_snake_case", "already_snake_case"),
            ("HTTPSConnection", "https_connection"),
            ("tool/with/slashes", "tool_with_slashes"),
            ("Multiple___Underscores", "multiple_underscores"),
        ],
    )
    def test_normalization(self, raw: str, expected: str) -> None:
        assert normalize_tool_name(raw) == expected

    def test_empty_returns_empty(self) -> None:
        assert normalize_tool_name("") == ""

    def test_leading_trailing_separators_stripped(self) -> None:
        assert normalize_tool_name("__weird__name__") == "weird_name"


class TestValidateToolSchema:
    def test_valid_schema_no_errors(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        assert validate_tool_schema(schema) == []

    def test_unknown_top_level_type(self) -> None:
        errors = validate_tool_schema({"type": "frobnicate"})
        assert any(e.path == "/type" for e in errors)

    def test_properties_must_be_object(self) -> None:
        errors = validate_tool_schema({"properties": ["not", "a", "dict"]})
        assert any(e.path == "/properties" for e in errors)

    def test_property_schema_must_be_object(self) -> None:
        errors = validate_tool_schema({"properties": {"x": "not-a-dict"}})
        assert any(e.path == "/properties/x" for e in errors)

    def test_unknown_property_type(self) -> None:
        errors = validate_tool_schema({"properties": {"x": {"type": "weird"}}})
        assert any("Unknown type" in e.message for e in errors)

    def test_required_must_be_array(self) -> None:
        errors = validate_tool_schema({"required": "query"})
        assert any(e.path == "/required" for e in errors)

    def test_required_references_missing_property(self) -> None:
        errors = validate_tool_schema({"properties": {"a": {"type": "string"}}, "required": ["b"]})
        assert any("not found in properties" in e.message for e in errors)

    def test_items_must_be_object(self) -> None:
        errors = validate_tool_schema({"type": "array", "items": "string"})
        assert any(e.path == "/items" for e in errors)

    def test_items_unknown_type(self) -> None:
        errors = validate_tool_schema({"type": "array", "items": {"type": "nope"}})
        assert any(e.path == "/items/type" for e in errors)


class TestTypeMatches:
    def test_string(self) -> None:
        assert _type_matches("x", "string") is True
        assert _type_matches(5, "string") is False

    def test_integer_rejects_bool(self) -> None:
        # bool is an int subclass in Python but must not satisfy "integer".
        assert _type_matches(5, "integer") is True
        assert _type_matches(True, "integer") is False

    def test_number_accepts_int_and_float(self) -> None:
        assert _type_matches(5, "number") is True
        assert _type_matches(5.0, "number") is True

    def test_boolean(self) -> None:
        assert _type_matches(True, "boolean") is True
        assert _type_matches(1, "boolean") is False

    def test_array_object_null(self) -> None:
        assert _type_matches([], "array") is True
        assert _type_matches({}, "object") is True
        assert _type_matches(None, "null") is True

    def test_unknown_type_passes(self) -> None:
        assert _type_matches("anything", "made-up-type") is True


class TestValidateToolParams:
    def test_all_present_and_typed(self) -> None:
        schema = {"properties": {"q": {"type": "string"}}, "required": ["q"]}
        assert validate_tool_params({"q": "hello"}, schema) == []

    def test_missing_required_param(self) -> None:
        schema = {"properties": {"q": {"type": "string"}}, "required": ["q"]}
        errors = validate_tool_params({}, schema)
        assert len(errors) == 1
        assert "Missing required parameter" in errors[0].message

    def test_type_mismatch_flagged(self) -> None:
        schema = {"properties": {"count": {"type": "integer"}}}
        errors = validate_tool_params({"count": "five"}, schema)
        assert len(errors) == 1
        assert "Type mismatch" in errors[0].message

    def test_extra_params_ignored(self) -> None:
        schema = {"properties": {"q": {"type": "string"}}}
        # 'extra' is not in properties -> not type-checked.
        assert validate_tool_params({"q": "x", "extra": 123}, schema) == []

    def test_non_list_required_ignored(self) -> None:
        assert validate_tool_params({}, {"required": "q"}) == []


class TestToolNormalizer:
    def test_register_returns_normalized_name(self) -> None:
        norm = ToolNormalizer()
        assert norm.register_tool("searchIssues") == "search_issues"

    def test_lookup_both_directions(self) -> None:
        norm = ToolNormalizer()
        norm.register_tool("searchIssues", server_name="gh")
        assert norm.get_normalized_name("searchIssues") == "search_issues"
        assert norm.get_original_name("search_issues") == "searchIssues"

    def test_lookup_unknown_returns_none(self) -> None:
        norm = ToolNormalizer()
        assert norm.get_normalized_name("nope") is None
        assert norm.get_original_name("nope") is None

    def test_tool_count_and_list(self) -> None:
        norm = ToolNormalizer()
        norm.register_tool("a", server_name="s1")
        norm.register_tool("bTool", server_name="s2")
        assert norm.tool_count == 2
        listed = norm.list_tools()
        servers = {entry["server"] for entry in listed}
        assert servers == {"s1", "s2"}

    def test_normalize_call_validates_against_schema(self) -> None:
        norm = ToolNormalizer()
        norm.register_tool(
            "searchIssues",
            server_name="gh",
            schema={"properties": {"q": {"type": "string"}}, "required": ["q"]},
        )
        # missing required 'q' -> one McpToolError.
        name, params, errors = norm.normalize_call("searchIssues", {})
        assert name == "search_issues"
        # params are returned unchanged even when validation fails.
        assert params == {}
        assert len(errors) == 1
        assert errors[0].code == "PARAM_VALIDATION_FAILED"
        assert errors[0].original_name == "searchIssues"

    def test_normalize_call_resolves_by_normalized_name(self) -> None:
        norm = ToolNormalizer()
        norm.register_tool("searchIssues")
        # passing the normalized name still resolves the entry.
        name, _params, errors = norm.normalize_call("search_issues", {})
        assert name == "search_issues"
        assert errors == []

    def test_normalize_call_unregistered_passthrough(self) -> None:
        norm = ToolNormalizer()
        name, params, errors = norm.normalize_call("brandNewTool", {"x": 1})
        assert name == "brand_new_tool"
        assert params == {"x": 1}
        assert errors == []

    def test_normalize_call_success_no_errors(self) -> None:
        norm = ToolNormalizer()
        norm.register_tool("searchIssues", schema={"properties": {"q": {"type": "string"}}, "required": ["q"]})
        _name, _params, errors = norm.normalize_call("searchIssues", {"q": "hello"})
        assert errors == []


class TestMcpToolError:
    def test_to_dict_without_details(self) -> None:
        err = McpToolError(tool_name="t", original_name="T", code="C", message="m")
        d = err.to_dict()
        assert d["tool_name"] == "t"
        assert "details" not in d

    def test_to_dict_with_details(self) -> None:
        err = McpToolError(tool_name="t", original_name="T", code="C", message="m", details={"k": "v"})
        assert err.to_dict()["details"] == {"k": "v"}

    def test_exception_wraps_error(self) -> None:
        err = McpToolError(tool_name="t", original_name="T", code="C", message="boom")
        exc = McpToolException(err)
        assert exc.error is err
        assert str(exc) == "boom"


class TestDecodeToolResult:
    def test_valid_object(self) -> None:
        assert _decode_tool_result('{"a": 1}') == {"a": 1}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            _decode_tool_result("{not json")

    def test_non_object_raises(self) -> None:
        with pytest.raises(ValueError, match="Expected a JSON object"):
            _decode_tool_result("[1, 2, 3]")
