"""Cordon-zone enforcement: only allow specific paths in heal diffs.

The v1 workflow embedded a regex allowlist directly in the YAML. v2
extracts the rule set into Python so the pre-commit hook (also new in
v2) and the workflow share one canonical list, and so the unit tests
can exercise edge cases without parsing YAML.

Cordon allowlist
----------------
* Config / docs files at repo root: ``typos.toml``, ``AGENTS.md``,
  ``CLAUDE.md``, ``.goosehints``, ``CONVENTIONS.md``.
* Cursor rule fragments: ``.cursor/rules/*.mdc``.
* Whitespace-only diffs anywhere under ``src/bernstein/**/*.py`` (the
  ruff-format pass).
* Tests under ``tests/**`` are NOT in the cordon: heal must never edit
  tests on its own. Auto-heal v3 will lift this restriction once the
  counter-example test injector ships.

The whitespace-only carve-out is enforced by the workflow, not this
module - we just expose the allowlist so the pre-commit hook can
decline non-whitespace touches outside the explicit list.

Operator extensions
-------------------
``BERNSTEIN_AUTOHEAL_CORDON_EXTRA`` is a colon-separated env var that
adds *exact* paths to :data:`CORDON_EXACT` at evaluation time. This
lets a repo carry per-fork additions (e.g. a custom typo allowlist
file) without forking this module. The env is read lazily so tests
can scope the override with ``monkeypatch``.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

CORDON_EXACT: Final[frozenset[str]] = frozenset(
    {
        "typos.toml",
        "AGENTS.md",
        "CLAUDE.md",
        ".goosehints",
        "CONVENTIONS.md",
    }
)

CORDON_GLOBS: Final[tuple[str, ...]] = (".cursor/rules/*.mdc",)

ENV_CORDON_EXTRA: Final[str] = "BERNSTEIN_AUTOHEAL_CORDON_EXTRA"
"""Colon-separated extra exact-allow paths, e.g. ``"extra/typos.lst:CODEOWNERS"``."""


def _extra_exact() -> frozenset[str]:
    """Resolve the operator-extended exact-allow set."""
    raw = os.environ.get(ENV_CORDON_EXTRA, "").strip()
    if not raw:
        return frozenset()
    parts = [p.strip() for p in raw.split(":") if p.strip()]
    return frozenset(parts)


WHITESPACE_OK_GLOBS: Final[tuple[str, ...]] = (
    "src/bernstein/**/*.py",
    "tests/**/*.py",
    "scripts/**/*.py",
)


@dataclass(frozen=True, slots=True)
class CordonDecision:
    """Cordon evaluation result for one file path."""

    path: str
    allowed: bool
    rule: str


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a ``**``-aware glob into a compiled regex.

    fnmatch alone treats ``**`` like a single ``*`` -- we want it to
    match arbitrarily-many segments. The translation here mirrors
    git's pathspec ``**`` semantics: a ``**/`` prefix matches zero or
    more directory components.

    Concretely, ``src/bernstein/**/*.py`` becomes::

        \\Asrc/bernstein/(?:.*/)?[^/]*\\.py\\Z

    matching both ``src/bernstein/foo.py`` and
    ``src/bernstein/sub/dir/foo.py``.
    """
    # Lex into a list of tokens, where ``**`` is its own token. Then
    # interleave with literal ``/`` separators.
    tokens: list[str] = []
    for raw in pattern.split("/"):
        if raw == "**":
            tokens.append("**")
        else:
            piece = raw.replace(".", r"\.").replace("*", "[^/]*").replace("?", "[^/]")
            tokens.append(piece)

    out: list[str] = []
    for i, tok in enumerate(tokens):
        if tok == "**":
            # zero-or-more components: a sequence of "<comp>/" any
            # number of times.
            out.append("(?:[^/]+/)*")
            continue
        out.append(tok)
        if i != len(tokens) - 1:
            out.append("/")
    body = "".join(out)
    return re.compile(r"\A" + body + r"\Z")


_WS_OK_REGEXES: tuple[re.Pattern[str], ...] = tuple(_glob_to_regex(p) for p in WHITESPACE_OK_GLOBS)


def evaluate(path: str, *, whitespace_only: bool = False) -> CordonDecision:
    """Return whether ``path`` is allowed under the cordon.

    Args:
        path: Repo-relative POSIX path.
        whitespace_only: True iff the diff for this file is whitespace-only.

    Rule:
        * Exact-allow paths in :data:`CORDON_EXACT` -> allowed.
        * Glob-allow paths in :data:`CORDON_GLOBS` -> allowed.
        * Anything else allowed only if it matches one of
          :data:`WHITESPACE_OK_GLOBS` AND ``whitespace_only`` is True.
        * Otherwise -> rejected.
    """
    norm = str(PurePosixPath(path))
    if norm in CORDON_EXACT:
        return CordonDecision(path=norm, allowed=True, rule="cordon_exact")
    if norm in _extra_exact():
        return CordonDecision(path=norm, allowed=True, rule="cordon_exact_env")
    for pattern in CORDON_GLOBS:
        if fnmatch.fnmatchcase(norm, pattern):
            return CordonDecision(path=norm, allowed=True, rule=f"cordon_glob:{pattern}")
    for pattern, regex in zip(WHITESPACE_OK_GLOBS, _WS_OK_REGEXES, strict=True):
        if regex.match(norm):
            if whitespace_only:
                return CordonDecision(
                    path=norm,
                    allowed=True,
                    rule=f"whitespace_only:{pattern}",
                )
            return CordonDecision(
                path=norm,
                allowed=False,
                rule=f"non_whitespace_in_protected:{pattern}",
            )
    return CordonDecision(path=norm, allowed=False, rule="not_in_cordon")


def evaluate_many(
    paths: list[str],
    whitespace_only_paths: set[str] | None = None,
) -> list[CordonDecision]:
    """Vectorised :func:`evaluate` over a list of paths."""
    ws = whitespace_only_paths if whitespace_only_paths is not None else set()
    return [evaluate(p, whitespace_only=p in ws) for p in paths]


__all__ = [
    "CORDON_EXACT",
    "CORDON_GLOBS",
    "ENV_CORDON_EXTRA",
    "WHITESPACE_OK_GLOBS",
    "CordonDecision",
    "evaluate",
    "evaluate_many",
]
