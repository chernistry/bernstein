# Two-Phase Sandboxed Execution — Wiring Design

**Date:** 2026-03-29
**Task:** fbe700a77fa2
**Status:** Ready for implementation

## Context

OpenAI Codex pioneered the two-phase sandbox pattern:
- **Phase 1 (setup):** Container runs with network access to install dependencies
- **Phase 2 (execution):** Container runs with network fully disabled — agent cannot exfiltrate data or make unexpected API calls

The core mechanics are **already implemented** in `src/bernstein/core/container.py` and `src/bernstein/core/spawner.py`. Tests pass. What's missing is the **end-to-end wiring** that lets users actually enable the feature.

## Gap Analysis

| Layer | Status |
|-------|--------|
| `ContainerManager.run_phase1_setup()` | ✅ Implemented |
| `AgentSpawner._spawn_in_container()` two-phase logic | ✅ Implemented |
| `ContainerIsolationConfig` in `OrchestratorConfig` | ✅ Model exists |
| CLI flags to enable container/two-phase | ❌ Missing |
| Orchestrator reads env vars → builds ContainerConfig | ❌ Missing |
| `AgentSpawner` receives `container_config` | ❌ Missing |
| Tests for wiring code | ❌ Missing |

## Design

### Communication pattern

Consistent with existing `--workflow`, `--routing`, `--compliance` flags:
- CLI sets env vars: `BERNSTEIN_CONTAINER=1`, `BERNSTEIN_CONTAINER_IMAGE=...`, `BERNSTEIN_TWO_PHASE_SANDBOX=1`
- `_start_spawner()` in `server_launch.py` propagates them via `env = dict(os.environ)`
- Orchestrator subprocess reads them in `start_run_mode()`

### New helper: `_build_container_config()`

Added to `orchestrator.py` as a module-level function:

```python
def _build_container_config(iso: ContainerIsolationConfig) -> ContainerConfig | None:
    if not iso.enabled:
        return None
    # Build TwoPhaseSandboxConfig if requested
    two_phase = TwoPhaseSandboxConfig(setup_commands=iso.sandbox_setup_commands) if iso.two_phase_sandbox else None
    return ContainerConfig(
        runtime=ContainerRuntime(iso.runtime),
        image=iso.image,
        resource_limits=ResourceLimits(cpu_cores=iso.cpu_cores, memory_mb=iso.memory_mb, pids_limit=iso.pids_limit),
        security=SecurityProfile(drop_capabilities=iso.drop_capabilities, read_only_rootfs=iso.read_only_rootfs),
        network_mode=NetworkMode(iso.network_mode),
        two_phase_sandbox=two_phase,
    )
```

### Orchestrator `start_run_mode` changes

After the existing `run_config.json` block, read container settings:

```python
container_enabled = bool(int(os.environ.get("BERNSTEIN_CONTAINER", "0") or "0"))
container_image = os.environ.get("BERNSTEIN_CONTAINER_IMAGE", "bernstein-agent:latest")
two_phase_sandbox = bool(int(os.environ.get("BERNSTEIN_TWO_PHASE_SANDBOX", "0") or "0"))
```

Build config and pass to `AgentSpawner`:

```python
container_iso = ContainerIsolationConfig(
    enabled=container_enabled, image=container_image, two_phase_sandbox=two_phase_sandbox,
)
container_config = _build_container_config(container_iso)

spawner = AgentSpawner(..., container_config=container_config)
```

### CLI flags (`run_cmd.py` `conduct` command)

```
--container/--no-container        Enable container isolation (requires Docker/Podman)
--container-image TEXT            Container image (default: bernstein-agent:latest)
--two-phase-sandbox/              Phase 1: install deps with network.
  --no-two-phase-sandbox          Phase 2: run agent with network disabled.
```

## Files Changed

1. `src/bernstein/cli/run_cmd.py` — add 3 CLI options to `conduct`
2. `src/bernstein/core/orchestrator.py` — add `_build_container_config()` + wire in `start_run_mode`
3. `tests/unit/test_container_wiring.py` — new test file for wiring logic

## Test Plan

- `_build_container_config(enabled=True, two_phase_sandbox=True)` → `ContainerConfig` with `TwoPhaseSandboxConfig`
- `_build_container_config(enabled=False)` → `None`
- `_build_container_config(enabled=True, two_phase_sandbox=False)` → `ContainerConfig` without two-phase
- `_build_container_config()` fields map correctly (image, cpu_cores, network_mode)
- Invalid runtime string falls back to DOCKER
