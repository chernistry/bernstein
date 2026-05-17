"""Adapter contract loader and capability checker.

For every Bernstein adapter we ship a YAML contract under
``tests/contract/contracts/<adapter>.yaml`` describing the *required*
surface of the upstream CLI binary — the flags and subcommands the
adapter always passes when it invokes the CLI.

This module loads those contracts and asserts the local binary's
``--help`` output still advertises every required token. When a secret
named by ``auth.secret_env`` is set and the contract lists required
models, we additionally run the CLI's configured model-list command
and check each entry of ``expected_models.required_present`` appears.

Design notes (refined per issue #1291):

* **Capability assertions only.** We do not snapshot ``--help`` output.
  Upstream CLIs reshuffle their help text frequently; a literal-byte
  diff produces noise that overwhelms the rare real regression.
* **Drift is a hard fail.** Missing required flag -> exit 2. There is
  no daily-batched "auto-fix" PR.
* **No new repo secrets required.** Adapters whose model-presence check
  needs a secret degrade to help-only coverage when the secret is
  absent; the workflow records that fact for operator visibility.

Refs: #1291.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo-root anchor. We compute the repo root from this file's location so
# the loader works under editable installs and from the wheel-installed
# package (in which case the contracts simply aren't packaged and the
# loader raises FileNotFoundError, the expected behaviour off-dev).
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
CONTRACTS_DIR = _REPO_ROOT / "tests" / "contract" / "contracts"

# Per-subprocess timeouts. Plenty for any well-behaved CLI.
_HELP_TIMEOUT_SECONDS = 30
_MODELS_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ContractSpec:
    """Parsed contract YAML for a single adapter."""

    adapter: str
    binary: str
    install_method: str
    install_spec: str
    auth_required_for_help: bool
    auth_required_for_models: bool
    auth_secret_env: str
    required_flags: tuple[str, ...]
    required_subcommands: tuple[str, ...]
    help_command: tuple[str, ...]
    models_command: tuple[str, ...]
    models_required_present: tuple[str, ...]

    @classmethod
    def load(cls, name: str, contracts_dir: Path | None = None) -> ContractSpec:
        """Load a contract by adapter name."""
        base = contracts_dir if contracts_dir is not None else CONTRACTS_DIR
        path = base / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No contract found for adapter {name!r} at {path}")
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}

        install = data.get("install") or {}
        auth = data.get("auth") or {}
        expected = data.get("expected_models") or {}
        return cls(
            adapter=str(data.get("adapter", name)),
            binary=str(data.get("binary", name)),
            install_method=str(install.get("method", "")),
            install_spec=str(install.get("spec", "")),
            auth_required_for_help=bool(auth.get("required_for_help", False)),
            auth_required_for_models=bool(auth.get("required_for_models", False)),
            auth_secret_env=str(auth.get("secret_env", "") or ""),
            required_flags=tuple(data.get("required_flags") or ()),
            required_subcommands=tuple(data.get("required_subcommands") or ()),
            help_command=tuple(data.get("help_command") or ()),
            models_command=tuple(expected.get("command") or ()),
            models_required_present=tuple(expected.get("required_present") or ()),
        )

    def resolved_help_command(self) -> list[str]:
        """The argv to run for the capability check.

        Defaults to ``[binary, "--help"]``. Contracts whose flags live
        under a subcommand can override this with an explicit
        ``help_command`` list (typically ``[binary, "<sub>", "--help"]``).
        """
        if self.help_command:
            return list(self.help_command)
        return [self.binary, "--help"]


@dataclass
class ContractResult:
    """Outcome of running ``check_contract``."""

    adapter: str
    binary: str
    binary_installed: bool
    help_exit_code: int = 0
    capability_failures: list[str] = field(default_factory=list)
    model_failures: list[str] = field(default_factory=list)
    models_checked: bool = False
    skipped_reason: str = ""

    @property
    def passed(self) -> bool:
        """True when binary is present and no capability/model failures."""
        if not self.binary_installed:
            return False
        return not self.capability_failures and not self.model_failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "binary": self.binary,
            "binary_installed": self.binary_installed,
            "help_exit_code": self.help_exit_code,
            "capability_failures": list(self.capability_failures),
            "model_failures": list(self.model_failures),
            "models_checked": self.models_checked,
            "skipped_reason": self.skipped_reason,
            "passed": self.passed,
        }


# Subprocess helpers --------------------------------------------------------


def _sandbox_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal env for help/model subprocesses.

    Equivalent to ``env -i`` plus the runtime variables a CLI typically
    needs (``PATH``, ``HOME``, locale, ``TERM``). Auth-bearing variables
    are passed through only when ``extra`` opts them in — the help check
    deliberately runs without auth.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "LOGNAME")
    env: dict[str, str] = {}
    for key in keep:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    # Discourage CLIs from phoning home or updating themselves.
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("DO_NOT_TRACK", "1")
    env.setdefault("TERM", "dumb")
    if extra:
        env.update(extra)
    return env


def _run_capture(
    cmd: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run ``cmd``, capture combined stdout+stderr. Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env if env is not None else _sandbox_env(),
            check=False,
        )
    except FileNotFoundError:
        return 127, f"<binary {cmd[0]!r} not found in PATH>\n"
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout or ""
        partial_err = exc.stderr or ""
        if isinstance(partial_out, bytes):  # pragma: no cover -- defensive
            partial_out = partial_out.decode("utf-8", errors="replace")
        if isinstance(partial_err, bytes):  # pragma: no cover -- defensive
            partial_err = partial_err.decode("utf-8", errors="replace")
        return 124, partial_out + partial_err + f"\n<timeout after {timeout}s>\n"
    except OSError as exc:
        return 1, f"<exec error: {exc}>\n"
    combined = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, combined


# Capability evaluation -----------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _capability_failures(spec: ContractSpec, help_text: str) -> list[str]:
    """Compute the list of human-readable capability failures.

    Flag match is case-insensitive substring. The leading dashes already
    make a flag unambiguous. Subcommand match is case-insensitive and
    requires a token boundary (start/end of line or whitespace) so that
    ``runs`` does not falsely satisfy ``run``.
    """
    failures: list[str] = []
    haystack = _strip_ansi(help_text)
    haystack_lower = haystack.lower()
    for flag in spec.required_flags:
        if flag.lower() not in haystack_lower:
            failures.append(f"missing required flag {flag!r} in `{spec.binary} --help`")
    for sub in spec.required_subcommands:
        pattern = rf"(?im)(^|\s){re.escape(sub)}(\s|$)"
        if not re.search(pattern, haystack):
            failures.append(f"missing required subcommand {sub!r} in `{spec.binary} --help`")
    return failures


def _model_failures(spec: ContractSpec, models_text: str) -> list[str]:
    """List required models missing from the CLI's model-list output."""
    failures: list[str] = []
    haystack = _strip_ansi(models_text).lower()
    for model in spec.models_required_present:
        if model.lower() not in haystack:
            failures.append(f"model {model!r} not present in `{' '.join(spec.models_command)}` output")
    return failures


def _secret_present(env_name: str) -> bool:
    """True iff a non-empty env var with that name is set."""
    if not env_name:
        return False
    value = os.environ.get(env_name)
    return bool(value and value.strip())


# Top-level checker ---------------------------------------------------------


def check_contract(spec: ContractSpec) -> ContractResult:
    """Evaluate the contract against the local environment.

    Returns a populated ``ContractResult``. The function never raises:
    every failure mode lands in ``capability_failures`` /
    ``model_failures`` / ``skipped_reason``.
    """
    result = ContractResult(adapter=spec.adapter, binary=spec.binary, binary_installed=False)

    if not spec.binary:
        result.skipped_reason = "contract has no binary"
        return result

    binary_path = shutil.which(spec.binary)
    if binary_path is None:
        result.skipped_reason = f"{spec.binary} not installed"
        return result
    result.binary_installed = True

    # 1. ``<cli> --help`` must succeed and advertise every required token.
    if spec.auth_required_for_help and not _secret_present(spec.auth_secret_env):
        result.skipped_reason = f"--help requires {spec.auth_secret_env or '<auth>'} which is unset; skipping"
        return result

    rc, help_text = _run_capture(spec.resolved_help_command(), timeout=_HELP_TIMEOUT_SECONDS)
    result.help_exit_code = rc
    if rc == 127:
        # Race between shutil.which() and spawn — extremely rare but
        # we report it cleanly.
        result.binary_installed = False
        result.skipped_reason = help_text.strip()
        return result

    result.capability_failures = _capability_failures(spec, help_text)

    # 2. Optional model-presence check.
    if spec.models_required_present and spec.models_command:
        if spec.auth_required_for_models and not _secret_present(spec.auth_secret_env):
            # Coverage degrades to help-only; the workflow records this
            # so operators can decide whether to add the secret.
            result.skipped_reason = f"model check needs {spec.auth_secret_env}; running help-only"
        else:
            extra_env: dict[str, str] = {}
            if spec.auth_secret_env:
                value = os.environ.get(spec.auth_secret_env)
                if value is not None:
                    extra_env[spec.auth_secret_env] = value
            models_env = _sandbox_env(extra_env)
            rc_m, models_text = _run_capture(
                list(spec.models_command),
                timeout=_MODELS_TIMEOUT_SECONDS,
                env=models_env,
            )
            result.models_checked = rc_m == 0
            if rc_m != 0:
                result.model_failures.append(
                    f"`{' '.join(spec.models_command)}` exited {rc_m}: {models_text.strip()[:200]}"
                )
            else:
                result.model_failures = _model_failures(spec, models_text)

    return result


def list_contracts(contracts_dir: Path | None = None) -> list[str]:
    """Return the sorted list of adapter names with a contract on disk."""
    base = contracts_dir if contracts_dir is not None else CONTRACTS_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Capability matrix — resume-from-checkpoint (feat-resume-from-checkpoint)
# ---------------------------------------------------------------------------
#
# Adapters opt into the resume protocol by implementing
# :py:meth:`bernstein.adapters.base.CLIAdapter.resume`. The default
# implementation declines via :data:`RESUME_FALLBACK_FRESH`, which signals
# the CLI to spawn a fresh session and reinject the recovered scratchpad
# (see ``bernstein.core.persistence.resume_prompt``).
#
# Keep this table in sync with adapter overrides. It is consulted by
# ``bernstein adapters resume-matrix`` and surfaced in ``bernstein doctor``.

#: Adapter resume capability — adapter inherits :class:`CLIAdapter.resume`'s
#: default and the CLI falls back to a fresh session.
RESUME_FALLBACK_FRESH: str = "fallback-fresh"

#: Adapter overrides :class:`CLIAdapter.resume` to attach to the prior
#: session via a provider-side session/resume id. The adapter is
#: responsible for reinjecting any context it considers necessary.
RESUME_NATIVE: str = "native"

#: Tri-state capability rendered as ``adapter -> capability``. Adapters
#: absent from this table are assumed :data:`RESUME_FALLBACK_FRESH`.
RESUME_CAPABILITY_MATRIX: dict[str, str] = {
    # Native resume — these adapters expose a stable session id that
    # survives process restart and can be reattached.
    "claude": RESUME_NATIVE,
    "claude_routine": RESUME_NATIVE,
    "openai_agents": RESUME_NATIVE,
    # Everyone else — explicit "no native resume; fall back to fresh
    # session with scratchpad reinjection".
    "aichat": RESUME_FALLBACK_FRESH,
    "aider": RESUME_FALLBACK_FRESH,
    "amp": RESUME_FALLBACK_FRESH,
    "auggie": RESUME_FALLBACK_FRESH,
    "autohand": RESUME_FALLBACK_FRESH,
    "charm": RESUME_FALLBACK_FRESH,
    "cline": RESUME_FALLBACK_FRESH,
    "codebuff": RESUME_FALLBACK_FRESH,
    "codex": RESUME_FALLBACK_FRESH,
    "cody": RESUME_FALLBACK_FRESH,
    "composio": RESUME_FALLBACK_FRESH,
    "continue_dev": RESUME_FALLBACK_FRESH,
    "copilot": RESUME_FALLBACK_FRESH,
    "cursor": RESUME_FALLBACK_FRESH,
    "devin_terminal": RESUME_FALLBACK_FRESH,
    "droid": RESUME_FALLBACK_FRESH,
    "forge": RESUME_FALLBACK_FRESH,
    "gemini": RESUME_FALLBACK_FRESH,
    "generic": RESUME_FALLBACK_FRESH,
    "goose": RESUME_FALLBACK_FRESH,
    "gptme": RESUME_FALLBACK_FRESH,
    "hermes": RESUME_FALLBACK_FRESH,
    "junie": RESUME_FALLBACK_FRESH,
    "kilo": RESUME_FALLBACK_FRESH,
    "kimi": RESUME_FALLBACK_FRESH,
    "kiro": RESUME_FALLBACK_FRESH,
    "letta_code": RESUME_FALLBACK_FRESH,
    "mistral": RESUME_FALLBACK_FRESH,
    "mock": RESUME_FALLBACK_FRESH,
    "ollama": RESUME_FALLBACK_FRESH,
    "open_interpreter": RESUME_FALLBACK_FRESH,
    "opencode": RESUME_FALLBACK_FRESH,
    "openhands": RESUME_FALLBACK_FRESH,
    "pi": RESUME_FALLBACK_FRESH,
    "plandex": RESUME_FALLBACK_FRESH,
    "q_dev": RESUME_FALLBACK_FRESH,
    "qwen": RESUME_FALLBACK_FRESH,
    "ralphex": RESUME_FALLBACK_FRESH,
    "rovo": RESUME_FALLBACK_FRESH,
}


def resume_capability(adapter_name: str) -> str:
    """Return the declared resume capability for ``adapter_name``.

    Unknown adapters default to :data:`RESUME_FALLBACK_FRESH`.
    """
    return RESUME_CAPABILITY_MATRIX.get(adapter_name, RESUME_FALLBACK_FRESH)
