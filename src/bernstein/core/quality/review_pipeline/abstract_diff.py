"""Intent-summary + pseudocode abstraction over raw PR diffs.

Reviewing AI-generated diffs line-by-line is the bottleneck once
``bernstein run`` is shipping multiple PRs per hour. This module turns a
unified diff plus a task description into a per-file intent summary
(1-3 bullets) and a pseudocode block for changed Python functions, so
reviewers see *what* changed at the behavioural level with drill-down
to the raw diff via a collapsible GitHub ``<details>`` block.

Pattern source: ``nibzard/awesome-agentic-patterns`` —
``abstracted-code-representation-for-review``.

Public API:

* :class:`IntentSummary` — per-file abstracted view.
* :class:`TaskContext` — minimal task metadata fed to the summariser.
* :func:`summarize_diff` — async, calls the cheap-tier LLM per file.
* :func:`pseudo_for_function` — AST-walked pseudocode for a single
  Python function.
* :func:`render_pr_body` — markdown wrapper used by ``pr_gen``.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from bernstein.core.defaults import ABSTRACT_DIFF_ENABLED, ABSTRACT_DIFF_MAX_FILES
from bernstein.core.llm import call_llm
from bernstein.core.quality.cross_model_verifier import (
    _MAX_TOKENS,
    _PROVIDER,
    select_reviewer_model,
)

logger = logging.getLogger(__name__)


LLMCaller = Callable[..., Awaitable[str]]


_PSEUDOCODE_INDENT: str = "    "
_MAX_BULLETS_PER_FILE: int = 3
_MAX_DIFF_CHARS_PER_FILE: int = 3_000
_OPUS_MARKER: str = "opus"


_SUMMARY_PROMPT_TEMPLATE: str = """\
You are summarising a single file's diff for a human reviewer.
Give a *behavioural* summary — what the change accomplishes — not a
line-by-line restatement.

## Task
**Title:** {title}
**Description:**
{description}

## File: {path}

```diff
{diff}
```

Return ONLY a JSON object with these fields:
{{
  "bullets": ["1-3 short bullets, each one sentence"],
  "confidence": 0.0
}}

Bullets describe the *intent* (e.g. "switches sort to quicksort for
N>32"), never raw line counts. ``confidence`` is between 0 and 1.
No markdown fences. No prose around the JSON.
"""


@dataclass(frozen=True)
class TaskContext:
    """Minimal task metadata threaded into the summary prompt.

    Attributes:
        title: Short subject line.
        description: Longer goal / acceptance criteria.
        writer_model: Model that authored the diff. Used only to pick a
            *different* reviewer via the cascade router.
    """

    title: str
    description: str = ""
    writer_model: str = ""


@dataclass(frozen=True)
class IntentSummary:
    """Abstracted view of a single file's diff.

    Attributes:
        path: Repo-relative file path the summary describes.
        bullet_points: 1-3 sentence bullets describing the intent. Empty
            when the LLM call failed or returned unparseable output.
        pseudocode_blocks: Pseudocode for changed Python functions
            (one block per function), AST-walked from the post-diff
            source text. Empty for non-Python files.
        raw_diff_link: GitHub anchor / URL pointing at the raw diff for
            drill-down. May be empty when no link is available.
        confidence: Reviewer-reported confidence in ``[0, 1]``. ``0``
            when the call failed or no number was returned.
    """

    path: str
    bullet_points: tuple[str, ...] = ()
    pseudocode_blocks: tuple[str, ...] = ()
    raw_diff_link: str = ""
    confidence: float = 0.0


@dataclass(frozen=True)
class _FileDiff:
    path: str
    body: str
    post_image: str = ""
    pseudocode: tuple[str, ...] = field(default_factory=tuple)


def _split_unified_diff(diff: str) -> list[_FileDiff]:
    """Split a unified diff into per-file blocks.

    Only ``diff --git`` headers are recognised — that is what ``git diff``
    emits. The post-image (post-change source) is reconstructed from
    ``+`` lines so :func:`pseudo_for_function` can run against syntactically
    valid Python where possible.
    """
    if not diff.strip():
        return []

    files: list[_FileDiff] = []
    current_path: str | None = None
    current_lines: list[str] = []
    post_lines: list[str] = []

    def _flush() -> None:
        if current_path is None:
            return
        body = "\n".join(current_lines)
        post = "\n".join(post_lines)
        files.append(_FileDiff(path=current_path, body=body, post_image=post))

    for raw_line in diff.splitlines():
        if raw_line.startswith("diff --git "):
            _flush()
            current_path = _extract_path(raw_line)
            current_lines = [raw_line]
            post_lines = []
            continue
        if current_path is None:
            continue
        current_lines.append(raw_line)
        if (raw_line.startswith("+") and not raw_line.startswith("+++")) or raw_line.startswith(" "):
            post_lines.append(raw_line[1:])

    _flush()
    return files


def _extract_path(header: str) -> str:
    """Pull the b-side path from a ``diff --git a/x b/x`` header."""
    parts = header.split()
    for token in parts:
        if token.startswith("b/"):
            return token[2:]
    return parts[-1] if parts else "unknown"


def pseudo_for_function(func_src: str) -> str:
    """Return a pseudocode rendering of a Python function.

    Walks the AST and emits one statement per significant node — control
    flow keywords, returns, assignments, and calls — collapsing bodies to
    pseudocode. Non-functions and unparseable input return an empty
    string so callers can skip them silently.

    Args:
        func_src: Source text of a single function definition.
    """
    src = func_src.strip()
    if not src:
        return ""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""

    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    func = tree.body[0]
    args = ", ".join(a.arg for a in func.args.args)
    header = f"function {func.name}({args}):"
    body = _render_block(func.body, depth=1)
    return header + "\n" + body if body else header + "\n" + _PSEUDOCODE_INDENT + "pass"


def _render_block(stmts: list[ast.stmt], *, depth: int) -> str:
    pad = _PSEUDOCODE_INDENT * depth
    out: list[str] = []
    for node in stmts:
        rendered = _render_stmt(node, depth=depth)
        if rendered:
            out.append(pad + rendered)
    return "\n".join(out)


def _render_stmt(node: ast.stmt, *, depth: int) -> str:
    if isinstance(node, ast.If):
        cond = _safe_unparse(node.test)
        then = _render_block(node.body, depth=depth + 1)
        out = f"if {cond}:"
        if then:
            out += "\n" + then
        if node.orelse:
            else_block = _render_block(node.orelse, depth=depth + 1)
            out += "\n" + _PSEUDOCODE_INDENT * depth + "else:"
            if else_block:
                out += "\n" + else_block
        return out
    if isinstance(node, (ast.For, ast.AsyncFor)):
        target = _safe_unparse(node.target)
        it = _safe_unparse(node.iter)
        body = _render_block(node.body, depth=depth + 1)
        return f"for {target} in {it}:" + ("\n" + body if body else "")
    if isinstance(node, ast.While):
        cond = _safe_unparse(node.test)
        body = _render_block(node.body, depth=depth + 1)
        return f"while {cond}:" + ("\n" + body if body else "")
    if isinstance(node, ast.Try):
        body = _render_block(node.body, depth=depth + 1)
        return "try:" + ("\n" + body if body else "")
    if isinstance(node, ast.Return):
        return f"return {_safe_unparse(node.value)}" if node.value is not None else "return"
    if isinstance(node, (ast.Raise,)):
        return f"raise {_safe_unparse(node.exc)}" if node.exc is not None else "raise"
    if isinstance(node, ast.Assign):
        targets = ", ".join(_safe_unparse(t) for t in node.targets)
        return f"{targets} = {_safe_unparse(node.value)}"
    if isinstance(node, ast.AugAssign):
        return f"{_safe_unparse(node.target)} {type(node.op).__name__.lower()}= {_safe_unparse(node.value)}"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return f"call {_safe_unparse(node.value)}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"define inner function {node.name}"
    if isinstance(node, ast.ClassDef):
        return f"define class {node.name}"
    return ""


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _extract_post_functions(post_image: str) -> tuple[str, ...]:
    """AST-walk the post-image and return pseudocode for each function."""
    if not post_image.strip():
        return ()
    try:
        tree = ast.parse(post_image)
    except SyntaxError:
        return ()
    blocks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                src = ast.unparse(node)
            except (AttributeError, ValueError):
                continue
            pseudo = pseudo_for_function(src)
            if pseudo:
                blocks.append(pseudo)
    return tuple(blocks)


def _disallow_opus(model: str) -> str:
    if _OPUS_MARKER in model.lower():
        logger.info("abstract_diff: opus tier disallowed (%s) — falling back to gemini-flash", model)
        return "google/gemini-flash-1.5"
    return model


def _build_summary_prompt(file: _FileDiff, ctx: TaskContext) -> str:
    body = file.body
    if len(body) > _MAX_DIFF_CHARS_PER_FILE:
        body = body[:_MAX_DIFF_CHARS_PER_FILE] + "\n... (truncated)"
    return _SUMMARY_PROMPT_TEMPLATE.format(
        title=ctx.title,
        description=ctx.description[:1500],
        path=file.path,
        diff=body,
    )


def _parse_summary(raw: str) -> tuple[tuple[str, ...], float]:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    data: dict[str, object] = {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(text[start:end])
    if not data:
        return (), 0.0
    bullets_raw: object = data.get("bullets", [])
    bullets: list[str] = []
    if isinstance(bullets_raw, list):
        bullets = [str(item).strip() for item in cast("list[object]", bullets_raw) if str(item).strip()]
    bullets = bullets[:_MAX_BULLETS_PER_FILE]
    conf_raw: object = data.get("confidence", 0.0)
    try:
        confidence = float(cast("float | str | int", conf_raw))
    except (TypeError, ValueError):
        confidence = 0.0
    return tuple(bullets), max(0.0, min(1.0, confidence))


async def _summarize_one(
    file: _FileDiff,
    ctx: TaskContext,
    *,
    model: str,
    llm_caller: LLMCaller,
    provider: str,
    max_tokens: int,
    raw_diff_link: str,
) -> IntentSummary:
    prompt = _build_summary_prompt(file, ctx)
    try:
        raw = await llm_caller(
            prompt=prompt,
            model=model,
            provider=provider,
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except (TimeoutError, RuntimeError, OSError) as exc:
        logger.warning("abstract_diff: summary call failed for %s: %s", file.path, exc)
        return IntentSummary(
            path=file.path,
            pseudocode_blocks=file.pseudocode,
            raw_diff_link=raw_diff_link,
        )
    bullets, confidence = _parse_summary(raw)
    return IntentSummary(
        path=file.path,
        bullet_points=bullets,
        pseudocode_blocks=file.pseudocode,
        raw_diff_link=raw_diff_link,
        confidence=confidence,
    )


async def summarize_diff(
    diff: str,
    task_context: TaskContext,
    *,
    llm_caller: LLMCaller | None = None,
    provider: str = _PROVIDER,
    max_tokens: int = _MAX_TOKENS,
    raw_diff_link: str = "",
    enabled: bool | None = None,
    max_files: int | None = None,
) -> list[IntentSummary]:
    """Produce a per-file :class:`IntentSummary` list for *diff*.

    Uses the cheap-tier reviewer chosen by
    :func:`bernstein.core.quality.cross_model_verifier.select_reviewer_model`
    against ``task_context.writer_model``; the opus tier is explicitly
    disallowed (the abstraction layer is meant to be cheap).

    On diffs with more than :data:`ABSTRACT_DIFF_MAX_FILES` files the
    abstraction *degrades gracefully*: a single top-level summary is
    returned with empty bullets so the PR body still renders without
    spending N x cheap-LLM calls.

    Args:
        diff: Unified diff text (``git diff`` output).
        task_context: Title / description / writer model. Threaded into
            the summary prompt so the reviewer sees *intent*.
        llm_caller: Override for tests.  Defaults to ``call_llm``.
        provider: LLM provider key.
        max_tokens: Per-file response cap.
        raw_diff_link: Optional GitHub anchor URL to embed in each
            summary's ``raw_diff_link``.
        enabled: Override the :data:`ABSTRACT_DIFF_ENABLED` toggle.
        max_files: Override the :data:`ABSTRACT_DIFF_MAX_FILES` cap.

    Returns:
        One :class:`IntentSummary` per changed file, in diff order. An
        empty list when the feature is disabled or the diff is empty.
    """
    is_enabled = ABSTRACT_DIFF_ENABLED if enabled is None else enabled
    if not is_enabled:
        return []
    cap = ABSTRACT_DIFF_MAX_FILES if max_files is None else max_files

    files_raw = _split_unified_diff(diff)
    if not files_raw:
        return []

    files: list[_FileDiff] = []
    for f in files_raw:
        pseudo = _extract_post_functions(f.post_image) if f.path.endswith(".py") else ()
        files.append(_FileDiff(path=f.path, body=f.body, post_image=f.post_image, pseudocode=pseudo))

    if len(files) > cap:
        logger.info(
            "abstract_diff: %d files exceeds cap %d — emitting top-level summary only",
            len(files),
            cap,
        )
        bullet = f"{len(files)} files changed — exceeds abstract-diff cap of {cap}; see raw diff for per-file detail."
        return [
            IntentSummary(
                path="<aggregate>",
                bullet_points=(bullet,),
                raw_diff_link=raw_diff_link,
            )
        ]

    caller = llm_caller or call_llm
    model = _disallow_opus(select_reviewer_model(task_context.writer_model or "claude"))

    results = await asyncio.gather(
        *[
            _summarize_one(
                f,
                task_context,
                model=model,
                llm_caller=caller,
                provider=provider,
                max_tokens=max_tokens,
                raw_diff_link=raw_diff_link,
            )
            for f in files
        ]
    )
    return list(results)


def render_pr_body(summaries: list[IntentSummary], *, raw_diff: str | None = None) -> str:
    """Render an "Intent" markdown section for a PR body.

    Each file gets a heading, its bullets, optional pseudocode, and a
    collapsible ``<details>`` containing the file's raw diff slice. When
    ``raw_diff`` is supplied, a single bottom-of-body details block holds
    the full raw diff for absolute drill-down.

    Args:
        summaries: Output of :func:`summarize_diff`.
        raw_diff: Optional full unified diff to embed at the bottom.
    """
    if not summaries:
        return ""

    lines: list[str] = ["## Intent", ""]
    file_diffs: dict[str, str] = {f.path: f.body for f in _split_unified_diff(raw_diff or "")}

    for s in summaries:
        lines.append(f"### `{s.path}`")
        if s.bullet_points:
            lines.extend(f"- {b}" for b in s.bullet_points)
        else:
            lines.append("- _(no behavioural summary available)_")
        if s.confidence:
            lines.append("")
            lines.append(f"_confidence: {s.confidence:.2f}_")
        if s.pseudocode_blocks:
            lines.append("")
            lines.append("<details><summary>Pseudocode</summary>")
            lines.append("")
            for block in s.pseudocode_blocks:
                lines.append("```text")
                lines.append(block)
                lines.append("```")
            lines.append("")
            lines.append("</details>")
        slice_diff = file_diffs.get(s.path, "")
        if slice_diff:
            lines.append("")
            lines.append("<details><summary>Raw diff</summary>")
            lines.append("")
            lines.append("```diff")
            lines.append(slice_diff)
            lines.append("```")
            lines.append("")
            lines.append("</details>")
        elif s.raw_diff_link:
            lines.append("")
            lines.append(f"[Raw diff]({s.raw_diff_link})")
        lines.append("")

    if raw_diff and len(summaries) > 1:
        lines.append("<details><summary>Full raw diff</summary>")
        lines.append("")
        lines.append("```diff")
        lines.append(raw_diff)
        lines.append("```")
        lines.append("")
        lines.append("</details>")

    return "\n".join(lines).rstrip() + "\n"
