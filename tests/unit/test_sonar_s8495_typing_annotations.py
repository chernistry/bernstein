"""Regression coverage for the scoped Sonar S8495 tuple-return findings."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AnnotationExpectation:
    """Expected annotation for one scoped S8495 finding."""

    path: str
    function_path: tuple[str, ...]
    target: str
    expected: str


EXPECTATIONS: tuple[AnnotationExpectation, ...] = (
    AnnotationExpectation(
        path="src/bernstein/core/orchestration/federation.py",
        function_path=("_to_string_tuple",),
        target="return",
        expected="_StringTuple",
    ),
    AnnotationExpectation(
        path="src/bernstein/core/security/auth_middleware.py",
        function_path=("_normalise_expected_resource",),
        target="raw",
        expected="_ExpectedResourceConfig",
    ),
    AnnotationExpectation(
        path="src/bernstein/core/security/permission_policy.py",
        function_path=("_coerce_str_tuple",),
        target="value",
        expected="object",
    ),
)

TUPLE_RETURN_EXPECTATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("src/bernstein/core/orchestration/federation.py", ("_to_string_tuple",)),
    ("src/bernstein/core/security/auth_middleware.py", ("_normalise_expected_resource",)),
    ("src/bernstein/core/security/permission_policy.py", ("_coerce_str_tuple",)),
)


def _source_for(path: str) -> tuple[str, ast.Module]:
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    return source, ast.parse(source)


def _find_function(module: ast.Module, function_path: tuple[str, ...]) -> ast.FunctionDef:
    body: list[ast.stmt] = list(module.body)
    for name in function_path:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
                if isinstance(node, ast.FunctionDef):
                    return node
                body = list(node.body)
                break
        else:  # pragma: no cover - failure path keeps assertion output readable
            raise AssertionError(f"missing function path {'.'.join(function_path)}")
    raise AssertionError(f"function path {'.'.join(function_path)} does not end in a function")


def _annotation_text(source: str, function: ast.FunctionDef, target: str) -> str:
    if target == "return":
        assert function.returns is not None, f"{function.name} must have a return annotation"
        annotation = ast.get_source_segment(source, function.returns)
    else:
        matching_args = [arg for arg in function.args.args if arg.arg == target]
        assert matching_args, f"{function.name} must have a {target!r} argument"
        arg = matching_args[0]
        assert arg.annotation is not None, f"{function.name}.{target} must be annotated"
        annotation = ast.get_source_segment(source, arg.annotation)
    assert annotation is not None
    return annotation


def test_scoped_s8495_function_annotations_are_resolved() -> None:
    """The scoped S8495 findings must not expose unresolved typing forms."""
    for expectation in EXPECTATIONS:
        source, module = _source_for(expectation.path)
        function = _find_function(module, expectation.function_path)
        actual = _annotation_text(source, function, expectation.target)
        assert actual == expectation.expected


def _direct_tuple_return_lengths(function: ast.FunctionDef) -> set[int]:
    lengths: set[int] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Return):
            continue
        if isinstance(node.value, ast.Tuple):
            lengths.add(len(node.value.elts))
    return lengths


def _empty_tuple_constructor_lines(function: ast.FunctionDef) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "tuple" and not node.args and not node.keywords:
            lines.append(node.lineno)
    return lines


def test_scoped_s8495_functions_do_not_return_mixed_tuple_literal_lengths() -> None:
    """The scoped S8495 findings must avoid direct tuple literals with mixed lengths."""
    for path, function_path in TUPLE_RETURN_EXPECTATIONS:
        _source, module = _source_for(path)
        function = _find_function(module, function_path)

        assert len(_direct_tuple_return_lengths(function)) <= 1


def test_scoped_tuple_functions_do_not_call_empty_tuple_constructor() -> None:
    """The scoped S7498 findings must avoid no-arg tuple constructors."""
    for path, function_path in TUPLE_RETURN_EXPECTATIONS:
        _source, module = _source_for(path)
        function = _find_function(module, function_path)

        assert _empty_tuple_constructor_lines(function) == []
