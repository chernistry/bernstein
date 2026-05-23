"""Spec-as-test layer: derive executable assertions from the feature contract.

The :mod:`bernstein.core.planning.feature_contract` module persists what the
operator promised - a frozen list of features with ``acceptance_steps`` and an
``acceptance_check`` command - at ``.sdd/contract/features.json``. This module
**consumes** that file and turns each feature into one or more
:class:`Assertion` records that can be executed deterministically against a
checkout to detect drift between *what was promised* and *what is currently
true on disk*.

The on-disk contract is the only source of truth. We never invent our own
shadow store; if a feature is not in ``features.json`` it is not asserted.

Assertion kinds (intentionally small - start narrow, grow with evidence):

``file_exists``
    ``target`` is a relative path that must exist.
``import_resolves``
    ``target`` is a Python dotted module path that must import without error
    using a sandboxed ``importlib.import_module`` call.
``regex_in_file``
    ``target`` is ``"path::pattern"``; the file must contain ``pattern`` as a
    Python regex.
``test_passes``
    ``target`` is a shell command (typically ``pytest <selector>``) whose exit
    code must be zero. Subprocess invocation is opt-in via
    :func:`run_assertions`'s ``allow_subprocess`` flag because tests must be
    able to short-circuit it.

Extraction is deterministic: each :class:`Feature` contributes
* one ``test_passes`` assertion derived from ``acceptance_check`` (when set);
* zero or more ``file_exists`` / ``import_resolves`` / ``regex_in_file``
  assertions parsed from the *first verb* of each ``acceptance_steps`` entry
  matching a small recognised grammar (``exists <path>``, ``import <module>``,
  ``contains <path> /<regex>/``).

Steps that do not match any grammar are reported via the ``unparsed`` list on
:class:`AssertionExtractionReport` rather than silently dropped, so a future
upgrade (LLM-augmented extraction, BDD parser, etc.) can target the gap.
"""

from __future__ import annotations

import importlib
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from bernstein.core.defaults import PY_IDENTIFIER_RE_FRAGMENT
from bernstein.core.planning.feature_contract import (
    DEFAULT_CONTRACT_PATH,
    FeatureContract,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bernstein.core.planning.feature_contract import Feature

AssertionKind = Literal["file_exists", "import_resolves", "test_passes", "regex_in_file"]

DEFAULT_PYTEST_OUTPUT = Path("tests/spec/test_plan_contract.py")

_RE_EXISTS = re.compile(r"^\s*(?:file\s+)?exists\s+(?P<path>\S.+?)\s*$", re.IGNORECASE)
_RE_IMPORT = re.compile(
    rf"^\s*import\s+(?P<module>{PY_IDENTIFIER_RE_FRAGMENT}(?:\.{PY_IDENTIFIER_RE_FRAGMENT})*)\s*$",
    re.IGNORECASE,
)
_RE_CONTAINS = re.compile(
    r"^\s*contains\s+(?P<path>\S+?)\s+/(?P<pattern>.+)/\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Assertion:
    """A single executable claim derived from a contract feature.

    Attributes:
        feature_id: The contract :class:`Feature` id this assertion verifies.
        kind: Discriminator selecting the runner branch.
        target: Kind-specific payload (path, module, command, ``path::regex``).
        predicate: Free-form human description for logs and pytest emission.
    """

    feature_id: str
    kind: AssertionKind
    target: str
    predicate: str


@dataclass
class AssertionExtractionReport:
    """Result of parsing a :class:`FeatureContract` into assertions.

    Attributes:
        assertions: Successfully parsed assertions.
        unparsed: Steps that matched no grammar - kept so an upstream layer
            can decide whether to log, escalate, or hand off to an LLM.
        skipped_features: Features with no ``acceptance_check`` and no
            parseable steps; these are essentially un-verifiable today.
    """

    assertions: list[Assertion] = field(default_factory=list[Assertion])
    unparsed: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    skipped_features: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class AssertionResult:
    """Outcome of executing a single :class:`Assertion`."""

    feature_id: str
    kind: AssertionKind
    target: str
    passed: bool
    detail: str


@dataclass
class AssertionReport:
    """Aggregate of :class:`AssertionResult` rows produced by a run."""

    results: list[AssertionResult] = field(default_factory=list[AssertionResult])

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[AssertionResult]:
        return [r for r in self.results if not r.passed]

    def failed_feature_ids(self) -> list[str]:
        return list(dict.fromkeys(r.feature_id for r in self.results if not r.passed))


def load_contract(path: Path = DEFAULT_CONTRACT_PATH) -> FeatureContract | None:
    """Load the feature contract or return None when the file is absent.

    Tampering and schema-version errors propagate from
    :meth:`FeatureContract.load` - they are *not* swallowed because a corrupt
    contract is a louder signal than a missing one.
    """
    if not path.exists():
        return None
    return FeatureContract.load(path)


def extract_assertions(contract: FeatureContract) -> AssertionExtractionReport:
    """Translate every feature in ``contract`` into zero or more assertions."""
    report = AssertionExtractionReport()
    for feature in contract.features:
        before = len(report.assertions)
        _emit_check_assertion(feature, report)
        for step in feature.acceptance_steps:
            _emit_step_assertion(feature, step, report)
        if len(report.assertions) == before:
            report.skipped_features.append(feature.id)
    return report


def _emit_check_assertion(feature: Feature, report: AssertionExtractionReport) -> None:
    check = feature.acceptance_check.strip()
    if not check:
        return
    report.assertions.append(
        Assertion(
            feature_id=feature.id,
            kind="test_passes",
            target=check,
            predicate=f"acceptance_check `{check}` exits 0",
        )
    )


def _emit_step_assertion(feature: Feature, step: str, report: AssertionExtractionReport) -> None:
    if (m := _RE_EXISTS.match(step)) is not None:
        path = m.group("path").strip()
        report.assertions.append(
            Assertion(
                feature_id=feature.id,
                kind="file_exists",
                target=path,
                predicate=f"path {path!r} exists",
            )
        )
        return
    if (m := _RE_IMPORT.match(step)) is not None:
        module = m.group("module").strip()
        report.assertions.append(
            Assertion(
                feature_id=feature.id,
                kind="import_resolves",
                target=module,
                predicate=f"module {module!r} imports cleanly",
            )
        )
        return
    if (m := _RE_CONTAINS.match(step)) is not None:
        path = m.group("path").strip()
        pattern = m.group("pattern")
        report.assertions.append(
            Assertion(
                feature_id=feature.id,
                kind="regex_in_file",
                target=f"{path}::{pattern}",
                predicate=f"file {path!r} matches /{pattern}/",
            )
        )
        return
    report.unparsed.append((feature.id, step))


def run_assertions(
    assertions: Iterable[Assertion],
    repo_root: Path,
    *,
    allow_subprocess: bool = False,
    timeout_s: float = 30.0,
) -> AssertionReport:
    """Execute ``assertions`` against ``repo_root`` and aggregate results.

    ``allow_subprocess`` is False by default so the loop is safe to run inside
    the orchestrator without accidentally shelling out to ``pytest`` from a
    half-cooked contract; callers who want the full behaviour pass True.
    """
    report = AssertionReport()
    for a in assertions:
        report.results.append(_dispatch(a, repo_root, allow_subprocess=allow_subprocess, timeout_s=timeout_s))
    return report


def _dispatch(a: Assertion, repo_root: Path, *, allow_subprocess: bool, timeout_s: float) -> AssertionResult:
    if a.kind == "file_exists":
        return _check_file_exists(a, repo_root)
    if a.kind == "import_resolves":
        return _check_import_resolves(a)
    if a.kind == "regex_in_file":
        return _check_regex_in_file(a, repo_root)
    if a.kind == "test_passes":
        return _check_test_passes(a, repo_root, allow_subprocess=allow_subprocess, timeout_s=timeout_s)
    return AssertionResult(
        feature_id=a.feature_id,
        kind=a.kind,
        target=a.target,
        passed=False,
        detail=f"unknown assertion kind {a.kind!r}",
    )


def _check_file_exists(a: Assertion, repo_root: Path) -> AssertionResult:
    candidate = (repo_root / a.target).resolve()
    ok = candidate.exists()
    return AssertionResult(
        feature_id=a.feature_id,
        kind=a.kind,
        target=a.target,
        passed=ok,
        detail="ok" if ok else f"missing: {candidate}",
    )


def _check_import_resolves(a: Assertion) -> AssertionResult:
    try:
        importlib.import_module(a.target)
    except Exception as exc:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return AssertionResult(feature_id=a.feature_id, kind=a.kind, target=a.target, passed=True, detail="ok")


def _check_regex_in_file(a: Assertion, repo_root: Path) -> AssertionResult:
    if "::" not in a.target:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail="malformed target: expected 'path::pattern'",
        )
    raw_path, pattern = a.target.split("::", 1)
    candidate = (repo_root / raw_path).resolve()
    if not candidate.exists():
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail=f"missing: {candidate}",
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail=f"invalid regex: {exc}",
        )
    text = candidate.read_text(errors="replace")
    ok = bool(compiled.search(text))
    return AssertionResult(
        feature_id=a.feature_id,
        kind=a.kind,
        target=a.target,
        passed=ok,
        detail="ok" if ok else f"no match for /{pattern}/ in {raw_path}",
    )


def _check_test_passes(
    a: Assertion,
    repo_root: Path,
    *,
    allow_subprocess: bool,
    timeout_s: float,
) -> AssertionResult:
    if not allow_subprocess:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail="subprocess execution disabled (allow_subprocess=False)",
        )
    try:
        argv = shlex.split(a.target)
    except ValueError as exc:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail=f"unparseable command: {exc}",
        )
    if not argv:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail="empty acceptance_check",
        )
    try:
        proc = subprocess.run(
            argv,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return AssertionResult(
            feature_id=a.feature_id,
            kind=a.kind,
            target=a.target,
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    ok = proc.returncode == 0
    detail = "ok" if ok else (proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}")[:400]
    return AssertionResult(feature_id=a.feature_id, kind=a.kind, target=a.target, passed=ok, detail=detail)


def apply_results_to_contract(contract: FeatureContract, report: AssertionReport) -> None:
    """Reflect the run outcome onto the contract's mutable per-feature flags.

    A feature flips to ``passes=True`` only when *every* assertion that
    references it succeeds. Any single failure flips it to ``passes=False``.
    Features that produced no assertions are left untouched - they are still
    pending and the upstream layer is expected to surface them via
    :attr:`AssertionExtractionReport.skipped_features`.
    """
    by_feature: dict[str, list[AssertionResult]] = {}
    for r in report.results:
        by_feature.setdefault(r.feature_id, []).append(r)
    for feature_id, results in by_feature.items():
        if all(r.passed for r in results):
            contract.mark_pass(feature_id)
        else:
            contract.mark_fail(feature_id)


def assertions_to_pytest(
    assertions: Iterable[Assertion],
    out_path: Path = DEFAULT_PYTEST_OUTPUT,
) -> Path:
    """Emit a runnable pytest module that re-checks every assertion.

    The emitted file imports back into this module so the pytest run delegates
    to :func:`run_assertions` against the current working directory. We choose
    delegation over inlining the checks because it keeps the bytecode small
    and means a future addition of an assertion kind requires no regeneration
    of the spec test file.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n".join(starmap(_render_pytest_case, enumerate(assertions)))
    body = _PYTEST_HEADER + (rendered or _PYTEST_EMPTY_PLACEHOLDER) + "\n"
    out_path.write_text(body)
    return out_path


def _render_pytest_case(idx: int, a: Assertion) -> str:
    safe_target = a.target.replace("\\", "\\\\").replace('"', '\\"')
    safe_predicate = a.predicate.replace("\\", "\\\\").replace('"', '\\"')
    safe_id = re.sub(r"[^A-Za-z0-9]+", "_", a.feature_id).strip("_") or f"feat_{idx}"
    return (
        f"def test_{safe_id}_{idx}_{a.kind}() -> None:\n"
        f'    """{safe_predicate}."""\n'
        f"    a = Assertion(\n"
        f'        feature_id="{a.feature_id}",\n'
        f'        kind="{a.kind}",\n'
        f'        target="{safe_target}",\n'
        f'        predicate="{safe_predicate}",\n'
        f"    )\n"
        f"    report = run_assertions([a], REPO_ROOT, allow_subprocess=True)\n"
        f"    result = report.results[0]\n"
        f"    assert result.passed, result.detail\n"
    )


_PYTEST_HEADER = (
    '"""Auto-generated from .sdd/contract/features.json - do not edit by hand."""\n\n'
    "from __future__ import annotations\n\n"
    "from pathlib import Path\n\n"
    "from bernstein.core.planning.spec_assertions import Assertion, run_assertions\n\n"
    "REPO_ROOT = Path(__file__).resolve().parents[2]\n\n\n"
)

_PYTEST_EMPTY_PLACEHOLDER = (
    "def test_no_assertions_extracted() -> None:\n"
    '    """Contract produced zero assertions - placeholder so pytest collects the file."""\n'
    "    assert True\n"
)


def verify_contract(
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    repo_root: Path | None = None,
    *,
    allow_subprocess: bool = False,
    apply: bool = False,
) -> tuple[AssertionExtractionReport, AssertionReport] | None:
    """High-level helper: load contract, extract, run, optionally apply.

    Returns ``None`` when the contract file is absent so callers can no-op
    cleanly on featureless plans.
    """
    contract = load_contract(contract_path)
    if contract is None:
        return None
    extraction = extract_assertions(contract)
    run = run_assertions(
        extraction.assertions,
        repo_root or Path.cwd(),
        allow_subprocess=allow_subprocess,
    )
    if apply:
        apply_results_to_contract(contract, run)
        contract.save(contract_path)
    return extraction, run


__all__ = [
    "DEFAULT_PYTEST_OUTPUT",
    "Assertion",
    "AssertionExtractionReport",
    "AssertionKind",
    "AssertionReport",
    "AssertionResult",
    "apply_results_to_contract",
    "assertions_to_pytest",
    "extract_assertions",
    "load_contract",
    "run_assertions",
    "verify_contract",
]
