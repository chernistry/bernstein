"""Tests for architecture conformance checking against declared module boundaries (#682).

Covers BoundaryRule, Violation, ConformanceResult dataclasses, YAML config
loading, AST-based import extraction, single-file checking, project-wide
checking, and Markdown report rendering.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bernstein.core.quality.arch_rules import (
    BoundaryRule,
    ConformanceResult,
    Violation,
    _check_import_against_rule,
    _extract_imports,
    _file_to_module,
    _find_matching_rule,
    check_file_conformance,
    check_project_conformance,
    load_boundary_rules,
    render_conformance_report,
)

# ---------------------------------------------------------------------------
# Dataclass construction and immutability
# ---------------------------------------------------------------------------


class TestBoundaryRule:
    """Test BoundaryRule frozen dataclass."""

    def test_basic_construction(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        assert rule.module == "bernstein.core"
        assert rule.allowed_imports == frozenset()
        assert rule.denied_imports == frozenset()

    def test_with_allowed_imports(self) -> None:
        rule = BoundaryRule(
            module="bernstein.core",
            allowed_imports=frozenset({"bernstein.core", "bernstein.adapters"}),
        )
        assert "bernstein.core" in rule.allowed_imports
        assert "bernstein.adapters" in rule.allowed_imports

    def test_with_denied_imports(self) -> None:
        rule = BoundaryRule(
            module="bernstein.cli",
            denied_imports=frozenset({"bernstein.adapters"}),
        )
        assert "bernstein.adapters" in rule.denied_imports

    def test_frozen(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        with pytest.raises(AttributeError):
            rule.module = "changed"  # type: ignore[misc]


class TestViolation:
    """Test Violation frozen dataclass."""

    def test_construction(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        v = Violation(
            source_file="src/bernstein/core/foo.py",
            source_module="bernstein.core.foo",
            imported_module="bernstein.cli",
            line_number=5,
            rule=rule,
        )
        assert v.source_file == "src/bernstein/core/foo.py"
        assert v.source_module == "bernstein.core.foo"
        assert v.imported_module == "bernstein.cli"
        assert v.line_number == 5
        assert v.rule is rule

    def test_frozen(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        v = Violation(
            source_file="f.py",
            source_module="m",
            imported_module="x",
            line_number=1,
            rule=rule,
        )
        with pytest.raises(AttributeError):
            v.line_number = 99  # type: ignore[misc]


class TestConformanceResult:
    """Test ConformanceResult frozen dataclass."""

    def test_passed_result(self) -> None:
        result = ConformanceResult(violations=(), checked_files=10, passed=True)
        assert result.passed is True
        assert result.checked_files == 10
        assert result.violations == ()

    def test_failed_result(self) -> None:
        rule = BoundaryRule(module="m")
        v = Violation(source_file="f", source_module="m", imported_module="x", line_number=1, rule=rule)
        result = ConformanceResult(violations=(v,), checked_files=5, passed=False)
        assert result.passed is False
        assert len(result.violations) == 1

    def test_frozen(self) -> None:
        result = ConformanceResult(violations=(), checked_files=0, passed=True)
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


class TestLoadBoundaryRules:
    """Test load_boundary_rules() YAML parsing."""

    def test_loads_basic_config(self, tmp_path: Path) -> None:
        config = tmp_path / "boundaries.yaml"
        config.write_text(
            textwrap.dedent("""\
            boundaries:
              - module: bernstein.core
                allowed_imports:
                  - bernstein.core
                  - bernstein.adapters
              - module: bernstein.cli
                denied_imports:
                  - bernstein.adapters
            """),
            encoding="utf-8",
        )
        rules = load_boundary_rules(config)
        assert len(rules) == 2
        assert rules[0].module == "bernstein.core"
        assert rules[0].allowed_imports == frozenset({"bernstein.core", "bernstein.adapters"})
        assert rules[1].module == "bernstein.cli"
        assert rules[1].denied_imports == frozenset({"bernstein.adapters"})

    def test_defaults_to_empty_sets(self, tmp_path: Path) -> None:
        config = tmp_path / "b.yaml"
        config.write_text("boundaries:\n  - module: foo\n", encoding="utf-8")
        rules = load_boundary_rules(config)
        assert rules[0].allowed_imports == frozenset()
        assert rules[0].denied_imports == frozenset()

    def test_missing_boundaries_key_raises(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("modules:\n  - name: x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="boundaries"):
            load_boundary_rules(config)

    def test_missing_module_key_raises(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("boundaries:\n  - allowed_imports: [x]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="module"):
            load_boundary_rules(config)

    def test_non_list_boundaries_raises(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("boundaries: not_a_list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="list"):
            load_boundary_rules(config)

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_boundary_rules(tmp_path / "nonexistent.yaml")

    def test_non_mapping_top_level_raises(self, tmp_path: Path) -> None:
        config = tmp_path / "bad.yaml"
        config.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_boundary_rules(config)


# ---------------------------------------------------------------------------
# AST import extraction
# ---------------------------------------------------------------------------


class TestExtractImports:
    """Test _extract_imports() AST parsing."""

    def test_from_import(self) -> None:
        source = "from bernstein.core import models\n"
        imports = _extract_imports(source)
        assert ("bernstein.core", 1) in imports

    def test_bare_import(self) -> None:
        source = "import os\n"
        imports = _extract_imports(source)
        assert ("os", 1) in imports

    def test_multiple_bare_imports(self) -> None:
        source = "import os, sys\n"
        imports = _extract_imports(source)
        modules = [m for m, _ in imports]
        assert "os" in modules
        assert "sys" in modules

    def test_line_numbers(self) -> None:
        source = "import os\n\nimport sys\n"
        imports = _extract_imports(source)
        line_map = {m: ln for m, ln in imports}
        assert line_map["os"] == 1
        assert line_map["sys"] == 3

    def test_syntax_error_returns_empty(self) -> None:
        source = "def broken(\n"
        imports = _extract_imports(source)
        assert imports == []

    def test_no_imports_returns_empty(self) -> None:
        source = "x = 1\ny = 2\n"
        imports = _extract_imports(source)
        assert imports == []

    def test_relative_from_import_skipped(self) -> None:
        """Relative imports (from . import X) have module=None, should be skipped."""
        source = "from . import sibling\n"
        imports = _extract_imports(source)
        # relative import has node.module = None, so not captured
        assert imports == []


# ---------------------------------------------------------------------------
# File to module conversion
# ---------------------------------------------------------------------------


class TestFileToModule:
    """Test _file_to_module() path-to-module resolution."""

    def test_standard_src_layout(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "bernstein" / "core" / "models.py"
        f.parent.mkdir(parents=True)
        f.touch()
        result = _file_to_module(f, tmp_path)
        assert result == "bernstein.core.models"

    def test_init_file_becomes_package(self, tmp_path: Path) -> None:
        f = tmp_path / "src" / "bernstein" / "core" / "__init__.py"
        f.parent.mkdir(parents=True)
        f.touch()
        result = _file_to_module(f, tmp_path)
        assert result == "bernstein.core"

    def test_no_src_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "bernstein" / "core" / "models.py"
        f.parent.mkdir(parents=True)
        f.touch()
        result = _file_to_module(f, tmp_path)
        assert result == "bernstein.core.models"

    def test_non_python_file_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.touch()
        result = _file_to_module(f, tmp_path)
        assert result is None

    def test_file_outside_project_returns_none(self, tmp_path: Path) -> None:
        other = tmp_path / "other"
        other.mkdir()
        f = other / "mod.py"
        f.touch()
        project = tmp_path / "project"
        project.mkdir()
        result = _file_to_module(f, project)
        assert result is None


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


class TestFindMatchingRule:
    """Test _find_matching_rule() specificity logic."""

    def test_exact_match(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        assert _find_matching_rule("bernstein.core", [rule]) is rule

    def test_prefix_match(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        assert _find_matching_rule("bernstein.core.models", [rule]) is rule

    def test_no_match(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        assert _find_matching_rule("bernstein.cli", [rule]) is None

    def test_most_specific_wins(self) -> None:
        broad = BoundaryRule(module="bernstein")
        narrow = BoundaryRule(module="bernstein.core")
        assert _find_matching_rule("bernstein.core.models", [broad, narrow]) is narrow

    def test_no_partial_component_match(self) -> None:
        """'bernstein.cor' should not match module 'bernstein.core'."""
        rule = BoundaryRule(module="bernstein.core")
        assert _find_matching_rule("bernstein.cor", [rule]) is None


# ---------------------------------------------------------------------------
# Import-against-rule checking
# ---------------------------------------------------------------------------


class TestCheckImportAgainstRule:
    """Test _check_import_against_rule() logic."""

    def test_allowed_import_passes(self) -> None:
        rule = BoundaryRule(
            module="bernstein.core",
            allowed_imports=frozenset({"bernstein.core", "bernstein.adapters"}),
        )
        v = _check_import_against_rule(
            imported_module="bernstein.core.models",
            source_file="f.py",
            source_module="bernstein.core.foo",
            line_number=1,
            rule=rule,
        )
        assert v is None

    def test_denied_import_triggers_violation(self) -> None:
        rule = BoundaryRule(
            module="bernstein.core",
            denied_imports=frozenset({"bernstein.cli"}),
        )
        v = _check_import_against_rule(
            imported_module="bernstein.cli.run",
            source_file="f.py",
            source_module="bernstein.core.foo",
            line_number=3,
            rule=rule,
        )
        assert v is not None
        assert v.imported_module == "bernstein.cli.run"

    def test_allowed_takes_precedence_over_denied(self) -> None:
        rule = BoundaryRule(
            module="bernstein.core",
            allowed_imports=frozenset({"bernstein.core", "bernstein.cli"}),
            denied_imports=frozenset({"bernstein.cli"}),
        )
        v = _check_import_against_rule(
            imported_module="bernstein.cli",
            source_file="f.py",
            source_module="bernstein.core",
            line_number=1,
            rule=rule,
        )
        assert v is None

    def test_no_rules_means_no_violation(self) -> None:
        rule = BoundaryRule(module="bernstein.core")
        v = _check_import_against_rule(
            imported_module="bernstein.cli",
            source_file="f.py",
            source_module="bernstein.core",
            line_number=1,
            rule=rule,
        )
        assert v is None

    def test_unlisted_import_violates_allowlist(self) -> None:
        rule = BoundaryRule(
            module="bernstein.core",
            allowed_imports=frozenset({"bernstein.core"}),
        )
        v = _check_import_against_rule(
            imported_module="bernstein.cli",
            source_file="f.py",
            source_module="bernstein.core",
            line_number=7,
            rule=rule,
        )
        assert v is not None
        assert v.line_number == 7


# ---------------------------------------------------------------------------
# Per-file conformance checking
# ---------------------------------------------------------------------------


class TestCheckFileConformance:
    """Test check_file_conformance() on real files."""

    def _write_py(self, base: Path, rel: str, content: str) -> Path:
        """Helper: write a Python file at base/rel with given content."""
        fp = base / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return fp

    def test_clean_file_no_violations(self, tmp_path: Path) -> None:
        fp = self._write_py(
            tmp_path,
            "src/bernstein/core/models.py",
            "from bernstein.core import utils\n",
        )
        rules = [
            BoundaryRule(
                module="bernstein.core",
                allowed_imports=frozenset({"bernstein.core"}),
            )
        ]
        violations = check_file_conformance(fp, rules, project_root=tmp_path)
        assert violations == []

    def test_violation_detected(self, tmp_path: Path) -> None:
        fp = self._write_py(
            tmp_path,
            "src/bernstein/core/bad.py",
            "from bernstein.cli import run\n",
        )
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli"}),
            )
        ]
        violations = check_file_conformance(fp, rules, project_root=tmp_path)
        assert len(violations) == 1
        assert violations[0].imported_module == "bernstein.cli"
        assert violations[0].source_module == "bernstein.core.bad"

    def test_file_not_in_any_module_returns_empty(self, tmp_path: Path) -> None:
        fp = self._write_py(
            tmp_path,
            "src/other/thing.py",
            "from bernstein.cli import run\n",
        )
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli"}),
            )
        ]
        violations = check_file_conformance(fp, rules, project_root=tmp_path)
        assert violations == []

    def test_multiple_violations_in_one_file(self, tmp_path: Path) -> None:
        fp = self._write_py(
            tmp_path,
            "src/bernstein/core/multi.py",
            "from bernstein.cli import a\nfrom bernstein.adapters import b\n",
        )
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli", "bernstein.adapters"}),
            )
        ]
        violations = check_file_conformance(fp, rules, project_root=tmp_path)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# Project-wide conformance checking
# ---------------------------------------------------------------------------


class TestCheckProjectConformance:
    """Test check_project_conformance() on a project tree."""

    def _write_py(self, base: Path, rel: str, content: str) -> Path:
        fp = base / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return fp

    def test_clean_project(self, tmp_path: Path) -> None:
        self._write_py(tmp_path, "src/bernstein/core/__init__.py", "")
        self._write_py(tmp_path, "src/bernstein/core/models.py", "from bernstein.core import utils\n")
        rules = [
            BoundaryRule(
                module="bernstein.core",
                allowed_imports=frozenset({"bernstein.core"}),
            )
        ]
        result = check_project_conformance(tmp_path, rules)
        assert result.passed is True
        assert result.checked_files >= 2

    def test_project_with_violations(self, tmp_path: Path) -> None:
        self._write_py(tmp_path, "src/bernstein/core/__init__.py", "")
        self._write_py(tmp_path, "src/bernstein/core/bad.py", "from bernstein.cli import x\n")
        self._write_py(tmp_path, "src/bernstein/cli/__init__.py", "")
        self._write_py(tmp_path, "src/bernstein/cli/run.py", "x = 1\n")
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli"}),
            )
        ]
        result = check_project_conformance(tmp_path, rules)
        assert result.passed is False
        assert len(result.violations) == 1
        assert result.checked_files >= 3

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        self._write_py(tmp_path, ".hidden/bad.py", "from bernstein.cli import x\n")
        self._write_py(tmp_path, "src/bernstein/core/ok.py", "x = 1\n")
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli"}),
            )
        ]
        result = check_project_conformance(tmp_path, rules)
        # .hidden should be skipped entirely
        assert result.passed is True

    def test_skips_pycache(self, tmp_path: Path) -> None:
        self._write_py(tmp_path, "src/__pycache__/cached.py", "from bernstein.cli import x\n")
        self._write_py(tmp_path, "src/bernstein/core/ok.py", "x = 1\n")
        rules = [
            BoundaryRule(
                module="bernstein.core",
                denied_imports=frozenset({"bernstein.cli"}),
            )
        ]
        result = check_project_conformance(tmp_path, rules)
        assert result.passed is True

    def test_empty_project(self, tmp_path: Path) -> None:
        rules = [BoundaryRule(module="anything")]
        result = check_project_conformance(tmp_path, rules)
        assert result.passed is True
        assert result.checked_files == 0


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


class TestRenderConformanceReport:
    """Test render_conformance_report() Markdown output."""

    def test_passed_report(self) -> None:
        result = ConformanceResult(violations=(), checked_files=42, passed=True)
        report = render_conformance_report(result)
        assert "PASSED" in report
        assert "42" in report
        assert "violation" not in report.lower() or "no violations" in report.lower()

    def test_failed_report_has_violations(self) -> None:
        rule = BoundaryRule(module="bernstein.core", denied_imports=frozenset({"bernstein.cli"}))
        v = Violation(
            source_file="src/bernstein/core/bad.py",
            source_module="bernstein.core.bad",
            imported_module="bernstein.cli",
            line_number=5,
            rule=rule,
        )
        result = ConformanceResult(violations=(v,), checked_files=10, passed=False)
        report = render_conformance_report(result)
        assert "FAILED" in report
        assert "1 violation" in report
        assert "bernstein.core.bad" in report
        assert "bernstein.cli" in report
        assert "5" in report

    def test_report_groups_by_module(self) -> None:
        rule_core = BoundaryRule(module="bernstein.core")
        rule_cli = BoundaryRule(module="bernstein.cli")
        v1 = Violation(
            source_file="a.py",
            source_module="bernstein.core.a",
            imported_module="x",
            line_number=1,
            rule=rule_core,
        )
        v2 = Violation(
            source_file="b.py",
            source_module="bernstein.cli.b",
            imported_module="y",
            line_number=2,
            rule=rule_cli,
        )
        result = ConformanceResult(violations=(v1, v2), checked_files=5, passed=False)
        report = render_conformance_report(result)
        # Both module headings should appear
        assert "bernstein.core.a" in report
        assert "bernstein.cli.b" in report

    def test_report_is_markdown(self) -> None:
        result = ConformanceResult(violations=(), checked_files=1, passed=True)
        report = render_conformance_report(result)
        assert report.startswith("# ")

    def test_failed_report_contains_table_headers(self) -> None:
        rule = BoundaryRule(module="m")
        v = Violation(source_file="f.py", source_module="m.x", imported_module="bad", line_number=1, rule=rule)
        result = ConformanceResult(violations=(v,), checked_files=1, passed=False)
        report = render_conformance_report(result)
        assert "| File |" in report
        assert "| Line |" in report or "Line" in report
