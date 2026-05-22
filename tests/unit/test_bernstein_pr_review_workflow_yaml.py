"""Structural assertions on ``.github/workflows/bernstein-pr-review.yml``."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


WORKFLOW = Path(".github/workflows/bernstein-pr-review.yml")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict[str, object]:
    return yaml.safe_load(workflow_text)


@pytest.fixture(scope="module")
def review_steps(workflow: dict[str, object]) -> list[dict[str, object]]:
    jobs = workflow.get("jobs", {})
    assert isinstance(jobs, dict)
    review = jobs.get("review")
    assert isinstance(review, dict), "expected a 'review' job"
    steps = review.get("steps", [])
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step_named(steps: list[dict[str, object]], name: str) -> dict[str, object]:
    step = next((item for item in steps if item.get("name") == name), None)
    assert step is not None, f"missing workflow step: {name}"
    return step


def test_workflow_file_exists() -> None:
    assert WORKFLOW.exists(), "Bernstein PR review workflow must exist"


def test_pr_review_runs_local_action_from_base_checkout(review_steps: list[dict[str, object]]) -> None:
    """The local action must not execute PR head code while the API key is set."""
    review_step = _step_named(review_steps, "Review PR")
    review_index = review_steps.index(review_step)

    checkout_steps = [
        step
        for step in review_steps[:review_index]
        if isinstance(step.get("uses"), str) and str(step["uses"]).startswith("actions/checkout@")
    ]
    assert checkout_steps, "Review PR must be preceded by a checkout of trusted action code"
    trusted_checkout = checkout_steps[-1]
    checkout_with = trusted_checkout.get("with", {})
    assert isinstance(checkout_with, dict)
    assert checkout_with.get("ref") == "${{ github.event.pull_request.base.sha }}", (
        "Review PR must run `uses: ./` from the base checkout, not from pull_request.head.sha"
    )
    assert checkout_with.get("persist-credentials") is False

    for step in checkout_steps:
        step_with = step.get("with", {})
        assert isinstance(step_with, dict)
        assert step_with.get("ref") != "${{ github.event.pull_request.head.sha }}", (
            "PR head code must not be checked out before running the local action with ANTHROPIC_API_KEY"
        )

    fetch_diff = _step_named(review_steps, "Fetch PR diff")
    assert review_steps.index(fetch_diff) < review_index
    run = fetch_diff.get("run", "")
    assert isinstance(run, str)
    assert ".bernstein-pr.diff" in run, "PR diff must be fetched as data for review context"
    assert "github.event.pull_request.diff_url" in run

    assert review_step.get("uses") == "./"
    inputs = review_step.get("with", {})
    assert isinstance(inputs, dict)
    task = inputs.get("task", "")
    assert isinstance(task, str)
    assert ".bernstein-pr.diff" in task, "Review task must point the action at the fetched PR diff"

