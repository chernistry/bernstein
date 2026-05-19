"""Unit tests for :mod:`bernstein.core.orchestration.issue_to_pr`.

The suite exercises the four pipeline stages with a deterministic fake
``gh`` runner.  No subprocess, no network, no filesystem access.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from bernstein.core.orchestration.issue_to_pr import (
    APPROVAL_KEYWORD,
    STAGE_APPROVED_MARKER,
    STAGE_PLAN_DONE_MARKER,
    STAGE_PR_OPENED_MARKER,
    STICKY_MARKER,
    DiffProposal,
    InlineReviewComment,
    IssueContext,
    IssuePRClient,
    IssueToPRConfig,
    IssueToPRPipeline,
    PlanProposal,
    RepoRef,
    Stage,
    StageOutcome,
    Stages,
    Triggers,
    append_marker,
    build_plan_body,
    extract_pr_number,
    find_sticky_comment,
)

# ---------------------------------------------------------------------------
# Fake ``gh`` runner
# ---------------------------------------------------------------------------


@dataclass
class FakeRunner:
    """Stub for the ``gh`` CLI; routes args to in-memory state."""

    issue: dict[str, Any] = field(default_factory=dict)
    comments: list[dict[str, Any]] = field(default_factory=list)
    reactions_on_comment: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    next_comment_id: int = 1000
    next_pr_number: int = 4242
    posted_comments: list[dict[str, Any]] = field(default_factory=list)
    patched_comments: list[dict[str, Any]] = field(default_factory=list)
    opened_prs: list[dict[str, Any]] = field(default_factory=list)
    fail_post: bool = False

    def __call__(
        self,
        args: list[str],
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._dispatch(args, stdin)

    def _ok(self, body: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout=json.dumps(body),
            stderr="",
        )

    def _fail(self, code: int = 1) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["gh"],
            returncode=code,
            stdout="",
            stderr="boom",
        )

    def _dispatch(
        self,
        args: list[str],
        stdin: str | None,
    ) -> subprocess.CompletedProcess[str]:
        # args is like ["api", "-X", "POST", ...] or ["api", "<url>"]
        # Strip leading "-H accept" pairs to keep the matcher simple.
        cleaned: list[str] = []
        skip = 0
        for _i, a in enumerate(args):
            if skip > 0:
                skip -= 1
                continue
            if a == "-H":
                skip = 1
                continue
            cleaned.append(a)
        method = "GET"
        if "-X" in cleaned:
            idx = cleaned.index("-X")
            method = cleaned[idx + 1]
            cleaned = cleaned[:idx] + cleaned[idx + 2 :]
        # Drop --input - if present.
        if "--input" in cleaned:
            idx = cleaned.index("--input")
            cleaned = cleaned[:idx] + cleaned[idx + 2 :]
        # cleaned == ["api", "<path>"]
        if len(cleaned) < 2 or cleaned[0] != "api":
            return self._fail()
        path = cleaned[1]
        return self._route(method, path, stdin)

    def _route(
        self,
        method: str,
        path: str,
        stdin: str | None,
    ) -> subprocess.CompletedProcess[str]:
        # Issue body: repos/<owner>/<repo>/issues/<number>
        if (
            method == "GET"
            and path.startswith("repos/")
            and "/issues/" in path
            and "/comments" not in path
            and "/reactions" not in path
        ):
            return self._ok(self.issue)
        if method == "GET" and "/issues/" in path and path.endswith("/comments?per_page=100"):
            return self._ok(self.comments)
        if method == "GET" and "/issues/comments/" in path and "/reactions" in path:
            cid = int(path.split("/issues/comments/")[1].split("/")[0])
            return self._ok(self.reactions_on_comment.get(cid, []))
        if method == "POST" and "/issues/" in path and path.endswith("/comments"):
            if self.fail_post:
                return self._fail()
            payload = json.loads(stdin or "{}")
            cid = self.next_comment_id
            self.next_comment_id += 1
            entry = {"id": cid, "body": payload.get("body", ""), "user": {"login": "bot"}}
            self.comments.append(entry)
            self.posted_comments.append(entry)
            return self._ok(entry)
        if method == "PATCH" and path.startswith("repos/") and "/issues/comments/" in path:
            cid = int(path.rsplit("/", 1)[-1])
            payload = json.loads(stdin or "{}")
            self.patched_comments.append({"id": cid, "body": payload.get("body", "")})
            for c in self.comments:
                if c.get("id") == cid:
                    c["body"] = payload.get("body", "")
            return self._ok({"id": cid})
        if method == "GET" and "/pulls/" in path and path.endswith("/comments?per_page=100"):
            return self._ok(self.review_comments)
        if method == "POST" and path.startswith("repos/") and path.endswith("/pulls"):
            payload = json.loads(stdin or "{}")
            pr = {"number": self.next_pr_number, **payload}
            self.next_pr_number += 1
            self.opened_prs.append(pr)
            return self._ok(pr)
        return self._fail()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _issue(
    number: int = 7,
    *,
    title: str = "Add foo",
    body: str = "Please add foo.",
    author: str = "alice",
    labels: tuple[str, ...] = ("ai-welcome",),
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "user": {"login": author},
        "labels": [{"name": l} for l in labels],
    }


def _plan_gen(ctx: IssueContext) -> PlanProposal:
    return PlanProposal(
        markdown=f"Plan for {ctx.title}",
        branch=f"bernstein/issue-{ctx.number}",
        commit_message=f"feat: resolve #{ctx.number}",
    )


def _diff_gen(ctx: IssueContext, plan: PlanProposal) -> DiffProposal:
    return DiffProposal(
        patch="diff --git a/x b/x\n",
        branch=plan.branch,
        commit_message=f"feat: resolve #{ctx.number}",
        base="main",
    )


def _empty_diff_gen(ctx: IssueContext, plan: PlanProposal) -> DiffProposal:
    return DiffProposal(patch="", branch=plan.branch, commit_message="", base="main")


def _revise_gen(
    ctx: IssueContext,
    pr_number: int,
    comments: list[InlineReviewComment],
) -> DiffProposal:
    body = "\n".join(c.body for c in comments)
    return DiffProposal(
        patch=f"diff for {body[:20]}\n",
        branch=f"bernstein/issue-{ctx.number}",
        commit_message=f"fixup: address {len(comments)} review comments",
        base="main",
    )


def _apply_diff_ok(d: DiffProposal) -> str:
    return "deadbeef"


def _apply_diff_raise(d: DiffProposal) -> str:
    raise RuntimeError("push failed")


def _pipeline(
    runner: FakeRunner,
    *,
    plan_generator=_plan_gen,
    diff_generator=_diff_gen,
    revise_generator=_revise_gen,
    apply_diff=_apply_diff_ok,
    triggers: Triggers | None = None,
    stages: Stages | None = None,
    repos: tuple[RepoRef, ...] = (),
) -> IssueToPRPipeline:
    config = IssueToPRConfig(
        repos=repos,
        triggers=triggers or Triggers(label_required="ai-welcome"),
        stages=stages or Stages(plan_comment_required_approval=True),
    )
    return IssueToPRPipeline(
        config=config,
        client=IssuePRClient(runner=runner),
        plan_generator=plan_generator,
        diff_generator=diff_generator,
        revise_generator=revise_generator,
        apply_diff=apply_diff,
    )


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_config_from_mapping_full_payload() -> None:
    cfg = IssueToPRConfig.from_mapping(
        {
            "repos": [{"owner": "acme", "name": "web"}, {"owner": "acme", "name": "api"}],
            "triggers": {
                "label_required": "ai-welcome",
                "author_allow_list": ["alice", "bob"],
            },
            "stages": {
                "plan_comment_required_approval": False,
                "draft_pr_default": False,
                "approval_keyword": "[ship-it]",
                "revise_quiet_window_s": 30,
            },
        }
    )
    assert cfg.repos == (RepoRef("acme", "web"), RepoRef("acme", "api"))
    assert cfg.triggers.label_required == "ai-welcome"
    assert cfg.triggers.author_allow_list == ("alice", "bob")
    assert cfg.stages.plan_comment_required_approval is False
    assert cfg.stages.draft_pr_default is False
    assert cfg.stages.approval_keyword == "[ship-it]"
    assert cfg.stages.revise_quiet_window_s == 30.0


def test_config_from_mapping_defaults_for_missing_keys() -> None:
    cfg = IssueToPRConfig.from_mapping(None)
    assert cfg.repos == ()
    assert cfg.triggers.label_required is None
    assert cfg.stages.draft_pr_default is True
    assert cfg.stages.approval_keyword == APPROVAL_KEYWORD


def test_config_from_mapping_drops_malformed_repos() -> None:
    cfg = IssueToPRConfig.from_mapping({"repos": [{"owner": "acme"}, "garbage", {"owner": "a", "name": "b"}]})
    assert cfg.repos == (RepoRef("a", "b"),)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_build_plan_body_embeds_sticky_and_plan_markers() -> None:
    out = build_plan_body("Hello plan")
    assert STICKY_MARKER in out
    assert STAGE_PLAN_DONE_MARKER in out
    assert "Hello plan" in out


def test_find_sticky_comment_returns_first_match() -> None:
    comments = [
        {"id": 1, "body": "unrelated"},
        {"id": 2, "body": f"{STICKY_MARKER}\nplan"},
        {"id": 3, "body": f"{STICKY_MARKER} also sticky"},
    ]
    sticky = find_sticky_comment(comments)
    assert sticky is not None
    assert sticky["id"] == 2


def test_extract_pr_number_roundtrip() -> None:
    body = f"sticky\n{STAGE_PR_OPENED_MARKER.format(pr_number=99)}\n"
    assert extract_pr_number(body) == 99


def test_extract_pr_number_returns_none_when_missing() -> None:
    assert extract_pr_number("sticky") is None


def test_append_marker_is_idempotent() -> None:
    body = "abc"
    once = append_marker(body, STAGE_APPROVED_MARKER)
    twice = append_marker(once, STAGE_APPROVED_MARKER)
    assert once == twice
    assert STAGE_APPROVED_MARKER in once


# ---------------------------------------------------------------------------
# Stage: plan
# ---------------------------------------------------------------------------


def test_tick_plan_posts_sticky_comment_first_run() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(runner)
    report = pipe.tick_plan("acme/web", 7)
    assert report.stage is Stage.PLAN
    assert report.outcome is StageOutcome.ADVANCED
    assert len(runner.posted_comments) == 1
    body = runner.posted_comments[0]["body"]
    assert STICKY_MARKER in body
    assert STAGE_PLAN_DONE_MARKER in body


def test_tick_plan_is_idempotent_when_sticky_present() -> None:
    sticky_body = build_plan_body("existing plan")
    runner = FakeRunner(
        issue=_issue(),
        comments=[{"id": 11, "body": sticky_body, "user": {"login": "bot"}}],
    )
    pipe = _pipeline(runner)
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.ALREADY_DONE
    assert runner.posted_comments == []


def test_tick_plan_skipped_when_label_missing() -> None:
    runner = FakeRunner(issue=_issue(labels=("bug",)))
    pipe = _pipeline(runner)
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.SKIPPED
    assert "missing required label" in report.detail


def test_tick_plan_skipped_when_author_not_in_allow_list() -> None:
    runner = FakeRunner(issue=_issue(author="mallory"))
    pipe = _pipeline(
        runner,
        triggers=Triggers(label_required="ai-welcome", author_allow_list=("alice",)),
    )
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.SKIPPED
    assert "mallory" in report.detail


def test_tick_plan_skipped_when_repo_not_in_allow_list() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(runner, repos=(RepoRef("other", "x"),))
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.SKIPPED


def test_tick_plan_error_when_issue_payload_empty() -> None:
    runner = FakeRunner(issue={})
    pipe = _pipeline(runner)
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.ERROR


def test_tick_plan_error_when_post_fails() -> None:
    runner = FakeRunner(issue=_issue(), fail_post=True)
    pipe = _pipeline(runner)
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.ERROR


def test_tick_plan_error_when_generator_missing() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(runner, plan_generator=None)
    report = pipe.tick_plan("acme/web", 7)
    assert report.outcome is StageOutcome.ERROR


# ---------------------------------------------------------------------------
# Stage: approval
# ---------------------------------------------------------------------------


def test_tick_approval_waiting_without_plan_comment() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(runner)
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.WAITING


def test_tick_approval_advances_when_keyword_seen_from_allow_list_user() -> None:
    sticky_body = build_plan_body("plan body")
    sticky = {"id": 50, "body": sticky_body, "user": {"login": "bot"}}
    approval = {"id": 51, "body": APPROVAL_KEYWORD, "user": {"login": "alice"}}
    runner = FakeRunner(issue=_issue(), comments=[sticky, approval])
    pipe = _pipeline(
        runner,
        triggers=Triggers(label_required="ai-welcome", author_allow_list=("alice",)),
    )
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED
    # Marker should now appear in the patched body.
    assert any(STAGE_APPROVED_MARKER in p["body"] for p in runner.patched_comments)


def test_tick_approval_ignores_keyword_from_unlisted_user() -> None:
    sticky_body = build_plan_body("plan body")
    sticky = {"id": 60, "body": sticky_body, "user": {"login": "bot"}}
    approval = {"id": 61, "body": APPROVAL_KEYWORD, "user": {"login": "mallory"}}
    runner = FakeRunner(issue=_issue(), comments=[sticky, approval])
    pipe = _pipeline(
        runner,
        triggers=Triggers(label_required="ai-welcome", author_allow_list=("alice",)),
    )
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.WAITING


def test_tick_approval_advances_when_thumbs_up_seen() -> None:
    sticky_body = build_plan_body("plan body")
    sticky = {"id": 70, "body": sticky_body, "user": {"login": "bot"}}
    runner = FakeRunner(
        issue=_issue(),
        comments=[sticky],
        reactions_on_comment={70: [{"content": "+1", "user": {"login": "alice"}}]},
    )
    pipe = _pipeline(
        runner,
        triggers=Triggers(label_required="ai-welcome", author_allow_list=("alice",)),
    )
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED


def test_tick_approval_already_done_when_marker_present() -> None:
    body = build_plan_body("plan") + "\n" + STAGE_APPROVED_MARKER + "\n"
    sticky = {"id": 71, "body": body, "user": {"login": "bot"}}
    runner = FakeRunner(issue=_issue(), comments=[sticky])
    pipe = _pipeline(runner)
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.ALREADY_DONE


def test_tick_approval_advances_when_approval_not_required() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(
        runner,
        stages=Stages(plan_comment_required_approval=False),
    )
    report = pipe.tick_approval("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED


# ---------------------------------------------------------------------------
# Stage: pr_open
# ---------------------------------------------------------------------------


def _approved_sticky(comment_id: int = 90) -> dict[str, Any]:
    body = build_plan_body("plan") + STAGE_APPROVED_MARKER + "\n"
    return {"id": comment_id, "body": body, "user": {"login": "bot"}}


def test_tick_pr_open_opens_draft_pr() -> None:
    runner = FakeRunner(issue=_issue(), comments=[_approved_sticky()])
    pipe = _pipeline(runner)
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED
    assert report.pr_number is not None
    assert runner.opened_prs[0]["draft"] is True
    # Sticky body now carries the pr-open marker.
    last_patch = runner.patched_comments[-1]
    assert f"pr={report.pr_number}" in last_patch["body"]


def test_tick_pr_open_waits_when_approval_marker_missing() -> None:
    sticky = {"id": 91, "body": build_plan_body("plan"), "user": {"login": "bot"}}
    runner = FakeRunner(issue=_issue(), comments=[sticky])
    pipe = _pipeline(runner)
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.WAITING


def test_tick_pr_open_idempotent_when_pr_marker_present() -> None:
    body = build_plan_body("plan") + STAGE_APPROVED_MARKER + "\n" + STAGE_PR_OPENED_MARKER.format(pr_number=321) + "\n"
    sticky = {"id": 95, "body": body, "user": {"login": "bot"}}
    runner = FakeRunner(issue=_issue(), comments=[sticky])
    pipe = _pipeline(runner)
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.ALREADY_DONE
    assert report.pr_number == 321
    assert runner.opened_prs == []


def test_tick_pr_open_waits_on_empty_diff() -> None:
    runner = FakeRunner(issue=_issue(), comments=[_approved_sticky()])
    pipe = _pipeline(runner, diff_generator=_empty_diff_gen)
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.WAITING
    assert runner.opened_prs == []


def test_tick_pr_open_surfaces_apply_diff_failure_as_error() -> None:
    runner = FakeRunner(issue=_issue(), comments=[_approved_sticky()])
    pipe = _pipeline(runner, apply_diff=_apply_diff_raise)
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.ERROR
    assert "push failed" in report.detail
    assert runner.opened_prs == []


def test_tick_pr_open_respects_non_draft_setting() -> None:
    runner = FakeRunner(issue=_issue(), comments=[_approved_sticky()])
    pipe = _pipeline(
        runner,
        stages=Stages(
            plan_comment_required_approval=True,
            draft_pr_default=False,
        ),
    )
    report = pipe.tick_pr_open("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED
    assert runner.opened_prs[0]["draft"] is False


# ---------------------------------------------------------------------------
# Stage: pr_revise
# ---------------------------------------------------------------------------


def _sticky_with_pr(pr_number: int = 123, comment_id: int = 200) -> dict[str, Any]:
    body = (
        build_plan_body("plan")
        + STAGE_APPROVED_MARKER
        + "\n"
        + STAGE_PR_OPENED_MARKER.format(pr_number=pr_number)
        + "\n"
    )
    return {"id": comment_id, "body": body, "user": {"login": "bot"}}


def test_tick_pr_revise_no_new_comments() -> None:
    runner = FakeRunner(
        issue=_issue(),
        comments=[_sticky_with_pr()],
        review_comments=[],
    )
    pipe = _pipeline(runner)
    report = pipe.tick_pr_revise("acme/web", 7)
    assert report.outcome is StageOutcome.ALREADY_DONE


def test_tick_pr_revise_advances_on_new_comment_and_records_marker() -> None:
    sticky = _sticky_with_pr()
    review_comments = [
        {
            "id": 9001,
            "path": "src/x.py",
            "line": 12,
            "body": "use a constant",
            "user": {"login": "carol"},
            "created_at": "2026-05-19T12:00:00Z",
        },
    ]
    runner = FakeRunner(
        issue=_issue(),
        comments=[sticky],
        review_comments=review_comments,
    )
    pipe = _pipeline(runner)
    report = pipe.tick_pr_revise("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED
    assert report.pr_number == 123
    last_body = runner.patched_comments[-1]["body"]
    assert "stage:pr-revise:last=2026-05-19T12:00:00Z" in last_body
    assert "sha=deadbeef" in last_body


def test_tick_pr_revise_filters_old_comments() -> None:
    body = (
        build_plan_body("plan")
        + STAGE_APPROVED_MARKER
        + "\n"
        + STAGE_PR_OPENED_MARKER.format(pr_number=123)
        + "\n<!-- stage:pr-revise:last=2026-05-19T11:00:00Z sha=cafefade -->\n"
    )
    sticky = {"id": 250, "body": body, "user": {"login": "bot"}}
    review_comments = [
        {
            "id": 9001,
            "path": "src/x.py",
            "line": 12,
            "body": "stale comment",
            "user": {"login": "carol"},
            "created_at": "2026-05-19T10:00:00Z",
        },
        {
            "id": 9002,
            "path": "src/x.py",
            "line": 13,
            "body": "fresh comment",
            "user": {"login": "carol"},
            "created_at": "2026-05-19T12:00:00Z",
        },
    ]
    runner = FakeRunner(
        issue=_issue(),
        comments=[sticky],
        review_comments=review_comments,
    )
    pipe = _pipeline(runner)
    report = pipe.tick_pr_revise("acme/web", 7)
    assert report.outcome is StageOutcome.ADVANCED
    last_body = runner.patched_comments[-1]["body"]
    assert "last=2026-05-19T12:00:00Z" in last_body
    # Old marker replaced, not stacked.
    assert last_body.count("stage:pr-revise:last=") == 1


def test_tick_pr_revise_waits_when_pr_not_opened() -> None:
    sticky = {
        "id": 260,
        "body": build_plan_body("plan") + STAGE_APPROVED_MARKER + "\n",
        "user": {"login": "bot"},
    }
    runner = FakeRunner(issue=_issue(), comments=[sticky])
    pipe = _pipeline(runner)
    report = pipe.tick_pr_revise("acme/web", 7)
    assert report.outcome is StageOutcome.WAITING


def test_tick_pr_revise_surfaces_apply_diff_failure() -> None:
    sticky = _sticky_with_pr()
    review_comments = [
        {
            "id": 9001,
            "path": "src/x.py",
            "line": 12,
            "body": "use a constant",
            "user": {"login": "carol"},
            "created_at": "2026-05-19T12:00:00Z",
        },
    ]
    runner = FakeRunner(
        issue=_issue(),
        comments=[sticky],
        review_comments=review_comments,
    )
    pipe = _pipeline(runner, apply_diff=_apply_diff_raise)
    report = pipe.tick_pr_revise("acme/web", 7)
    assert report.outcome is StageOutcome.ERROR


# ---------------------------------------------------------------------------
# Driver and trace
# ---------------------------------------------------------------------------


def test_tick_drives_first_run_and_stops_at_approval_waiting() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(runner)
    reports = pipe.tick("acme/web", 7)
    # plan advances, approval waits, remaining stages never run.
    assert [r.stage for r in reports] == [Stage.PLAN, Stage.APPROVAL]
    assert reports[0].outcome is StageOutcome.ADVANCED
    assert reports[1].outcome is StageOutcome.WAITING


def test_tick_drives_full_pipeline_when_approval_not_required() -> None:
    runner = FakeRunner(issue=_issue())
    pipe = _pipeline(
        runner,
        stages=Stages(plan_comment_required_approval=False),
    )
    reports = pipe.tick("acme/web", 7)
    stages = [r.stage for r in reports]
    assert Stage.PLAN in stages
    assert Stage.APPROVAL in stages
    assert Stage.PR_OPEN in stages
    assert Stage.PR_REVISE in stages


def test_trace_reports_progress() -> None:
    sticky = _sticky_with_pr(pr_number=555, comment_id=999)
    runner = FakeRunner(issue=_issue(), comments=[sticky])
    pipe = _pipeline(runner)
    trace = pipe.trace("acme/web", 7)
    assert trace.repo == "acme/web"
    assert trace.issue_number == 7
    assert trace.plan_posted is True
    assert trace.approved is True
    assert trace.pr_number == 555
    rendered = trace.render()
    assert "pr_number:      555" in rendered
    assert "approved:       True" in rendered


def test_trace_handles_missing_state() -> None:
    runner = FakeRunner(issue=_issue(), comments=[])
    pipe = _pipeline(runner)
    trace = pipe.trace("acme/web", 7)
    assert trace.plan_posted is False
    assert trace.approved is False
    assert trace.pr_number is None
    assert trace.last_revise_at is None
