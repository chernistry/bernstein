"""Lint for installed skill directories (issue #1720, track 1 minimum).

The default install and sync paths stay advisory for backward compatibility.
When their strict mode is enabled, ERROR findings block the operation while
WARNING findings remain advisory. Operators can also run lint by hand or via
CI to surface frontmatter typos, missing referenced files, sensitive patterns
the sanitiser flags, and body conventions (heading order, max length).

Checks performed:

- Frontmatter parses as YAML and matches :class:`SkillManifest` after a
  loose pre-filter (extra Claude-style keys like ``whenToUse`` are
  warnings, not errors, because authoring formats are still in flux).
- Every ``references`` / ``scripts`` / ``assets`` entry resolves to a
  real file under the matching bucket.
- The sanitiser (`core/skills/sanitizer.py`) reports zero stripped
  codepoints. Anything stripped is a high-signal sensitive-pattern leak.
- The body starts with an ``H1`` heading and is under 5 KB.

Output shape is a list of :class:`LintFinding` records. The caller renders
them and chooses the exit code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from bernstein.core.skills.manifest import SkillManifest
from bernstein.core.skills.sanitizer import strip_invisible_tags

#: Body cap: 5 KB before we warn. The RFC sets this as a soft cap so a
#: future skill that needs more context can still ship.
_MAX_BODY_BYTES: int = 5 * 1024

_VALID_BUCKETS: tuple[str, ...] = ("references", "scripts", "assets")


class LintSeverity(StrEnum):
    """Severity of a lint finding.

    ``WARNING`` is the default. ``ERROR`` blocks install and sync only when
    the caller opts in to strict mode.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class LintFinding:
    """One advisory finding from :func:`lint_skill`."""

    skill_name: str
    severity: LintSeverity
    code: str
    message: str
    path: Path | None = None


def _split_skill_md(text: str) -> tuple[str, str] | None:
    """Re-implement the split locally to avoid importing the loader path.

    Returns ``None`` when the file lacks frontmatter at all.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    front: list[str] = []
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            return ("\n".join(front), "\n".join(lines[idx + 1 :]).strip("\n"))
        front.append(lines[idx])
    return None


def _coerce_known_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop extra keys (e.g. ``whenToUse``) before strict validation.

    Strict :class:`SkillManifest` rejects unknown keys; we warn on them
    separately so an author who is mid-migration still gets useful
    feedback rather than a single fatal error.
    """
    allowed = set(SkillManifest.model_fields.keys())
    return {k: v for k, v in raw.items() if k in allowed}


def lint_skill(skill_dir: Path, *, skill_name: str | None = None) -> list[LintFinding]:
    """Run the advisory lint against one installed skill directory.

    Args:
        skill_dir: Directory containing ``SKILL.md``.
        skill_name: Override for reporting. Defaults to ``skill_dir.name``.

    Returns:
        Findings, possibly empty. An empty list means lint-clean.
    """
    name = skill_name or skill_dir.name
    findings: list[LintFinding] = []

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="missing-skill-md",
                message="SKILL.md is missing",
                path=skill_dir,
            )
        )
        return findings

    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="unreadable-skill-md",
                message=f"failed to read SKILL.md: {exc}",
                path=skill_md,
            )
        )
        return findings
    split = _split_skill_md(text)
    if split is None:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="missing-frontmatter",
                message="SKILL.md has no YAML frontmatter",
                path=skill_md,
            )
        )
        return findings
    front_raw, body = split

    try:
        loaded = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="yaml-error",
                message=f"frontmatter YAML failed to parse: {exc}",
                path=skill_md,
            )
        )
        return findings
    if not isinstance(loaded, dict):
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="frontmatter-shape",
                message="frontmatter must be a YAML mapping",
                path=skill_md,
            )
        )
        return findings
    raw_dict = cast("dict[str, Any]", loaded)

    extra_keys = sorted(set(raw_dict.keys()) - set(SkillManifest.model_fields.keys()))
    for key in extra_keys:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.WARNING,
                code="extra-key",
                message=f"frontmatter contains unknown key {key!r}",
                path=skill_md,
            )
        )

    try:
        manifest = SkillManifest.model_validate(_coerce_known_fields(raw_dict))
    except ValidationError as exc:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="invalid-manifest",
                message=f"manifest failed validation: {exc}",
                path=skill_md,
            )
        )
        return findings

    # Anchor containment to the resolved skill root so a symlinked
    # bucket (``references -> /tmp/elsewhere``) cannot smuggle host
    # files past the lint. The bucket directories themselves are not
    # resolved; we build candidates from the unresolved path and check
    # them against the resolved skill root.
    skill_root = skill_dir.resolve()
    for bucket in _VALID_BUCKETS:
        bucket_root = skill_root / bucket
        for filename in getattr(manifest, bucket):
            rel = Path(filename)
            if rel.is_absolute() or ".." in rel.parts:
                findings.append(
                    LintFinding(
                        skill_name=name,
                        severity=LintSeverity.ERROR,
                        code="unsafe-reference-path",
                        message=(
                            f"manifest declares unsafe {bucket} path {filename!r}; "
                            "absolute paths and parent traversal are not allowed"
                        ),
                        path=skill_md,
                    )
                )
                continue
            candidate = (skill_dir / bucket / rel).resolve()
            if not candidate.is_relative_to(bucket_root):
                findings.append(
                    LintFinding(
                        skill_name=name,
                        severity=LintSeverity.ERROR,
                        code="unsafe-reference-path",
                        message=(f"manifest {bucket} path {filename!r} escapes the {bucket}/ root"),
                        path=skill_md,
                    )
                )
                continue
            if not candidate.is_file():
                findings.append(
                    LintFinding(
                        skill_name=name,
                        severity=LintSeverity.ERROR,
                        code="missing-reference",
                        message=f"manifest declares {bucket}/{filename} but the file is missing",
                        path=candidate,
                    )
                )

    _cleaned, stripped = strip_invisible_tags(text)
    if stripped > 0:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.ERROR,
                code="sensitive-pattern",
                message=(
                    f"sanitiser stripped {stripped} invisible Unicode codepoint(s); "
                    "this skill carries a likely prompt-injection payload"
                ),
                path=skill_md,
            )
        )

    body_bytes = len(body.encode("utf-8"))
    if body_bytes > _MAX_BODY_BYTES:
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.WARNING,
                code="body-too-large",
                message=(f"body is {body_bytes} bytes; recommended max is {_MAX_BODY_BYTES}"),
                path=skill_md,
            )
        )

    body_lines = [line for line in body.splitlines() if line.strip()]
    if body_lines and not body_lines[0].lstrip().startswith("#"):
        findings.append(
            LintFinding(
                skill_name=name,
                severity=LintSeverity.WARNING,
                code="missing-h1",
                message="body should start with an H1 heading",
                path=skill_md,
            )
        )

    return findings


__all__ = ["LintFinding", "LintSeverity", "lint_skill"]
