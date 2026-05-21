"""Playwright-based self-testing for UI/web agent runs.

When an agent's task touches UI or web code, this module drives a
Playwright browser context against the dev server, captures screenshots,
console messages, and network errors, and (optionally) hands the result
to an LLM judge for verdict. The agent reads the structured self-test
block on its next prompt and revises.

The module is **import-safe** without Playwright installed: the SDK is
imported lazily inside :func:`_import_playwright`. Instantiating
:class:`PlaywrightRunner` does not require the SDK; only calling
:meth:`PlaywrightRunner.run` does.

Public surface:

- :class:`PlaywrightScenario` / :class:`PlaywrightStep` - scenario schema.
- :class:`PlaywrightRunResult` - run output (per-scenario results +
  judge verdict).
- :func:`load_scenarios` - YAML loader.
- :class:`PlaywrightRunner` - orchestrator.

Output layout (under ``output_dir``)::

    output_dir/
      <scenario-id>/
        step-001-navigate.png
        step-002-click.png
        console.jsonl
        network-errors.jsonl
        scenario-result.json
      run-summary.json

Step types supported in v1:

- ``navigate`` - ``{url: str}``
- ``click`` - ``{selector: str}``
- ``type`` - ``{selector: str, text: str}``
- ``assert_visible`` - ``{selector: str}``
- ``screenshot`` - ``{name?: str}``

Out of scope (per ticket): mobile, SSIM diffing, cross-browser. Chromium
only in v1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bernstein.eval.judge import JudgeVerdict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STEP_TYPES = frozenset({"navigate", "click", "type", "assert_visible", "screenshot"})

# Default per-step timeout (milliseconds). Playwright's own default is 30s;
# we mirror that so behaviour matches operator intuition.
DEFAULT_STEP_TIMEOUT_MS = 30_000

# Default browser channel. Chromium-only in v1 per ticket scope.
DEFAULT_BROWSER = "chromium"


StepStatus = Literal["passed", "failed", "skipped"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PlaywrightUnavailableError(RuntimeError):
    """Raised when the ``playwright`` package is not importable.

    Install with: ``pip install playwright && playwright install chromium``.
    """


class PlaywrightScenarioError(ValueError):
    """Raised when a scenario YAML fails to parse or validate."""


# ---------------------------------------------------------------------------
# Lazy SDK import
# ---------------------------------------------------------------------------


def _import_playwright() -> Any:
    """Lazy-import the Playwright async API.

    Returns:
        The ``playwright.async_api`` module.

    Raises:
        PlaywrightUnavailableError: When Playwright is not installed.
    """
    try:
        from playwright import async_api  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via mocks
        raise PlaywrightUnavailableError(
            "Playwright is not installed. Install with: `pip install playwright` then `playwright install chromium`."
        ) from exc
    return cast("Any", async_api)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaywrightStep:
    """A single step inside a scenario.

    Attributes:
        type: One of :data:`VALID_STEP_TYPES`.
        selector: CSS / Playwright selector. Required for click, type,
            assert_visible.
        url: Navigation target. Required for navigate.
        text: Text to type. Required for type.
        name: Optional screenshot file basename. Used by screenshot.
        timeout_ms: Per-step timeout override. Defaults to
            :data:`DEFAULT_STEP_TIMEOUT_MS`.
    """

    type: str
    selector: str | None = None
    url: str | None = None
    text: str | None = None
    name: str | None = None
    timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS

    def validate(self) -> None:
        """Validate the step shape.

        Raises:
            PlaywrightScenarioError: When the step has missing or
                inconsistent fields for its type.
        """
        if self.type not in VALID_STEP_TYPES:
            raise PlaywrightScenarioError(f"Unknown step type {self.type!r}. Valid: {sorted(VALID_STEP_TYPES)}")
        if self.type == "navigate" and not self.url:
            raise PlaywrightScenarioError("navigate step requires 'url'")
        if self.type in {"click", "assert_visible"} and not self.selector:
            raise PlaywrightScenarioError(f"{self.type} step requires 'selector'")
        if self.type == "type" and (not self.selector or self.text is None):
            raise PlaywrightScenarioError("type step requires 'selector' and 'text'")
        if self.timeout_ms <= 0:
            raise PlaywrightScenarioError("timeout_ms must be positive")


@dataclass(frozen=True)
class PlaywrightScenario:
    """A single scenario: a named sequence of steps.

    Attributes:
        name: Human-readable name (also used as the scenario directory).
        steps: Ordered sequence of :class:`PlaywrightStep`.
        expectation: Plain-English description handed to the LLM judge
            describing the user-visible success criterion. Optional.
    """

    name: str
    steps: tuple[PlaywrightStep, ...]
    expectation: str = ""

    def validate(self) -> None:
        """Validate scenario shape.

        Raises:
            PlaywrightScenarioError: When the scenario is malformed.
        """
        if not self.name:
            raise PlaywrightScenarioError("Scenario must have a non-empty 'name'")
        if not self.steps:
            raise PlaywrightScenarioError(f"Scenario {self.name!r} must contain at least one step")
        for index, step in enumerate(self.steps):
            try:
                step.validate()
            except PlaywrightScenarioError as exc:
                raise PlaywrightScenarioError(f"Scenario {self.name!r} step {index + 1}: {exc}") from exc


@dataclass
class StepResult:
    """Per-step execution result.

    Attributes:
        index: 1-based step index.
        type: Step type.
        status: passed | failed | skipped.
        error: Error message when status == failed; ``None`` otherwise.
        screenshot_path: POSIX path of the captured screenshot, if any.
        duration_ms: Wall-clock duration in milliseconds.
    """

    index: int
    type: str
    status: StepStatus
    error: str | None = None
    screenshot_path: str | None = None
    duration_ms: int = 0


@dataclass
class ScenarioResult:
    """Per-scenario execution result.

    Attributes:
        scenario: The scenario that ran.
        passed: True iff every step passed.
        steps: Ordered per-step results.
        console_messages: Captured ``page.on("console")`` payloads.
        network_errors: Captured ``page.on("requestfailed")`` payloads.
        screenshots: POSIX paths of all screenshots produced.
        output_dir: Directory holding the scenario's artefacts.
    """

    scenario: PlaywrightScenario
    passed: bool
    steps: list[StepResult] = field(default_factory=list)
    console_messages: list[dict[str, Any]] = field(default_factory=list)
    network_errors: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    output_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-ready dict."""
        return {
            "name": self.scenario.name,
            "expectation": self.scenario.expectation,
            "passed": self.passed,
            "steps": [asdict(step) for step in self.steps],
            "console_messages": self.console_messages.copy(),
            "network_errors": self.network_errors.copy(),
            "screenshots": self.screenshots.copy(),
            "output_dir": self.output_dir,
        }


@dataclass
class PlaywrightRunResult:
    """Aggregate result for a single :meth:`PlaywrightRunner.run` call.

    Attributes:
        scenarios: Per-scenario results in execution order.
        judge_verdict: Optional LLM judge verdict. ``None`` when the
            runner was invoked with ``judge=None``.
        summary_path: POSIX path to the ``run-summary.json`` file.
    """

    scenarios: list[ScenarioResult]
    judge_verdict: JudgeVerdict | None = None
    summary_path: str = ""

    @property
    def passed(self) -> bool:
        """True iff every scenario passed."""
        return all(scenario.passed for scenario in self.scenarios)

    def to_self_test_block(self) -> str:
        """Render the result as a structured block for the next agent prompt.

        The format is intentionally tight: agents consume this verbatim, so
        every line is one fact. Screenshots are referenced by path; the
        agent's harness is expected to read them out-of-band.
        """
        lines: list[str] = ["## Playwright self-test"]
        lines.append(f"overall: {'PASS' if self.passed else 'FAIL'}")
        for scenario in self.scenarios:
            lines.append("")
            status = "PASS" if scenario.passed else "FAIL"
            lines.append(f"### {scenario.scenario.name} -- {status}")
            if scenario.scenario.expectation:
                lines.append(f"expected: {scenario.scenario.expectation}")
            for step in scenario.steps:
                detail = f"step {step.index} {step.type} -> {step.status}"
                if step.error:
                    detail += f" ({step.error})"
                lines.append(detail)
            if scenario.console_messages:
                lines.append(f"console_messages: {len(scenario.console_messages)}")
            if scenario.network_errors:
                lines.append(f"network_errors: {len(scenario.network_errors)}")
            if scenario.screenshots:
                lines.append("screenshots:")
                for shot in scenario.screenshots:
                    lines.append(f"  - {shot}")
        if self.judge_verdict is not None:
            lines.extend(
                (
                    "",
                    "### Judge verdict",
                    f"verdict: {self.judge_verdict.verdict}",
                    f"correctness: {self.judge_verdict.correctness}",
                    f"style: {self.judge_verdict.style}",
                    f"test_coverage: {self.judge_verdict.test_coverage}",
                    f"safety: {self.judge_verdict.safety}",
                )
            )
            if self.judge_verdict.issues:
                lines.append("issues:")
                for issue in self.judge_verdict.issues:
                    lines.append(f"  - {issue}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------


def load_scenarios(path: Path) -> list[PlaywrightScenario]:
    """Load a list of scenarios from a YAML file.

    The file must be a YAML document with a top-level ``scenarios`` list,
    or a bare list of scenario objects.

    Args:
        path: Path to a YAML file.

    Returns:
        Validated scenarios in declaration order.

    Raises:
        PlaywrightScenarioError: On any parse / validation failure.
        FileNotFoundError: When the path does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Scenarios file not found: {path}")
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PlaywrightScenarioError(f"{path}: invalid YAML: {exc}") from exc
    if raw is None:
        raise PlaywrightScenarioError(f"Scenarios file is empty: {path}")

    items: list[object]
    if isinstance(raw, dict):
        candidate = cast("dict[str, object]", raw).get("scenarios")
        if not isinstance(candidate, list):
            raise PlaywrightScenarioError(f"{path}: top-level 'scenarios' must be a list")
        items = cast("list[object]", candidate)
    elif isinstance(raw, list):
        items = cast("list[object]", raw)
    else:
        raise PlaywrightScenarioError(f"{path}: top-level YAML must be a list or mapping")

    scenarios: list[PlaywrightScenario] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PlaywrightScenarioError(f"{path}: scenario {index + 1} is not a mapping")
        scenarios.append(
            _parse_scenario(
                cast("dict[str, object]", item),
                source=path,
                position=index + 1,
            )
        )
    return scenarios


def _parse_scenario(
    payload: dict[str, object],
    *,
    source: Path,
    position: int,
) -> PlaywrightScenario:
    """Parse one scenario dict into a :class:`PlaywrightScenario`."""
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise PlaywrightScenarioError(f"{source}: scenario {position} is missing 'name'")
    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list):
        raise PlaywrightScenarioError(f"{source}: scenario {name!r} is missing 'steps' list")
    expectation = payload.get("expectation", "")
    if not isinstance(expectation, str):
        raise PlaywrightScenarioError(f"{source}: scenario {name!r} 'expectation' must be a string")
    steps: list[PlaywrightStep] = []
    for step_index, step_raw in enumerate(cast("list[object]", steps_raw)):
        if not isinstance(step_raw, dict):
            raise PlaywrightScenarioError(f"{source}: scenario {name!r} step {step_index + 1} is not a mapping")
        steps.append(_parse_step(cast("dict[str, object]", step_raw)))
    scenario = PlaywrightScenario(
        name=name,
        steps=tuple(steps),
        expectation=expectation,
    )
    scenario.validate()
    return scenario


def _parse_step(payload: dict[str, object]) -> PlaywrightStep:
    """Parse one step dict into a :class:`PlaywrightStep`."""
    step_type = payload.get("type")
    if not isinstance(step_type, str):
        raise PlaywrightScenarioError("Step is missing 'type'")
    timeout_ms_raw = payload.get("timeout_ms", DEFAULT_STEP_TIMEOUT_MS)
    if not isinstance(timeout_ms_raw, int) or isinstance(timeout_ms_raw, bool):
        raise PlaywrightScenarioError("Step 'timeout_ms' must be an integer")
    step = PlaywrightStep(
        type=step_type,
        selector=_optional_str(payload, "selector"),
        url=_optional_str(payload, "url"),
        text=_optional_str(payload, "text"),
        name=_optional_str(payload, "name"),
        timeout_ms=timeout_ms_raw,
    )
    step.validate()
    return step


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    """Return ``payload[key]`` as a string or None when absent."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlaywrightScenarioError(f"Field {key!r} must be a string when present")
    return value


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


# Judge prompt used when the LLM judge is enabled. Kept terse - the
# agent harness, not Bernstein, owns image attachment, so we describe
# the artefacts by reference rather than embedding base64 here.
_JUDGE_PROMPT = """\
You are a UI self-test judge. An agent made code changes and a
Playwright runner exercised the resulting dev server. Decide whether
the visible result matches the expectation.

## Task
{task_description}

## Scenarios
{scenarios_block}

Rate the result on the same 0-5 axes you use for code review:
- correctness: Does the visible result match each scenario's expectation?
- style: Does the rendered UI look intentional (no broken layout, no console errors)?
- test_coverage: Did the scenarios actually exercise the changed surface?
- safety: Were there security-relevant console / network errors?

Respond with ONLY valid JSON in this format:
{{"correctness": 0, "style": 0, "test_coverage": 0, "safety": 0, "verdict": "PASS", "issues": ["..."]}}
"""


class PlaywrightRunner:
    """Drive a Playwright browser context against scenarios.

    The runner is asynchronous: callers ``await runner.run(...)``. The
    Playwright async API is used because the FastAPI server, the
    sandbox subsystem, and the eval harness are already asyncio-native.

    Args:
        base_url: Base URL of the dev server (e.g. ``http://localhost:5173``).
            ``navigate`` steps may supply absolute URLs that override this.
        output_dir: Directory where artefacts (screenshots, console
            logs, network errors, summary) are written. Created on demand.
        browser: Browser channel. Chromium-only in v1.
        headless: Whether to launch headless. Defaults to True.
    """

    def __init__(
        self,
        *,
        base_url: str,
        output_dir: Path,
        browser: str = DEFAULT_BROWSER,
        headless: bool = True,
    ) -> None:
        if browser != "chromium":
            raise ValueError(f"Unsupported browser {browser!r}; chromium only in v1")
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir
        self.browser = browser
        self.headless = headless

    async def run(
        self,
        scenarios: Sequence[PlaywrightScenario],
        *,
        task_description: str = "",
        judge: Any = None,
    ) -> PlaywrightRunResult:
        """Execute every scenario and optionally hand the result to a judge.

        Args:
            scenarios: Scenarios to execute, in order.
            task_description: What the agent was asked to do. Forwarded
                to the judge prompt when ``judge`` is set.
            judge: Optional object exposing the
                :class:`bernstein.eval.judge.EvalJudge` interface. When
                supplied, ``judge.dual_attempt(prompt)`` is awaited and
                its verdict is attached to the result.

        Returns:
            :class:`PlaywrightRunResult`.

        Raises:
            PlaywrightUnavailableError: When Playwright is not installed.
        """
        if not scenarios:
            raise ValueError("At least one scenario is required")

        playwright_api = _import_playwright()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        results: list[ScenarioResult] = []
        async with playwright_api.async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=self.headless)
            try:
                for scenario in scenarios:
                    result = await self._run_scenario(browser, scenario)
                    self._persist_scenario(result)
                    results.append(result)
            finally:
                await browser.close()

        verdict = None
        if judge is not None:
            verdict = await self._invoke_judge(
                judge=judge,
                task_description=task_description,
                results=results,
            )

        run_result = PlaywrightRunResult(scenarios=results, judge_verdict=verdict)
        run_result.summary_path = self._persist_summary(run_result)
        return run_result

    # -- internal helpers --------------------------------------------------

    async def _run_scenario(
        self,
        browser: Any,
        scenario: PlaywrightScenario,
    ) -> ScenarioResult:
        """Execute one scenario against a fresh browser context."""
        scenario_dir = self.output_dir / _slugify(scenario.name)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        result = ScenarioResult(
            scenario=scenario,
            passed=False,
            output_dir=scenario_dir.as_posix(),
        )

        context = await browser.new_context()
        page = await context.new_page()
        _wire_capture(page, result)

        all_passed = True
        try:
            for index, step in enumerate(scenario.steps, start=1):
                step_result = await self._run_step(
                    page=page,
                    step=step,
                    index=index,
                    scenario_dir=scenario_dir,
                )
                result.steps.append(step_result)
                if step_result.screenshot_path:
                    result.screenshots.append(step_result.screenshot_path)
                if step_result.status == "failed":
                    all_passed = False
                    # Stop on first failure: subsequent steps probably
                    # depend on UI state that did not materialise.
                    break
        finally:
            await context.close()

        result.passed = all_passed and bool(result.steps)
        return result

    async def _run_step(
        self,
        *,
        page: Any,
        step: PlaywrightStep,
        index: int,
        scenario_dir: Path,
    ) -> StepResult:
        """Execute a single step and capture a screenshot afterwards."""
        import time

        started_at = time.monotonic()
        try:
            await self._dispatch_step(page=page, step=step)
            status: StepStatus = "passed"
            error: str | None = None
        except asyncio.CancelledError:
            # Cooperative task cancellation must propagate; do not record
            # the step as a benign "failed" or it masks the cancel signal
            # and prevents the parent task from cleaning up promptly.
            raise
        except Exception as exc:
            logger.warning(
                "Playwright step %d (%s) failed: %s",
                index,
                step.type,
                exc,
                exc_info=True,
            )
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        finally:
            duration_ms = int((time.monotonic() - started_at) * 1000)

        screenshot_path: str | None = None
        # Always screenshot on screenshot steps. On failures we also
        # screenshot so the judge can diagnose what went wrong.
        if step.type == "screenshot" or status == "failed":
            file_name = step.name or f"step-{index:03d}-{step.type}.png"
            screenshot_path = await _capture_screenshot(page, scenario_dir / file_name)

        return StepResult(
            index=index,
            type=step.type,
            status=status,
            error=error,
            screenshot_path=screenshot_path,
            duration_ms=duration_ms,
        )

    async def _dispatch_step(self, *, page: Any, step: PlaywrightStep) -> None:
        """Dispatch the step to the appropriate Playwright primitive."""
        timeout = step.timeout_ms
        if step.type == "navigate":
            target = self._resolve_url(step.url or "")
            await page.goto(target, timeout=timeout)
        elif step.type == "click":
            await page.click(step.selector, timeout=timeout)
        elif step.type == "type":
            # Playwright recommends ``fill`` for deterministic typing.
            await page.fill(step.selector, step.text or "", timeout=timeout)
        elif step.type == "assert_visible":
            locator = page.locator(step.selector)
            await locator.wait_for(state="visible", timeout=timeout)
        elif step.type == "screenshot":
            # Screenshot is captured by the surrounding step harness.
            return
        else:  # pragma: no cover - guarded by validate()
            raise PlaywrightScenarioError(f"Unhandled step type {step.type!r}")

    def _resolve_url(self, url: str) -> str:
        """Resolve a step's URL against the configured base URL."""
        if url.startswith(("http://", "https://")):
            return url
        if not url.startswith("/"):
            url = "/" + url
        return f"{self.base_url}{url}"

    def _persist_scenario(self, result: ScenarioResult) -> None:
        """Write scenario-level artefacts to ``output_dir``."""
        scenario_dir = Path(result.output_dir)
        (scenario_dir / "console.jsonl").write_text(
            "\n".join(json.dumps(msg) for msg in result.console_messages),
            encoding="utf-8",
        )
        (scenario_dir / "network-errors.jsonl").write_text(
            "\n".join(json.dumps(err) for err in result.network_errors),
            encoding="utf-8",
        )
        (scenario_dir / "scenario-result.json").write_text(
            json.dumps(result.to_dict(), indent=2),
            encoding="utf-8",
        )

    def _persist_summary(self, run_result: PlaywrightRunResult) -> str:
        """Write the aggregate ``run-summary.json`` and return its path."""
        summary = {
            "passed": run_result.passed,
            "scenarios": [scenario.to_dict() for scenario in run_result.scenarios],
            "judge_verdict": (
                _judge_to_dict(run_result.judge_verdict) if run_result.judge_verdict is not None else None
            ),
        }
        summary_path = self.output_dir / "run-summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary_path.as_posix()

    async def _invoke_judge(
        self,
        *,
        judge: Any,
        task_description: str,
        results: list[ScenarioResult],
    ) -> JudgeVerdict | None:
        """Build the judge prompt and dispatch to the supplied judge."""
        scenarios_block = "\n\n".join(_render_scenario_for_judge(r) for r in results)
        prompt = _JUDGE_PROMPT.format(
            task_description=task_description or "(not supplied)",
            scenarios_block=scenarios_block,
        )
        try:
            return await judge.dual_attempt(prompt)
        except asyncio.CancelledError:
            # Cancellation must propagate so the caller can tear down the
            # judge attempt cleanly; do not swallow it as a generic
            # judge failure.
            raise
        except Exception:
            logger.exception("Playwright judge call failed")
            return None


# ---------------------------------------------------------------------------
# Capture wiring + helpers
# ---------------------------------------------------------------------------


def _wire_capture(page: Any, result: ScenarioResult) -> None:
    """Hook console + network listeners onto the page."""

    def _on_console(msg: Any) -> None:
        try:
            result.console_messages.append(
                {
                    "type": getattr(msg, "type", "log"),
                    "text": getattr(msg, "text", ""),
                    "location": getattr(msg, "location", None),
                }
            )
        except Exception:
            logger.debug("Failed to record console message", exc_info=True)

    def _on_requestfailed(request: Any) -> None:
        try:
            failure = getattr(request, "failure", None)
            result.network_errors.append(
                {
                    "url": getattr(request, "url", ""),
                    "method": getattr(request, "method", ""),
                    "failure": str(failure) if failure else "",
                    "resource_type": getattr(request, "resource_type", ""),
                }
            )
        except Exception:
            logger.debug("Failed to record requestfailed event", exc_info=True)

    page.on("console", _on_console)
    page.on("requestfailed", _on_requestfailed)


async def _capture_screenshot(page: Any, target: Path) -> str | None:
    """Capture a full-page screenshot.

    Returns:
        The POSIX path on success, ``None`` when capture failed. ``None`` so
        callers can distinguish a missing screenshot from a recorded one
        without treating an empty string as a valid path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(target), full_page=True)
    except asyncio.CancelledError:
        # Propagate cancellation; a partial screenshot is not worth
        # masking the cancel.
        raise
    except Exception:
        logger.exception("Failed to capture screenshot to %s", target)
        return None
    return target.as_posix()


def _slugify(value: str) -> str:
    """Make a filesystem-safe slug from a scenario name."""
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "scenario"


def _render_scenario_for_judge(result: ScenarioResult) -> str:
    """Render a scenario block for the judge prompt."""
    lines = [
        f"Scenario: {result.scenario.name}",
        f"Result: {'PASS' if result.passed else 'FAIL'}",
    ]
    if result.scenario.expectation:
        lines.append(f"Expectation: {result.scenario.expectation}")
    lines.append("Steps:")
    for step in result.steps:
        line = f"  - {step.index} {step.type} -> {step.status}"
        if step.error:
            line += f" ({step.error})"
        lines.append(line)
    if result.screenshots:
        lines.append("Screenshots:")
        for shot in result.screenshots:
            lines.append(f"  - {shot}")
    if result.console_messages:
        lines.append(f"Console messages: {len(result.console_messages)}")
    if result.network_errors:
        lines.append(f"Network errors: {len(result.network_errors)}")
    return "\n".join(lines)


def _judge_to_dict(verdict: JudgeVerdict) -> dict[str, Any]:
    """Serialise a :class:`JudgeVerdict` to a JSON-ready dict."""
    return {
        "correctness": verdict.correctness,
        "style": verdict.style,
        "test_coverage": verdict.test_coverage,
        "safety": verdict.safety,
        "verdict": verdict.verdict,
        "issues": list(verdict.issues),
    }


__all__ = [
    "DEFAULT_BROWSER",
    "DEFAULT_STEP_TIMEOUT_MS",
    "VALID_STEP_TYPES",
    "PlaywrightRunResult",
    "PlaywrightRunner",
    "PlaywrightScenario",
    "PlaywrightScenarioError",
    "PlaywrightStep",
    "PlaywrightUnavailableError",
    "ScenarioResult",
    "StepResult",
    "load_scenarios",
]
