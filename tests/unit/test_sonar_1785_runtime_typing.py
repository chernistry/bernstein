"""Regression guards for Sonar runtime typing findings in #1785."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEDERATION = PROJECT_ROOT / "src/bernstein/core/orchestration/federation.py"
SCHEDULE_SUPERVISOR = PROJECT_ROOT / "src/bernstein/core/orchestration/schedule_supervisor.py"
AUTH_MIDDLEWARE = PROJECT_ROOT / "src/bernstein/core/security/auth_middleware.py"
PERMISSION_POLICY = PROJECT_ROOT / "src/bernstein/core/security/permission_policy.py"
REMOTE_TRANSPORT = PROJECT_ROOT / "src/bernstein/mcp/remote_transport.py"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return child
    msg = f"function {name!r} not found"
    raise AssertionError(msg)


def _isinstance_check_operands(tree: ast.AST) -> list[ast.AST]:
    operands: list[ast.AST] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) >= 2
        ):
            operands.append(node.args[1])
    return operands


def test_federation_string_tuple_coercion_does_not_runtime_check_typing_iterable() -> None:
    func = _function(_module(FEDERATION), "_to_string_tuple")
    operands = _isinstance_check_operands(func)

    assert not any(isinstance(operand, ast.Name) and operand.id == "Iterable" for operand in operands)


def test_auth_resource_normalisation_does_not_use_union_in_isinstance() -> None:
    func = _function(_module(AUTH_MIDDLEWARE), "_normalise_expected_resource")
    operands = _isinstance_check_operands(func)

    assert not any(isinstance(operand, ast.BinOp) and isinstance(operand.op, ast.BitOr) for operand in operands)


def test_permission_policy_string_tuple_coercion_uses_runtime_object_input() -> None:
    func = _function(_module(PERMISSION_POLICY), "_coerce_str_tuple")
    arg = func.args.args[0]

    assert isinstance(arg.annotation, ast.Name)
    assert arg.annotation.id == "object"


def test_schedule_supervisor_records_misfire_counterfactual_with_single_branch() -> None:
    tick = _function(_module(SCHEDULE_SUPERVISOR), "_tick_one")
    calls = [
        node
        for node in ast.walk(tick)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_record_counterfactual"
    ]

    assert len(calls) == 1


def test_remote_transport_uses_create_task_for_tool_execution() -> None:
    body = REMOTE_TRANSPORT.read_text(encoding="utf-8")

    assert "asyncio.ensure_future(" not in body
    assert "asyncio.create_task(" in body
