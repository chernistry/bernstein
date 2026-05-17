"""Property-based tests for the doctor subsystem.

These tests do not exercise network or subprocess paths. They drive the
pure helpers (summarize, exit_code_for, environment detection, etc.) with
random valid input to surface invariants that case-based tests can miss.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from bernstein.cli.doctor.adapter_checks import _first_nonempty_line, _load_adapters_from_yaml
from bernstein.cli.doctor.environment_checks import detect_environments
from bernstein.cli.doctor.report import (
    DoctorResult,
    exit_code_for,
    render_report,
    summarize,
)

STATUSES = ("ok", "warn", "fail", "skip")
CATEGORIES = ("installation", "adapter", "network", "environment")


def _result_strategy() -> st.SearchStrategy[DoctorResult]:
    safe_text = st.text(alphabet=string.printable, min_size=0, max_size=40).filter(
        lambda s: "[" not in s and "]" not in s
    )
    return st.builds(
        DoctorResult,
        name=safe_text.filter(lambda s: bool(s.strip())),
        category=st.sampled_from(CATEGORIES),
        status=st.sampled_from(STATUSES),
        detail=safe_text,
        remediation=safe_text,
    )


@settings(deadline=None, max_examples=80)
@given(results=st.lists(_result_strategy(), max_size=40))
def test_summarize_counts_sum_equals_input_length(results: list[DoctorResult]) -> None:
    counts = summarize(results)
    assert sum(counts.values()) == len(results)
    assert set(counts.keys()) == {"ok", "warn", "fail", "skip"}


@settings(deadline=None, max_examples=80)
@given(results=st.lists(_result_strategy(), max_size=40))
def test_exit_code_iff_any_fail(results: list[DoctorResult]) -> None:
    has_fail = any(r.status == "fail" for r in results)
    assert (exit_code_for(results) == 1) == has_fail


@settings(deadline=None, max_examples=40)
@given(results=st.lists(_result_strategy(), max_size=20))
def test_render_report_never_raises(results: list[DoctorResult]) -> None:
    # Smoke test that the renderer copes with arbitrary detail strings.
    text = render_report(results)
    assert isinstance(text, str)


_LINE_ALPHABET = string.ascii_letters + string.digits + " \t.-_"


@settings(deadline=None, max_examples=80)
@given(
    text=st.lists(
        st.text(alphabet=_LINE_ALPHABET, min_size=0, max_size=20),
        min_size=0,
        max_size=10,
    )
)
def test_first_nonempty_line_returns_first_stripped_or_empty(text: list[str]) -> None:
    joined = "\n".join(text)
    expected = ""
    for line in text:
        if line.strip():
            expected = line.strip()
            break
    assert _first_nonempty_line(joined) == expected


@settings(deadline=None, max_examples=60)
@given(
    keys=st.lists(
        st.sampled_from(
            [
                "GITHUB_ACTIONS",
                "GITLAB_CI",
                "BUILDKITE",
                "CIRCLECI",
                "JENKINS_URL",
                "DEVCONTAINER",
                "REMOTE_CONTAINERS",
                "INVOCATION_ID",
                "CI",
            ]
        ),
        unique=True,
        max_size=5,
    )
)
def test_environment_detection_is_subset_of_known_labels(keys: list[str]) -> None:
    env: dict[str, str] = {}
    for k in keys:
        env[k] = (
            "true"
            if k in ("GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI", "DEVCONTAINER", "REMOTE_CONTAINERS", "CI")
            else "value"
        )
    matches = detect_environments(env=env, file_exists=lambda _p: False)
    labels = {m.label for m in matches}
    # No invented labels.
    expected_universe = {
        "GitHub Actions",
        "GitLab CI",
        "Buildkite",
        "CircleCI",
        "Jenkins",
        "VS Code devcontainer",
        "VS Code devcontainer (remote)",
        "systemd-run",
        "Generic CI",
    }
    assert labels.issubset(expected_universe)


@settings(deadline=None, max_examples=40)
@given(
    extras=st.dictionaries(
        st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=5),
        st.text(alphabet=string.printable, min_size=0, max_size=10),
        max_size=5,
    )
)
def test_environment_detection_ignores_unknown_vars(extras: dict[str, str]) -> None:
    # Junk env vars must not cause spurious detections.
    safe_env = {
        k: v
        for k, v in extras.items()
        if k
        not in {
            "GITHUB_ACTIONS",
            "GITLAB_CI",
            "BUILDKITE",
            "CIRCLECI",
            "JENKINS_URL",
            "DEVCONTAINER",
            "REMOTE_CONTAINERS",
            "INVOCATION_ID",
            "CI",
        }
    }
    matches = detect_environments(env=safe_env, file_exists=lambda _p: False)
    assert matches == []


@settings(deadline=None, max_examples=40)
@given(label=st.text(alphabet=string.ascii_letters + string.digits + "_-", min_size=1, max_size=20))
def test_render_report_handles_arbitrary_labels(label: str) -> None:
    result = DoctorResult(
        name=label,
        category="installation",
        status="ok",
        detail="",
    )
    text = render_report([result])
    assert "Bernstein Doctor" in text


@settings(deadline=None, max_examples=40)
@given(content=st.text(alphabet=string.printable, max_size=200))
def test_load_adapters_from_yaml_never_raises(tmp_path_factory, content: str) -> None:  # type: ignore[no-untyped-def]
    # Even for completely junk content, the YAML loader must return a list.
    p = tmp_path_factory.mktemp("yaml") / "bernstein.yaml"
    try:
        p.write_text(content, encoding="utf-8")
    except UnicodeEncodeError:
        return  # Hypothesis fed us bytes Python cannot write; skip.
    result = _load_adapters_from_yaml(p)
    assert isinstance(result, list)
    assert all(isinstance(x, str) for x in result)


@settings(deadline=None, max_examples=40)
@given(
    statuses=st.lists(st.sampled_from(STATUSES), max_size=20),
)
def test_summarize_is_order_independent(statuses: list[str]) -> None:
    forward = [
        DoctorResult(name=f"x{i}", category="installation", status=s, detail="")  # type: ignore[arg-type]
        for i, s in enumerate(statuses)
    ]
    reverse = list(reversed(forward))
    assert summarize(forward) == summarize(reverse)


@settings(deadline=None, max_examples=40)
@given(results=st.lists(_result_strategy(), max_size=20))
def test_exit_code_only_zero_or_one(results: list[DoctorResult]) -> None:
    assert exit_code_for(results) in (0, 1)
