#!/usr/bin/env python3
"""Fake CLI binary used by adapter integration tests.

Mimics claude / codex / gemini / aider / ollama just enough that the
production adapter code spawns it, captures its output, and processes
its exit code as if it were the real upstream CLI.  The behaviour is
controlled through environment variables so each test can configure the
fake without rewriting the script:

``BERNSTEIN_FAKE_CLI_PROFILE``
    Which CLI to impersonate.  One of ``claude`` / ``codex`` / ``gemini``
    / ``aider`` / ``ollama``.  Falls back to deriving the profile from
    ``argv[0]`` when unset (so a symlink ``claude -> fake_cli.py`` works
    out of the box).

``BERNSTEIN_FAKE_CLI_MODE``
    Behaviour mode.  Recognised values:

    * ``success`` (default) — emit profile-shaped stdout and exit 0.
    * ``error`` — print an error line to stderr and exit ``EXIT_CODE``.
    * ``stream_then_die`` — emit a few stream chunks then exit non-zero.
    * ``hang`` — sleep forever (hits the adapter's timeout watchdog).
    * ``no_output`` — exit 0 without printing anything.

``BERNSTEIN_FAKE_CLI_EXIT_CODE``
    Override the exit code in error/stream_then_die modes.  Default ``2``.

``BERNSTEIN_FAKE_CLI_STDOUT``
    Verbatim stdout body (one line per ``\\n``).  When set, replaces the
    profile's default output.  Lets tests inject deterministic strings.

``BERNSTEIN_FAKE_CLI_STDERR``
    Verbatim stderr body.  Mainly useful for the ``error`` mode.

``BERNSTEIN_FAKE_CLI_DELAY_S``
    Sleep N seconds between the first and last stdout writes (for
    streaming-output tests).  Default ``0``.

``BERNSTEIN_FAKE_CLI_ENV_DUMP``
    When set to a path, the script dumps ``os.environ`` as JSON to that
    file before exiting.  Used by env-isolation tests to assert which
    variables actually crossed into the spawned process.

``BERNSTEIN_FAKE_CLI_ARGV_DUMP``
    When set to a path, the script dumps argv as JSON to that file.
    Used by argv-shape tests where the adapter wraps the CLI through
    bernstein-worker (so the test can recover the inner argv).

The script is intentionally stdlib-only so it runs identically on every
CI runner and inside the bernstein-worker subprocess (which inherits a
hand-built env).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile-specific stdout payloads
# ---------------------------------------------------------------------------

# Claude Code emits NDJSON stream-json events.  The wrapper script piped
# downstream by ``ClaudeCodeAdapter._launch_process`` parses these one
# line at a time.  Three event types are enough to exercise the wrapper
# (assistant text, tool_use, result).
_CLAUDE_STREAM: tuple[dict[str, object], ...] = (
    {"type": "system", "subtype": "init", "session_id": "fake-claude"},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "fake-claude-stream-ok",
                }
            ],
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "result": "fake-claude-result",
        "total_cost_usd": 0.0123,
        "num_turns": 1,
        "duration_ms": 42,
    },
)

# Codex CLI prints JSON status messages to stdout when run with --json.
_CODEX_LINES: tuple[str, ...] = (
    json.dumps({"event": "task.start", "model": "fake-codex"}),
    json.dumps({"event": "task.message", "content": "fake-codex-output"}),
    json.dumps({"event": "task.complete", "exit_code": 0}),
)

# Gemini CLI emits a single JSON object on success.
_GEMINI_PAYLOAD: dict[str, object] = {
    "model": "fake-gemini",
    "response": "fake-gemini-output",
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}

# Aider prints free-form lines.
_AIDER_LINES: tuple[str, ...] = (
    "Aider v0.86.0 (fake)",
    "Repo-map: fake",
    "fake-aider-output",
    "Tokens: 0",
)

# Ollama-via-aider produces aider-shaped output too, but we tag it so
# tests can distinguish profiles.
_OLLAMA_LINES: tuple[str, ...] = (
    "Aider via Ollama (fake)",
    "Model: ollama/fake",
    "fake-ollama-output",
)


_PROFILE_HANDLERS = {
    "claude": "_emit_claude",
    "codex": "_emit_codex",
    "gemini": "_emit_gemini",
    "aider": "_emit_aider",
    "ollama": "_emit_ollama",
}


def _emit_claude() -> None:
    """Print Claude's stream-json NDJSON output."""
    for event in _CLAUDE_STREAM:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()


def _emit_codex() -> None:
    """Print Codex's JSON-line output."""
    for line in _CODEX_LINES:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _emit_gemini() -> None:
    """Print Gemini's single-shot JSON object."""
    sys.stdout.write(json.dumps(_GEMINI_PAYLOAD) + "\n")
    sys.stdout.flush()


def _emit_aider() -> None:
    """Print Aider's free-form output."""
    for line in _AIDER_LINES:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _emit_ollama() -> None:
    """Print Aider-via-Ollama output."""
    for line in _OLLAMA_LINES:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Argument shape validation
# ---------------------------------------------------------------------------


def _validate_argv(profile: str, argv: list[str]) -> None:
    """Sanity-check the argv shape produced by each adapter.

    The check is loose on purpose — we just confirm a few mandatory flags
    survive argv assembly so an adapter regression that drops a flag
    reaches the assertion.
    """
    flags = set(argv)
    if profile == "claude":
        # Adapter must request stream-json output and bypass permissions
        required = {"--output-format", "--permission-mode"}
    elif profile == "codex":
        required = {"exec", "--full-auto", "--json"}
    elif profile == "gemini":
        required = {"-p", "-m", "--yolo"}
    elif profile in {"aider", "ollama"}:
        required = {"--model", "--message", "--yes"}
    else:
        return
    missing = required - flags
    if missing:
        sys.stderr.write(f"fake_cli[{profile}]: missing required flags: {sorted(missing)}\n")
        sys.exit(64)  # EX_USAGE


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _resolve_profile(argv0: str) -> str:
    """Return the profile name based on env var or argv[0] basename."""
    env_profile = os.environ.get("BERNSTEIN_FAKE_CLI_PROFILE", "").strip()
    if env_profile:
        return env_profile
    base = Path(argv0).name.lower()
    if base.endswith(".py"):
        base = base[:-3]
    if base in _PROFILE_HANDLERS:
        return base
    # Fallback: try resolving the symlink (some shells pass the resolved path)
    try:
        resolved = Path(shutil.which(argv0) or argv0).name.lower()
    except OSError:
        resolved = base
    return resolved if resolved in _PROFILE_HANDLERS else "claude"


def _maybe_dump_env() -> None:
    """Write os.environ to ``BERNSTEIN_FAKE_CLI_ENV_DUMP`` if set."""
    dump_path = os.environ.get("BERNSTEIN_FAKE_CLI_ENV_DUMP")
    if not dump_path:
        return
    try:
        Path(dump_path).write_text(
            json.dumps(dict(os.environ), sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        sys.stderr.write(f"fake_cli: env dump failed: {exc}\n")


def _maybe_dump_argv(argv: list[str]) -> None:
    """Write argv to ``BERNSTEIN_FAKE_CLI_ARGV_DUMP`` if set."""
    dump_path = os.environ.get("BERNSTEIN_FAKE_CLI_ARGV_DUMP")
    if not dump_path:
        return
    try:
        Path(dump_path).write_text(json.dumps(argv), encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"fake_cli: argv dump failed: {exc}\n")


def _emit_profile_default(profile: str) -> None:
    """Dispatch to the profile-specific stdout writer."""
    handler_name = _PROFILE_HANDLERS.get(profile)
    if handler_name is None:
        sys.stdout.write(f"fake_cli: unknown profile {profile!r}\n")
        return
    globals()[handler_name]()


def _run_success(profile: str, delay_s: float) -> int:
    """Emit the profile's stdout payload and exit 0."""
    custom = os.environ.get("BERNSTEIN_FAKE_CLI_STDOUT")
    if custom is not None:
        sys.stdout.write(custom)
        if not custom.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        _emit_profile_default(profile)
    if delay_s > 0:
        time.sleep(delay_s)
    return 0


def _run_error(exit_code: int) -> int:
    """Print an error to stderr and return ``exit_code``."""
    err = os.environ.get("BERNSTEIN_FAKE_CLI_STDERR", "fake_cli: simulated upstream error")
    sys.stderr.write(err)
    if not err.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()
    return exit_code


def _run_stream_then_die(profile: str, exit_code: int) -> int:
    """Emit a partial stream then exit non-zero (truncated-output test)."""
    if profile == "claude":
        sys.stdout.write(json.dumps(_CLAUDE_STREAM[0]) + "\n")
        sys.stdout.flush()
    else:
        sys.stdout.write("partial-output-line\n")
        sys.stdout.flush()
    sys.stderr.write("fake_cli: dying mid-stream\n")
    sys.stderr.flush()
    return exit_code


def _run_hang() -> int:
    """Sleep forever — the adapter's timeout watchdog must intervene."""
    while True:
        time.sleep(60)


def main(argv: list[str] | None = None) -> int:
    """Parse env config, dispatch to the requested mode."""
    argv = list(sys.argv if argv is None else argv)
    profile = _resolve_profile(argv[0] if argv else "")

    _maybe_dump_argv(argv)
    _maybe_dump_env()
    _validate_argv(profile, argv)

    mode = os.environ.get("BERNSTEIN_FAKE_CLI_MODE", "success").strip() or "success"
    try:
        exit_code = int(os.environ.get("BERNSTEIN_FAKE_CLI_EXIT_CODE", "2") or "2")
    except ValueError:
        exit_code = 2
    try:
        delay_s = float(os.environ.get("BERNSTEIN_FAKE_CLI_DELAY_S", "0") or "0")
    except ValueError:
        delay_s = 0.0

    if mode == "success":
        return _run_success(profile, delay_s)
    if mode == "no_output":
        return 0
    if mode == "error":
        return _run_error(exit_code)
    if mode == "stream_then_die":
        return _run_stream_then_die(profile, exit_code)
    if mode == "hang":
        return _run_hang()
    sys.stderr.write(f"fake_cli: unknown mode {mode!r}\n")
    return 64


if __name__ == "__main__":
    sys.exit(main())
