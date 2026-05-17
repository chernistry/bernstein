"""GitLab CI failure routing: trace fetch, parse, blame and payload builder.

Mirror of :mod:`bernstein.github_app.ci_router`.  Pure functions only —
HTTP calls go through :mod:`bernstein.gitlab_app.app.fetch_job_trace`.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bernstein.gitlab_app.app import fetch_job_trace

if TYPE_CHECKING:
    from bernstein.core.quality.ci_fix import CIFailure

logger = logging.getLogger(__name__)

# Maximum auto-retries before the system stops creating new ci-fix tasks
# for a given branch.  Mirrors the GitHub side.
MAX_CI_RETRIES: int = 3


@dataclass
class GitLabCIBlameResult:
    """Attribution metadata for a GitLab pipeline failure.

    Attributes:
        head_sha: SHA the pipeline ran against.
        ref: Source branch / tag.
        responsible_files: Files mentioned in the failure logs.  May be
            empty when no trace was downloadable.
    """

    head_sha: str
    ref: str = ""
    responsible_files: list[str] = field(default_factory=list[str])


def fetch_and_parse_failures(
    project_id: str | int,
    failed_builds: list[dict[str, Any]],
    token: str = "",
    base_url: str | None = None,
    *,
    max_jobs: int = 3,
) -> list[CIFailure]:
    """Fetch + parse traces for the first *max_jobs* failed builds.

    Args:
        project_id: GitLab project ID / encoded slug.
        failed_builds: List of GitLab pipeline build dicts with
            ``status == "failed"`` (or ``"canceled"``).
        token: API token for ``PRIVATE-TOKEN`` header.
        base_url: GitLab base URL override.
        max_jobs: Cap on how many job traces to download.

    Returns:
        List of :class:`CIFailure` objects parsed from the traces.
        Empty when no token / network failures / unparseable logs.
    """
    if not failed_builds or not token or not project_id:
        return []

    from bernstein.adapters.ci.gitlab_ci import GitLabCIParser

    parser = GitLabCIParser()
    failures: list[CIFailure] = []
    for build in failed_builds[:max_jobs]:
        job_id_raw = build.get("id")
        if not isinstance(job_id_raw, int):
            continue
        trace = fetch_job_trace(project_id, job_id_raw, token, base_url)
        if not trace:
            continue
        # ``parser.parse`` is internally typed but pyright sees the
        # imported parser at a relaxed boundary — funnel through ``Any``
        # to keep this module strict.
        parse_fn: Any = parser.parse
        parsed: Any = parse_fn(trace)
        if isinstance(parsed, list):
            parsed_typed: list[Any] = parsed  # type: ignore[assignment]
            for failure in parsed_typed:
                failures.append(failure)

    logger.debug(
        "fetch_and_parse_failures: project=%s jobs=%d failures=%d",
        project_id,
        len(failed_builds),
        len(failures),
    )

    return failures


def build_pipeline_routing_payload(
    failures: list[CIFailure],
    blame: GitLabCIBlameResult,
    pipeline_id: int,
    pipeline_url: str = "",
    failed_builds: list[dict[str, Any]] | None = None,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Build a fix-task payload enriched with GitLab pipeline context.

    Mirrors :func:`bernstein.github_app.ci_router.build_ci_routing_payload`.

    Model / effort escalate on repeats:

    * Attempts 1-2: ``sonnet`` / ``high``
    * Attempts 3+:  ``opus``   / ``max``

    Args:
        failures: Parsed CI failures (may be empty).
        blame: Blame attribution.
        pipeline_id: GitLab pipeline ID.
        pipeline_url: Pipeline web URL.
        failed_builds: Original failed-builds list — used to summarise
            jobs when we have no parsed CI failures (e.g. no token).
        retry_count: Previous fix attempts (0 means first try).

    Returns:
        Task creation dict compatible with ``TaskCreate`` fields.
    """
    model = "opus" if retry_count >= 2 else "sonnet"
    effort = "max" if retry_count >= 2 else "high"

    if failures:
        failure_kinds = ", ".join(sorted({f.kind.value for f in failures}))
        failure_summaries = "\n".join(f"- [{f.job}] {f.summary}" for f in failures)
        hints = "\n".join(f"  {f.fix_hint}" for f in failures if f.fix_hint)
    else:
        failure_kinds = "pipeline_failed"
        # No parseable failures — fall back to job names.
        failure_summaries = (
            "\n".join(
                f"- Job **{b.get('name', 'unknown')}** (stage: {b.get('stage', 'unknown')}) failed"
                for b in (failed_builds or [])[:5]
            )
            or f"- Pipeline {pipeline_id} failed (no job traces available)"
        )
        hints = ""

    files_block = (
        "\n".join(f"  - {f}" for f in blame.responsible_files) if blame.responsible_files else "  (could not determine)"
    )
    pipeline_link = f"\nPipeline: {pipeline_url}" if pipeline_url else ""
    retry_note = f"\n\n**Retry attempt {retry_count + 1}/{MAX_CI_RETRIES}**" if retry_count > 0 else ""
    ref_note = f" on `{blame.ref}`" if blame.ref else ""

    description = textwrap.dedent(
        f"""\
        GitLab pipeline {pipeline_id}{ref_note} failed.{retry_note}
        Commit: {blame.head_sha[:8]}
        Failures: {failure_kinds}
        {pipeline_link}

        ## Files to investigate
        {files_block}

        ## Failure summaries
        {failure_summaries}

        ## Suggested fixes
        {hints}

        ## Instructions
        1. Review the failing jobs in the GitLab pipeline view.
        2. Apply the suggested fixes locally.
        3. Verify with the project's lint + test commands.
        4. Push the fix and confirm pipeline goes green.
        """
    )

    sha_short = blame.head_sha[:8] if blame.head_sha else f"p{pipeline_id}"
    title = f"[ci-fix][{sha_short}] GitLab pipeline {pipeline_id}: {failure_kinds}"[:120]

    return {
        "title": title,
        "description": description,
        "role": "qa",
        "priority": 1,
        "scope": "small",
        "task_type": "fix",
        "model": model,
        "effort": effort,
    }
