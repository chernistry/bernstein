"""Unit tests for :mod:`bernstein.core.sandbox.playwright_runner`.

The Playwright SDK is not a hard project dependency, so every test in
this module mocks the SDK out at the lazy-import boundary
(:func:`bernstein.core.sandbox.playwright_runner._import_playwright`).
The mocks emulate the slice of the ``playwright.async_api`` surface
that the runner actually touches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bernstein.core.sandbox import playwright_runner
from bernstein.core.sandbox.playwright_runner import (
    PlaywrightRunner,
    PlaywrightScenario,
    PlaywrightScenarioError,
    PlaywrightStep,
    PlaywrightUnavailableError,
    load_scenarios,
)

# ---------------------------------------------------------------------------
# Mock Playwright surface
# ---------------------------------------------------------------------------


class _FakeLocator:
    def __init__(self) -> None:
        self.wait_for = AsyncMock()


class _FakePage:
    """Stand-in for ``playwright.async_api.Page``.

    Records every call so tests can assert on dispatched primitives. Event
    listeners attached via :meth:`on` are stored so tests can drive them
    by hand.
    """

    def __init__(self) -> None:
        self.goto = AsyncMock()
        self.click = AsyncMock()
        self.fill = AsyncMock()
        self.screenshot = AsyncMock()
        self.locator = MagicMock(side_effect=lambda _selector: _FakeLocator())
        self._handlers: dict[str, list[Any]] = {"console": [], "requestfailed": []}

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit_console(self, msg: Any) -> None:
        for handler in self._handlers.get("console", []):
            handler(msg)

    def emit_requestfailed(self, request: Any) -> None:
        for handler in self._handlers.get("requestfailed", []):
            handler(request)


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.new_page = AsyncMock(return_value=page)
        self.close = AsyncMock()


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.close = AsyncMock()

    async def new_context(self) -> _FakeContext:
        return _FakeContext(self._page)


class _FakeChromiumLauncher:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.launch = AsyncMock(return_value=_FakeBrowser(page))


class _FakePlaywrightRuntime:
    def __init__(self, page: _FakePage) -> None:
        self.chromium = _FakeChromiumLauncher(page)


class _FakeAsyncPlaywrightCtx:
    def __init__(self, page: _FakePage) -> None:
        self._runtime = _FakePlaywrightRuntime(page)

    async def __aenter__(self) -> _FakePlaywrightRuntime:
        return self._runtime

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    page: _FakePage | None = None,
) -> _FakePage:
    """Install a fake ``playwright.async_api`` module into the runner."""
    pg = page or _FakePage()
    fake_module = MagicMock()
    fake_module.async_playwright = lambda: _FakeAsyncPlaywrightCtx(pg)
    monkeypatch.setattr(playwright_runner, "_import_playwright", lambda: fake_module)
    return pg


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_step_validate_rejects_unknown_type() -> None:
    with pytest.raises(PlaywrightScenarioError, match="Unknown step type"):
        PlaywrightStep(type="teleport").validate()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"type": "navigate"}, "navigate step requires 'url'"),
        ({"type": "click"}, "click step requires 'selector'"),
        ({"type": "assert_visible"}, "assert_visible step requires 'selector'"),
        ({"type": "type", "selector": "#a"}, "type step requires 'selector' and 'text'"),
        ({"type": "type", "text": "x"}, "type step requires 'selector' and 'text'"),
        ({"type": "navigate", "url": "/x", "timeout_ms": 0}, "timeout_ms must be positive"),
    ],
)
def test_step_validate_field_requirements(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(PlaywrightScenarioError, match=match):
        PlaywrightStep(**kwargs).validate()


def test_scenario_validate_requires_name_and_steps() -> None:
    with pytest.raises(PlaywrightScenarioError, match="non-empty 'name'"):
        PlaywrightScenario(name="", steps=()).validate()
    with pytest.raises(PlaywrightScenarioError, match="at least one step"):
        PlaywrightScenario(name="x", steps=()).validate()


def test_scenario_validate_propagates_step_index() -> None:
    bad = PlaywrightScenario(
        name="checkout",
        steps=(
            PlaywrightStep(type="navigate", url="/"),
            PlaywrightStep(type="click"),  # missing selector
        ),
    )
    with pytest.raises(PlaywrightScenarioError, match="step 2"):
        bad.validate()


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------


def test_load_scenarios_supports_top_level_list(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.yaml"
    path.write_text(
        """\
- name: home
  expectation: home page renders
  steps:
    - {type: navigate, url: /}
    - {type: assert_visible, selector: h1}
""",
        encoding="utf-8",
    )
    scenarios = load_scenarios(path)
    assert len(scenarios) == 1
    assert scenarios[0].name == "home"
    assert scenarios[0].steps[0].url == "/"


def test_load_scenarios_supports_mapping(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.yaml"
    path.write_text(
        """\
scenarios:
  - name: signup
    steps:
      - {type: navigate, url: /signup}
      - {type: type, selector: '#email', text: a@b.co}
      - {type: click, selector: 'button[type=submit]'}
""",
        encoding="utf-8",
    )
    scenarios = load_scenarios(path)
    assert scenarios[0].name == "signup"
    assert scenarios[0].steps[1].text == "a@b.co"


def test_load_scenarios_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_scenarios(tmp_path / "missing.yaml")


def test_load_scenarios_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(PlaywrightScenarioError, match="empty"):
        load_scenarios(path)


def test_load_scenarios_rejects_non_mapping_step(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """\
- name: x
  steps:
    - "navigate"
""",
        encoding="utf-8",
    )
    with pytest.raises(PlaywrightScenarioError, match="step 1 is not a mapping"):
        load_scenarios(path)


def test_load_scenarios_normalises_yaml_parse_errors(tmp_path: Path) -> None:
    # Malformed YAML must surface as PlaywrightScenarioError so the CLI's
    # except clause converts it into a clean ClickException.
    path = tmp_path / "broken.yaml"
    path.write_text("scenarios: [name: x\n  steps: -\n", encoding="utf-8")
    with pytest.raises(PlaywrightScenarioError, match="invalid YAML"):
        load_scenarios(path)


# ---------------------------------------------------------------------------
# Runner - happy path + capture
# ---------------------------------------------------------------------------


def _make_scenario() -> PlaywrightScenario:
    return PlaywrightScenario(
        name="Home page renders",
        expectation="hero text is visible",
        steps=(
            PlaywrightStep(type="navigate", url="/"),
            PlaywrightStep(type="assert_visible", selector="h1"),
            PlaywrightStep(type="screenshot", name="hero.png"),
        ),
    )


@pytest.mark.asyncio
async def test_runner_executes_all_steps_and_writes_artefacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _install_fake_playwright(monkeypatch)
    runner = PlaywrightRunner(
        base_url="http://localhost:5173",
        output_dir=tmp_path,
    )

    # Drive the runner; emit a console message after navigate runs.
    async def fake_goto(*_args: Any, **_kwargs: Any) -> None:
        console_msg = MagicMock()
        console_msg.type = "log"
        console_msg.text = "hello"
        console_msg.location = None
        page.emit_console(console_msg)

    page.goto.side_effect = fake_goto

    # Ensure screenshot path is created so the runner records it.
    async def fake_screenshot(path: str, full_page: bool) -> None:
        _ = full_page
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG fake")

    page.screenshot.side_effect = fake_screenshot

    result = await runner.run([_make_scenario()], task_description="render home")

    assert result.passed is True
    assert len(result.scenarios) == 1
    scenario_result = result.scenarios[0]
    assert scenario_result.passed is True
    assert [step.type for step in scenario_result.steps] == [
        "navigate",
        "assert_visible",
        "screenshot",
    ]
    assert all(step.status == "passed" for step in scenario_result.steps)
    assert len(scenario_result.console_messages) == 1
    assert scenario_result.console_messages[0]["text"] == "hello"

    # navigate URL was resolved against the base URL.
    page.goto.assert_awaited_once()
    args, kwargs = page.goto.await_args
    assert args[0] == "http://localhost:5173/"
    assert kwargs["timeout"] == playwright_runner.DEFAULT_STEP_TIMEOUT_MS

    # Screenshot step produced a file under the scenario dir.
    assert scenario_result.screenshots, "screenshot step must record a path"
    shot = Path(scenario_result.screenshots[0])
    assert shot.exists()
    assert shot.name == "hero.png"

    # Summary file is written and parses as JSON.
    summary_path = Path(result.summary_path)
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert summary["judge_verdict"] is None

    # The structured block can be embedded back into the next agent prompt.
    block = result.to_self_test_block()
    assert "overall: PASS" in block
    assert "Home page renders" in block


@pytest.mark.asyncio
async def test_runner_records_step_failure_and_screenshots_diagnostically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _install_fake_playwright(monkeypatch)
    page.click.side_effect = RuntimeError("element not found")

    async def fake_screenshot(path: str, full_page: bool) -> None:
        _ = full_page
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")

    page.screenshot.side_effect = fake_screenshot

    scenario = PlaywrightScenario(
        name="login",
        steps=(
            PlaywrightStep(type="navigate", url="/login"),
            PlaywrightStep(type="click", selector="button#submit"),
            PlaywrightStep(type="assert_visible", selector="h2"),  # never reached
        ),
    )

    runner = PlaywrightRunner(base_url="http://localhost:5173", output_dir=tmp_path)
    result = await runner.run([scenario])

    assert result.passed is False
    scenario_result = result.scenarios[0]
    assert scenario_result.passed is False
    statuses = [step.status for step in scenario_result.steps]
    # navigate passed, click failed, assert_visible was skipped.
    assert statuses == ["passed", "failed"]
    failed = scenario_result.steps[1]
    assert failed.error is not None
    assert "element not found" in failed.error
    # Diagnostic screenshot is recorded for the failed step.
    assert failed.screenshot_path
    assert Path(failed.screenshot_path).exists()


@pytest.mark.asyncio
async def test_runner_captures_network_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _install_fake_playwright(monkeypatch)

    async def fake_goto(*_args: Any, **_kwargs: Any) -> None:
        request = MagicMock()
        request.url = "https://api/x"
        request.method = "GET"
        request.failure = "net::ERR_FAILED"
        request.resource_type = "fetch"
        page.emit_requestfailed(request)

    page.goto.side_effect = fake_goto

    scenario = PlaywrightScenario(
        name="api",
        steps=(PlaywrightStep(type="navigate", url="/"),),
    )
    runner = PlaywrightRunner(base_url="http://localhost:5173", output_dir=tmp_path)
    result = await runner.run([scenario])

    errors = result.scenarios[0].network_errors
    assert len(errors) == 1
    assert errors[0]["url"] == "https://api/x"
    assert "ERR_FAILED" in errors[0]["failure"]


# ---------------------------------------------------------------------------
# Runner - judge integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_invokes_judge_and_attaches_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bernstein.eval.judge import JudgeVerdict

    _install_fake_playwright(monkeypatch)
    judge = MagicMock()
    expected = JudgeVerdict(
        correctness=4,
        style=3,
        test_coverage=4,
        safety=5,
        verdict="PASS",
        issues=[],
    )
    judge.dual_attempt = AsyncMock(return_value=expected)

    scenario = PlaywrightScenario(
        name="home",
        expectation="renders hero",
        steps=(PlaywrightStep(type="navigate", url="/"),),
    )
    runner = PlaywrightRunner(base_url="http://localhost:5173", output_dir=tmp_path)
    result = await runner.run([scenario], task_description="ship home page", judge=judge)

    assert result.judge_verdict is expected
    judge.dual_attempt.assert_awaited_once()
    prompt = judge.dual_attempt.await_args.args[0]
    assert "ship home page" in prompt
    assert "renders hero" in prompt

    block = result.to_self_test_block()
    assert "Judge verdict" in block
    assert "verdict: PASS" in block
    # All four judge axes (including test_coverage) surface in the block so
    # the agent can react to coverage feedback as well as correctness.
    assert "test_coverage: 4" in block


@pytest.mark.asyncio
async def test_runner_swallows_judge_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_playwright(monkeypatch)
    judge = MagicMock()
    judge.dual_attempt = AsyncMock(side_effect=RuntimeError("boom"))

    scenario = PlaywrightScenario(
        name="home",
        steps=(PlaywrightStep(type="navigate", url="/"),),
    )
    runner = PlaywrightRunner(base_url="http://localhost:5173", output_dir=tmp_path)
    result = await runner.run([scenario], judge=judge)
    assert result.judge_verdict is None
    assert result.passed is True


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_runner_rejects_non_chromium_browser(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="chromium only"):
        PlaywrightRunner(
            base_url="http://localhost",
            output_dir=tmp_path,
            browser="webkit",
        )


@pytest.mark.asyncio
async def test_runner_requires_at_least_one_scenario(tmp_path: Path) -> None:
    runner = PlaywrightRunner(base_url="http://localhost", output_dir=tmp_path)
    with pytest.raises(ValueError, match="At least one scenario"):
        await runner.run([])


@pytest.mark.asyncio
async def test_runner_surfaces_unavailable_playwright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> Any:
        raise PlaywrightUnavailableError("install playwright")

    monkeypatch.setattr(playwright_runner, "_import_playwright", _raise)
    runner = PlaywrightRunner(base_url="http://localhost", output_dir=tmp_path)
    scenario = PlaywrightScenario(
        name="x",
        steps=(PlaywrightStep(type="navigate", url="/"),),
    )
    with pytest.raises(PlaywrightUnavailableError):
        await runner.run([scenario])


def test_cli_rejects_unsafe_task_id(tmp_path: Path) -> None:
    """task_id with path separators or traversal must be rejected upfront."""
    from click.testing import CliRunner

    from bernstein.cli.commands.sandbox_cmd import sandbox_group

    scenarios = tmp_path / "scenarios.yaml"
    scenarios.write_text(
        "- {name: x, steps: [{type: navigate, url: /}]}",
        encoding="utf-8",
    )
    runner = CliRunner()
    bad_inputs = ["../escape", "task/with/slash", "..", ""]
    for bad in bad_inputs:
        result = runner.invoke(
            sandbox_group,
            [
                "web-test",
                bad,
                "--url",
                "http://localhost:5173",
                "--scenarios",
                str(scenarios),
            ],
        )
        assert result.exit_code != 0, f"task_id {bad!r} should be rejected"
        # Empty string trips Click's "argument required" check before our
        # regex, but every non-empty bad input must hit BadParameter.
        if bad:
            assert "task_id must match" in result.output


def test_run_result_self_test_block_includes_failure_details(tmp_path: Path) -> None:
    from bernstein.core.sandbox.playwright_runner import (
        PlaywrightRunResult,
        ScenarioResult,
        StepResult,
    )

    scenario = PlaywrightScenario(
        name="failing",
        steps=(PlaywrightStep(type="navigate", url="/"),),
    )
    scenario_result = ScenarioResult(
        scenario=scenario,
        passed=False,
        steps=[
            StepResult(
                index=1,
                type="navigate",
                status="failed",
                error="TimeoutError: 30000ms",
            )
        ],
        output_dir=str(tmp_path),
    )
    run_result = PlaywrightRunResult(scenarios=[scenario_result])
    block = run_result.to_self_test_block()
    assert "overall: FAIL" in block
    assert "TimeoutError" in block
