"""Tests for MCP server protocol-version compatibility checking.

Covers ``mcp_version_compat`` end to end:

* ``ParsedVersion.parse`` across semver / date / prefixed / unparseable.
* ``VersionChecker.check`` decision matrix: date >= / < / exact, semver
  major mismatch, minor mismatch (strict vs lenient), patch-compatible,
  and the unknown-version allow path.
* ``check_many`` / ``get_incompatible`` / ``get_result`` / ``all_results``
  result bookkeeping and ``to_dict`` serialisation.

Pure version arithmetic - fully deterministic.
"""

from __future__ import annotations

from bernstein.core.protocols.mcp.mcp_version_compat import (
    CompatLevel,
    ParsedVersion,
    VersionChecker,
)


class TestParsedVersion:
    def test_parse_semver_three_parts(self) -> None:
        pv = ParsedVersion.parse("1.2.3")
        assert pv.parts == (1, 2, 3)
        assert pv.is_date is False

    def test_parse_date_version(self) -> None:
        pv = ParsedVersion.parse("2025-11-05")
        assert pv.parts == (2025, 11, 5)
        assert pv.is_date is True

    def test_parse_v_prefix_stripped(self) -> None:
        pv = ParsedVersion.parse("v2.0")
        assert pv.parts == (2, 0)

    def test_parse_single_number(self) -> None:
        assert ParsedVersion.parse("1").parts == (1,)

    def test_parse_unparseable_empty_parts(self) -> None:
        pv = ParsedVersion.parse("not-a-version")
        assert pv.parts == ()
        assert pv.raw == "not-a-version"

    def test_parse_preserves_raw(self) -> None:
        assert ParsedVersion.parse("  v1.2.3 ").raw == "  v1.2.3 "


class TestDateVersionChecks:
    def test_exact_date_match_compatible(self) -> None:
        checker = VersionChecker(required_version="2025-11-05")
        result = checker.check("gh", "2025-11-05")
        assert result.compatible is True
        assert result.level == CompatLevel.COMPATIBLE
        assert result.message == "Exact version match"

    def test_newer_date_compatible(self) -> None:
        checker = VersionChecker(required_version="2025-11-05")
        result = checker.check("gh", "2026-01-01")
        assert result.compatible is True
        assert result.level == CompatLevel.COMPATIBLE

    def test_older_date_incompatible(self) -> None:
        checker = VersionChecker(required_version="2025-11-05")
        result = checker.check("gh", "2024-01-01")
        assert result.compatible is False
        assert result.level == CompatLevel.INCOMPATIBLE


class TestSemverChecks:
    def test_major_mismatch_incompatible(self) -> None:
        checker = VersionChecker(required_version="2.0.0")
        result = checker.check("srv", "1.5.0")
        assert result.compatible is False
        assert result.level == CompatLevel.INCOMPATIBLE
        assert "Major version mismatch" in result.message

    def test_minor_mismatch_lenient_compatible(self) -> None:
        checker = VersionChecker(required_version="1.5.0", strict=False)
        result = checker.check("srv", "1.2.0")
        assert result.level == CompatLevel.MINOR_MISMATCH
        assert result.compatible is True

    def test_minor_mismatch_strict_incompatible(self) -> None:
        checker = VersionChecker(required_version="1.5.0", strict=True)
        result = checker.check("srv", "1.2.0")
        assert result.level == CompatLevel.MINOR_MISMATCH
        assert result.compatible is False

    def test_higher_minor_is_compatible(self) -> None:
        checker = VersionChecker(required_version="1.2.0")
        result = checker.check("srv", "1.9.0")
        assert result.compatible is True
        assert result.level == CompatLevel.COMPATIBLE

    def test_patch_difference_compatible(self) -> None:
        checker = VersionChecker(required_version="1.2.3")
        result = checker.check("srv", "1.2.99")
        assert result.compatible is True
        assert result.level == CompatLevel.COMPATIBLE

    def test_exact_semver_compatible(self) -> None:
        checker = VersionChecker(required_version="1.2.3")
        assert checker.check("srv", "1.2.3").compatible is True


class TestUnknownVersion:
    def test_unparseable_server_version_allowed(self) -> None:
        checker = VersionChecker(required_version="1.2.3")
        result = checker.check("srv", "garbage")
        assert result.level == CompatLevel.UNKNOWN
        assert result.compatible is True

    def test_unparseable_required_version_allows_all(self) -> None:
        checker = VersionChecker(required_version="garbage")
        result = checker.check("srv", "1.2.3")
        assert result.level == CompatLevel.UNKNOWN
        assert result.compatible is True


class TestResultBookkeeping:
    def test_check_many_returns_one_per_server(self) -> None:
        checker = VersionChecker(required_version="1.2.0")
        results = checker.check_many({"a": "1.3.0", "b": "2.0.0"})
        assert len(results) == 2
        by_name = {r.server_name: r for r in results}
        assert by_name["a"].compatible is True
        assert by_name["b"].compatible is False

    def test_get_incompatible_filters(self) -> None:
        checker = VersionChecker(required_version="2.0.0")
        checker.check("good", "2.1.0")
        checker.check("bad", "1.0.0")
        incompatible = checker.get_incompatible()
        assert [r.server_name for r in incompatible] == ["bad"]

    def test_get_result_returns_last(self) -> None:
        checker = VersionChecker(required_version="1.0.0")
        checker.check("srv", "1.0.0")
        assert checker.get_result("srv") is not None
        assert checker.get_result("missing") is None

    def test_all_results_accumulates(self) -> None:
        checker = VersionChecker(required_version="1.0.0")
        checker.check("a", "1.0.0")
        checker.check("b", "1.0.0")
        assert len(checker.all_results()) == 2

    def test_required_version_property(self) -> None:
        assert VersionChecker(required_version="2025-11-05").required_version == "2025-11-05"

    def test_to_dict_serialisation(self) -> None:
        checker = VersionChecker(required_version="1.2.0", strict=True)
        checker.check("srv", "1.3.0")
        d = checker.to_dict()
        assert d["required_version"] == "1.2.0"
        assert d["strict"] is True
        assert "srv" in d["results"]
        assert d["results"]["srv"]["compatible"] is True

    def test_compat_result_to_dict(self) -> None:
        checker = VersionChecker(required_version="1.0.0")
        result = checker.check("srv", "1.0.0")
        d = result.to_dict()
        assert d["server_name"] == "srv"
        assert d["server_version"] == "1.0.0"
        assert d["level"] == "compatible"
