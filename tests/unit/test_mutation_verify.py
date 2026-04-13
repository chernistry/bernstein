"""Unit tests for test mutation verification module."""

from __future__ import annotations

import subprocess as sp
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from bernstein.core.quality.test_mutation_verify import (
    MutantOutcome,
    MutationVerifyConfig,
    VerificationResult,
    generate_simple_mutants,
    render_verification_report,
    run_test_against_mutant,
    verify_agent_tests,
)

# ---------------------------------------------------------------------------
# MutationVerifyConfig
# ---------------------------------------------------------------------------


class TestMutationVerifyConfig:
    def test_defaults(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("src/mod.py"),
            test_file=Path("tests/test_mod.py"),
        )
        assert cfg.min_kill_rate == pytest.approx(0.7)
        assert cfg.timeout_per_mutant_s == 30

    def test_custom_values(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("a.py"),
            test_file=Path("b.py"),
            min_kill_rate=0.9,
            timeout_per_mutant_s=60,
        )
        assert cfg.min_kill_rate == pytest.approx(0.9)
        assert cfg.timeout_per_mutant_s == 60

    def test_frozen(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("a.py"),
            test_file=Path("b.py"),
        )
        with pytest.raises(AttributeError):
            cfg.min_kill_rate = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MutantOutcome
# ---------------------------------------------------------------------------


class TestMutantOutcome:
    def test_creation(self) -> None:
        outcome = MutantOutcome(
            line=10,
            mutation_type="compare_swap(Eq->NotEq)",
            killed=True,
            original_code="if x == 1:",
            mutated_code="if x != 1:",
        )
        assert outcome.line == 10
        assert outcome.killed is True

    def test_frozen(self) -> None:
        outcome = MutantOutcome(
            line=1,
            mutation_type="t",
            killed=False,
            original_code="a",
            mutated_code="b",
        )
        with pytest.raises(AttributeError):
            outcome.killed = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------


class TestVerificationResult:
    def test_creation(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("s.py"),
            test_file=Path("t.py"),
        )
        result = VerificationResult(
            config=cfg,
            total_mutants=10,
            killed=8,
            survived=2,
            kill_rate=0.8,
            passed=True,
            outcomes=(),
        )
        assert result.total_mutants == 10
        assert result.passed is True
        assert result.outcomes == ()

    def test_frozen(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("s.py"),
            test_file=Path("t.py"),
        )
        result = VerificationResult(
            config=cfg,
            total_mutants=0,
            killed=0,
            survived=0,
            kill_rate=1.0,
            passed=True,
        )
        with pytest.raises(AttributeError):
            result.passed = False  # type: ignore[misc]

    def test_default_outcomes(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("s.py"),
            test_file=Path("t.py"),
        )
        result = VerificationResult(
            config=cfg,
            total_mutants=0,
            killed=0,
            survived=0,
            kill_rate=1.0,
            passed=True,
        )
        assert result.outcomes == ()


# ---------------------------------------------------------------------------
# generate_simple_mutants
# ---------------------------------------------------------------------------


class TestGenerateSimpleMutants:
    def test_compare_swap(self) -> None:
        source = textwrap.dedent("""\
            def f(x):
                if x == 1:
                    return True
        """)
        mutants = generate_simple_mutants(source)
        compare_mutants = [m for m in mutants if m.mutation_type.startswith("compare_swap")]
        assert len(compare_mutants) >= 1
        assert any("!=" in m.mutated_source for m in compare_mutants)

    def test_bool_negate(self) -> None:
        source = textwrap.dedent("""\
            def check():
                return True
        """)
        mutants = generate_simple_mutants(source)
        bool_mutants = [m for m in mutants if m.mutation_type.startswith("bool_negate")]
        assert len(bool_mutants) >= 1
        assert any("False" in m.mutated_source for m in bool_mutants)

    def test_return_remove(self) -> None:
        source = textwrap.dedent("""\
            def compute(x):
                return x * 2
        """)
        mutants = generate_simple_mutants(source)
        return_mutants = [m for m in mutants if m.mutation_type == "return_remove"]
        assert len(return_mutants) >= 1
        assert any("None" in m.mutated_source for m in return_mutants)

    def test_arith_swap(self) -> None:
        source = textwrap.dedent("""\
            def add(a, b):
                return a + b
        """)
        mutants = generate_simple_mutants(source)
        arith_mutants = [m for m in mutants if m.mutation_type.startswith("arith_swap")]
        assert len(arith_mutants) >= 1
        assert any("-" in m.mutated_source for m in arith_mutants)

    def test_mult_to_div(self) -> None:
        source = textwrap.dedent("""\
            def scale(x):
                return x * 3
        """)
        mutants = generate_simple_mutants(source)
        arith_mutants = [m for m in mutants if "Mult" in m.mutation_type]
        assert len(arith_mutants) >= 1

    def test_empty_source_no_mutants(self) -> None:
        mutants = generate_simple_mutants("")
        assert mutants == []

    def test_syntax_error_returns_empty(self) -> None:
        mutants = generate_simple_mutants("def broken(")
        assert mutants == []

    def test_line_numbers_positive(self) -> None:
        source = textwrap.dedent("""\
            def f():
                x = 1 + 2
                return x == 3
        """)
        mutants = generate_simple_mutants(source)
        assert all(m.line > 0 for m in mutants)

    def test_multiple_mutation_types(self) -> None:
        source = textwrap.dedent("""\
            def example(a, b):
                if a == b:
                    return a + b
                return False
        """)
        mutants = generate_simple_mutants(source)
        types = {m.mutation_type.split("(")[0] for m in mutants}
        assert "compare_swap" in types
        assert "arith_swap" in types
        assert "bool_negate" in types
        assert "return_remove" in types

    def test_original_snippet_populated(self) -> None:
        source = textwrap.dedent("""\
            def f(x):
                return x + 1
        """)
        mutants = generate_simple_mutants(source)
        assert len(mutants) > 0
        for m in mutants:
            assert m.original_snippet != ""

    def test_mutated_snippet_differs_from_original(self) -> None:
        source = textwrap.dedent("""\
            def f():
                return True
        """)
        mutants = generate_simple_mutants(source)
        bool_mutants = [m for m in mutants if m.mutation_type.startswith("bool_negate")]
        assert len(bool_mutants) >= 1
        # The mutated snippet should differ from original
        for m in bool_mutants:
            assert m.mutated_source != source

    def test_lt_to_gte_swap(self) -> None:
        source = textwrap.dedent("""\
            def cmp(a, b):
                return a < b
        """)
        mutants = generate_simple_mutants(source)
        lt_mutants = [m for m in mutants if "Lt" in m.mutation_type]
        assert len(lt_mutants) >= 1

    def test_no_mutation_for_plain_assignment(self) -> None:
        source = textwrap.dedent("""\
            x = 42
        """)
        mutants = generate_simple_mutants(source)
        # No comparisons, bools, returns, or arithmetic — zero mutants
        assert mutants == []


# ---------------------------------------------------------------------------
# run_test_against_mutant (mocked subprocess)
# ---------------------------------------------------------------------------


class TestRunTestAgainstMutant:
    def test_killed_when_tests_fail(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("original", encoding="utf-8")
        test_file = tmp_path / "test_module.py"
        test_file.write_text("pass", encoding="utf-8")

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            killed = run_test_against_mutant(test_file, "mutated", src, timeout=10)

        assert killed is True
        assert src.read_text(encoding="utf-8") == "original"

    def test_survived_when_tests_pass(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("original", encoding="utf-8")
        test_file = tmp_path / "test_module.py"
        test_file.write_text("pass", encoding="utf-8")

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            killed = run_test_against_mutant(test_file, "mutated", src, timeout=10)

        assert killed is False
        assert src.read_text(encoding="utf-8") == "original"

    def test_killed_on_timeout(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("original", encoding="utf-8")
        test_file = tmp_path / "test_module.py"
        test_file.write_text("pass", encoding="utf-8")

        with patch(
            "bernstein.core.quality.test_mutation_verify.subprocess.run",
            side_effect=sp.TimeoutExpired("cmd", 5),
        ):
            killed = run_test_against_mutant(test_file, "mutated", src, timeout=5)

        assert killed is True
        assert src.read_text(encoding="utf-8") == "original"

    def test_restores_on_os_error(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("original", encoding="utf-8")
        test_file = tmp_path / "test_module.py"
        test_file.write_text("pass", encoding="utf-8")

        with patch(
            "bernstein.core.quality.test_mutation_verify.subprocess.run",
            side_effect=OSError("disk full"),
        ):
            killed = run_test_against_mutant(test_file, "mutated", src, timeout=5)

        assert killed is True
        assert src.read_text(encoding="utf-8") == "original"

    def test_mutant_code_written_during_execution(self, tmp_path: Path) -> None:
        src = tmp_path / "module.py"
        src.write_text("original", encoding="utf-8")
        test_file = tmp_path / "test_module.py"
        test_file.write_text("pass", encoding="utf-8")

        written_content: list[str] = []

        def capture_run(*_args: object, **_kwargs: object) -> object:
            written_content.append(src.read_text(encoding="utf-8"))

            class FakeResult:
                returncode = 1

            return FakeResult()

        with patch(
            "bernstein.core.quality.test_mutation_verify.subprocess.run",
            side_effect=capture_run,
        ):
            run_test_against_mutant(test_file, "mutated code", src, timeout=10)

        assert written_content == ["mutated code"]
        # Original restored after run
        assert src.read_text(encoding="utf-8") == "original"


# ---------------------------------------------------------------------------
# verify_agent_tests (mocked subprocess)
# ---------------------------------------------------------------------------


class TestVerifyAgentTests:
    def _make_files(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a source file and test file for testing."""
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
                def add(a, b):
                    return a + b
            """),
            encoding="utf-8",
        )
        test = tmp_path / "test_mod.py"
        test.write_text("pass", encoding="utf-8")
        return src, test

    def test_all_killed_passes(self, tmp_path: Path) -> None:
        src, test = self._make_files(tmp_path)

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = verify_agent_tests(src, test, tmp_path)

        assert result.total_mutants > 0
        assert result.killed == result.total_mutants
        assert result.survived == 0
        assert result.kill_rate == pytest.approx(1.0)
        assert result.passed is True

    def test_none_killed_fails(self, tmp_path: Path) -> None:
        src, test = self._make_files(tmp_path)

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = verify_agent_tests(src, test, tmp_path)

        assert result.total_mutants > 0
        assert result.killed == 0
        assert result.survived == result.total_mutants
        assert result.kill_rate == pytest.approx(0.0)
        assert result.passed is False

    def test_partial_kill_rate(self, tmp_path: Path) -> None:
        src, test = self._make_files(tmp_path)
        call_count = 0

        def alternating_run(*_args: object, **_kwargs: object) -> object:
            nonlocal call_count
            call_count += 1

            class FakeResult:
                returncode = 1 if call_count % 2 == 1 else 0

            return FakeResult()

        with patch(
            "bernstein.core.quality.test_mutation_verify.subprocess.run",
            side_effect=alternating_run,
        ):
            result = verify_agent_tests(src, test, tmp_path)

        assert result.total_mutants > 0
        assert 0 < result.kill_rate < 1.0
        assert result.killed + result.survived == result.total_mutants

    def test_missing_source_returns_empty_pass(self, tmp_path: Path) -> None:
        test = tmp_path / "test_mod.py"
        test.write_text("pass", encoding="utf-8")
        missing_src = tmp_path / "nonexistent.py"

        result = verify_agent_tests(missing_src, test, tmp_path)

        assert result.total_mutants == 0
        assert result.passed is True

    def test_missing_test_returns_empty_fail(self, tmp_path: Path) -> None:
        src = tmp_path / "mod.py"
        src.write_text("x = 1 + 2\n", encoding="utf-8")
        missing_test = tmp_path / "nonexistent_test.py"

        result = verify_agent_tests(src, missing_test, tmp_path)

        assert result.total_mutants == 0
        assert result.passed is False

    def test_custom_min_kill_rate(self, tmp_path: Path) -> None:
        src, test = self._make_files(tmp_path)

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = verify_agent_tests(src, test, tmp_path, min_kill_rate=0.95)

        assert result.config.min_kill_rate == pytest.approx(0.95)
        assert result.passed is True

    def test_outcomes_populated(self, tmp_path: Path) -> None:
        src, test = self._make_files(tmp_path)

        with patch("bernstein.core.quality.test_mutation_verify.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = verify_agent_tests(src, test, tmp_path)

        assert len(result.outcomes) == result.total_mutants
        assert all(isinstance(o, MutantOutcome) for o in result.outcomes)

    def test_no_mutants_from_trivial_source(self, tmp_path: Path) -> None:
        src = tmp_path / "trivial.py"
        src.write_text("x = 42\n", encoding="utf-8")
        test = tmp_path / "test_trivial.py"
        test.write_text("pass", encoding="utf-8")

        result = verify_agent_tests(src, test, tmp_path)

        assert result.total_mutants == 0
        assert result.passed is True


# ---------------------------------------------------------------------------
# render_verification_report
# ---------------------------------------------------------------------------


class TestRenderVerificationReport:
    def _make_result(
        self,
        *,
        kill_rate: float = 0.75,
        min_kill_rate: float = 0.7,
        killed: int = 3,
        survived: int = 1,
    ) -> VerificationResult:
        cfg = MutationVerifyConfig(
            source_file=Path("src/mod.py"),
            test_file=Path("tests/test_mod.py"),
            min_kill_rate=min_kill_rate,
        )
        outcomes: list[MutantOutcome] = []
        for i in range(killed):
            outcomes.append(
                MutantOutcome(
                    line=i + 1,
                    mutation_type="compare_swap(Eq->NotEq)",
                    killed=True,
                    original_code="if x == 1:",
                    mutated_code="if x != 1:",
                )
            )
        for i in range(survived):
            outcomes.append(
                MutantOutcome(
                    line=100 + i,
                    mutation_type="return_remove",
                    killed=False,
                    original_code="return x + 1",
                    mutated_code="return None",
                )
            )
        return VerificationResult(
            config=cfg,
            total_mutants=killed + survived,
            killed=killed,
            survived=survived,
            kill_rate=kill_rate,
            passed=kill_rate >= min_kill_rate,
            outcomes=tuple(outcomes),
        )

    def test_contains_kill_rate(self) -> None:
        md = render_verification_report(self._make_result())
        assert "75%" in md

    def test_shows_pass_when_above_threshold(self) -> None:
        md = render_verification_report(self._make_result(kill_rate=0.90, min_kill_rate=0.70))
        assert "PASS" in md

    def test_shows_fail_when_below_threshold(self) -> None:
        md = render_verification_report(self._make_result(kill_rate=0.50, min_kill_rate=0.70))
        assert "FAIL" in md

    def test_surviving_mutants_section(self) -> None:
        md = render_verification_report(self._make_result(survived=2))
        assert "Surviving Mutants" in md
        assert "return_remove" in md

    def test_killed_mutants_section(self) -> None:
        md = render_verification_report(self._make_result(killed=3))
        assert "Killed Mutants" in md

    def test_no_surviving_section_when_all_killed(self) -> None:
        md = render_verification_report(self._make_result(killed=4, survived=0, kill_rate=1.0))
        assert "Surviving Mutants" not in md

    def test_source_and_test_paths_in_report(self) -> None:
        md = render_verification_report(self._make_result())
        assert "src/mod.py" in md
        assert "tests/test_mod.py" in md

    def test_threshold_in_report(self) -> None:
        md = render_verification_report(self._make_result(min_kill_rate=0.80))
        assert "80%" in md

    def test_pipe_escaping_in_table(self) -> None:
        cfg = MutationVerifyConfig(
            source_file=Path("s.py"),
            test_file=Path("t.py"),
        )
        outcome = MutantOutcome(
            line=1,
            mutation_type="test",
            killed=False,
            original_code="x | y",
            mutated_code="x | z",
        )
        result = VerificationResult(
            config=cfg,
            total_mutants=1,
            killed=0,
            survived=1,
            kill_rate=0.0,
            passed=False,
            outcomes=(outcome,),
        )
        md = render_verification_report(result)
        # Pipes should be escaped
        assert "\\|" in md
