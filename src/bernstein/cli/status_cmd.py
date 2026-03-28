"""Status and diagnostic commands: status, ps, doctor."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click

from bernstein.cli.helpers import (
    STATUS_COLORS,
    console,
    is_process_alive,
    print_banner,
    server_get,
)

# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@click.command("score", hidden=True)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON.")
def status(as_json: bool) -> None:
    """Task summary, active agents, cost estimate.

    \b
      bernstein status          # Rich table output
      bernstein status --json   # machine-readable JSON
    """
    data = server_get("/status")
    if data is None:
        if as_json:
            click.echo(json.dumps({"error": "Cannot reach task server"}))
        else:
            console.print(
                "[red]Cannot reach task server.[/red] Is Bernstein running? Run [bold]bernstein[/bold] to start."
            )
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    print_banner()

    # ---- Task table ----
    from rich.table import Table

    tasks: list[dict[str, Any]] = data.get("tasks", [])
    task_table = Table(title="Tasks", show_lines=False, header_style="bold cyan")
    task_table.add_column("ID", style="dim", min_width=10)
    task_table.add_column("Title", min_width=30)
    task_table.add_column("Role", min_width=10)
    task_table.add_column("Status", min_width=14)
    task_table.add_column("Priority", justify="right")
    task_table.add_column("Agent", min_width=12)

    for t in tasks:
        raw_status = t.get("status", "open")
        color = STATUS_COLORS.get(raw_status, "white")
        task_table.add_row(
            t.get("id", "—"),
            t.get("title", "—"),
            t.get("role", "—"),
            f"[{color}]{raw_status}[/{color}]",
            str(t.get("priority", 2)),
            t.get("assigned_agent") or "[dim]—[/dim]",
        )

    console.print(task_table)

    # ---- Agent table ----
    agents: list[dict[str, Any]] = data.get("agents", [])
    if agents:
        agent_table = Table(title="Active Agents", show_lines=False, header_style="bold cyan")
        agent_table.add_column("ID", style="dim", min_width=12)
        agent_table.add_column("Role", min_width=10)
        agent_table.add_column("Status", min_width=10)
        agent_table.add_column("Model", min_width=10)
        agent_table.add_column("Tasks")

        for a in agents:
            raw_astatus = a.get("status", "idle")
            acolor = "yellow" if raw_astatus == "working" else "dim"
            agent_table.add_row(
                a.get("id", "—"),
                a.get("role", "—"),
                f"[{acolor}]{raw_astatus}[/{acolor}]",
                a.get("model", "—"),
                str(len(a.get("task_ids", []))),
            )
        console.print(agent_table)
    else:
        console.print("[dim]No active agents.[/dim]")

    # ---- Summary stats ----
    summary: dict[str, Any] = data.get("summary", {})
    total = summary.get("total", len(tasks))
    done = summary.get("done", sum(1 for t in tasks if t.get("status") == "done"))
    in_prog = summary.get("in_progress", sum(1 for t in tasks if t.get("status") == "in_progress"))
    failed = summary.get("failed", sum(1 for t in tasks if t.get("status") == "failed"))

    console.print(
        f"\n[bold]Tasks:[/bold] {total} total  "
        f"[green]{done} done[/green]  "
        f"[yellow]{in_prog} in progress[/yellow]  "
        f"[red]{failed} failed[/red]"
    )

    elapsed_s: int | None = data.get("elapsed_seconds")
    if elapsed_s is not None:
        minutes, secs = divmod(elapsed_s, 60)
        console.print(f"[bold]Elapsed:[/bold] {minutes}m {secs}s")

    # ---- Cost section ----
    total_cost_usd: float = data.get("total_cost_usd", 0.0)
    per_role: list[dict[str, Any]] = data.get("per_role", [])
    roles_with_cost = [r for r in per_role if r.get("cost_usd", 0.0) > 0.0]
    if total_cost_usd > 0.0 or roles_with_cost:
        console.print(f"\n[bold]Total spend:[/bold] [green]${total_cost_usd:.4f}[/green]")
        if roles_with_cost:
            cost_table = Table(title="Cost by Role", show_lines=False, header_style="bold cyan")
            cost_table.add_column("Role", min_width=12)
            cost_table.add_column("Tasks", justify="right")
            cost_table.add_column("Cost", justify="right")
            for r in sorted(roles_with_cost, key=lambda x: x.get("cost_usd", 0.0), reverse=True):
                role_tasks = r.get("done", 0) + r.get("failed", 0) + r.get("claimed", 0) + r.get("open", 0)
                cost_table.add_row(
                    r.get("role", "—"),
                    str(role_tasks),
                    f"${r.get('cost_usd', 0.0):.4f}",
                )
            console.print(cost_table)

    # ---- Cluster section (only shown when nodes are registered) ----
    cluster = server_get("/cluster/status")
    if cluster and cluster.get("total_nodes", 0) > 0:
        node_table = Table(title="Cluster Nodes", show_lines=False, header_style="bold cyan")
        node_table.add_column("ID", style="dim", min_width=12)
        node_table.add_column("Name", min_width=12)
        node_table.add_column("Status", min_width=10)
        node_table.add_column("Slots", justify="right")
        node_table.add_column("Active", justify="right")
        node_table.add_column("URL", min_width=20)

        for n in cluster.get("nodes", []):
            raw_nstatus = n.get("status", "offline")
            ncolor = "green" if raw_nstatus == "online" else ("yellow" if raw_nstatus == "degraded" else "red")
            cap = n.get("capacity", {})
            node_table.add_row(
                n.get("id", "—")[:12],
                n.get("name", "—"),
                f"[{ncolor}]{raw_nstatus}[/{ncolor}]",
                str(cap.get("available_slots", "—")),
                str(cap.get("active_agents", "—")),
                n.get("url", "—") or "[dim]—[/dim]",
            )

        console.print(node_table)
        console.print(
            f"[bold]Cluster:[/bold] {cluster.get('topology', '?')}  "
            f"[green]{cluster.get('online_nodes', 0)} online[/green]  "
            f"{cluster.get('offline_nodes', 0)} offline  "
            f"[bold]{cluster.get('available_slots', 0)} slots available[/bold]"
        )


# ---------------------------------------------------------------------------
# ps — process visibility
# ---------------------------------------------------------------------------


@click.command("ps")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON instead of table.")
@click.option("--pid-dir", default=".sdd/runtime/pids", help="PID metadata directory.")
def ps_cmd(as_json: bool, pid_dir: str) -> None:
    """Show running Bernstein agent processes."""
    from rich.table import Table

    pid_path = Path(pid_dir)
    if not pid_path.exists():
        if as_json:
            console.print("[]")
        else:
            console.print("[dim]No agent processes found.[/dim]")
        return

    agents: list[dict[str, Any]] = []
    stale_files: list[Path] = []

    for pid_file in sorted(pid_path.glob("*.json")):
        try:
            info = json.loads(pid_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        worker_pid = info.get("worker_pid", 0)
        child_pid = info.get("child_pid")
        alive = is_process_alive(worker_pid) if worker_pid else False

        if not alive:
            stale_files.append(pid_file)
            continue

        started_at = info.get("started_at", 0)
        runtime_s = time.time() - started_at if started_at else 0
        minutes, secs = divmod(int(runtime_s), 60)
        hours, minutes = divmod(minutes, 60)
        runtime_str = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"

        agents.append(
            {
                "session": info.get("session", "?"),
                "role": info.get("role", "?"),
                "command": info.get("command", "?"),
                "model": info.get("model", "?"),
                "worker_pid": worker_pid,
                "child_pid": child_pid,
                "runtime": runtime_str,
                "started_at": started_at,
            }
        )

    # Clean up stale PID files
    for f in stale_files:
        f.unlink(missing_ok=True)

    if as_json:
        console.print(json.dumps(agents, indent=2))
        return

    if not agents:
        console.print("[dim]No running agents.[/dim]")
        return

    table = Table(title="Bernstein Agents", show_lines=False, header_style="bold cyan")
    table.add_column("Session", style="dim", min_width=18)
    table.add_column("Role", min_width=10)
    table.add_column("CLI", min_width=8)
    table.add_column("Model", min_width=16)
    table.add_column("Worker PID", justify="right")
    table.add_column("Agent PID", justify="right")
    table.add_column("Runtime", justify="right")

    for a in agents:
        table.add_row(
            a["session"],
            f"[bold]{a['role']}[/bold]",
            a["command"],
            a["model"],
            str(a["worker_pid"]),
            str(a["child_pid"] or "—"),
            a["runtime"],
        )

    console.print(table)
    console.print(f"\n[dim]{len(agents)} agent(s) running[/dim]")


# ---------------------------------------------------------------------------
# doctor — self-diagnostic
# ---------------------------------------------------------------------------


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON.")
def doctor(as_json: bool) -> None:
    """Run self-diagnostics: check Python, adapters, API keys, port, and workspace.

    \b
      bernstein doctor          # print diagnostic report
      bernstein doctor --json   # machine-readable output
    """
    import shutil
    import socket

    checks: list[dict[str, Any]] = []

    def _check(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    # 1. Python version
    major, minor = sys.version_info.major, sys.version_info.minor
    py_ok = (major, minor) >= (3, 12)
    _check(
        "Python version",
        py_ok,
        f"Python {major}.{minor} (need 3.12+)",
        "Install Python 3.12 or newer" if not py_ok else "",
    )

    # 2. CLI adapters
    adapters = {
        "claude": "ANTHROPIC_API_KEY",
        "codex": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }
    any_adapter = False
    for adapter_name, _env_var in adapters.items():
        found = shutil.which(adapter_name) is not None
        if found:
            any_adapter = True
        _check(
            f"Adapter: {adapter_name}",
            found,
            "found in PATH" if found else "not in PATH",
            f"Install {adapter_name} CLI — see docs" if not found else "",
        )

    # 3. API keys (Claude Code supports OAuth — API key optional)
    key_vars = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]
    any_key = False
    for var in key_vars:
        set_val = bool(os.environ.get(var))
        if set_val:
            any_key = True
        hint = ""
        status_str = "set" if set_val else "not set"
        if var == "ANTHROPIC_API_KEY" and not set_val:
            # Check for OAuth session
            from bernstein.core.bootstrap import _claude_has_oauth_session  # type: ignore[reportPrivateUsage]

            if _claude_has_oauth_session():
                status_str = "not set (OAuth active — OK)"
                any_key = True
                set_val = True
            else:
                hint = "export ANTHROPIC_API_KEY=key or: claude login"
        elif not set_val:
            hint = f"export {var}=your-key"
        _check(f"Env: {var}", set_val, status_str, hint)

    # 4. Port 8052 availability
    port = 8052
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", port))
            port_in_use = result == 0
    except Exception:
        port_in_use = False
    _check(
        f"Port {port}",
        not port_in_use,
        "in use — server may already be running" if port_in_use else "available",
        "Run 'bernstein stop' to free the port" if port_in_use else "",
    )

    # 5. .sdd/ structure
    workdir = Path.cwd()
    required_dirs = [".sdd", ".sdd/backlog", ".sdd/runtime"]
    sdd_ok = all((workdir / d).exists() for d in required_dirs)
    _check(
        ".sdd workspace",
        sdd_ok,
        "present" if sdd_ok else "missing or incomplete",
        "Run 'bernstein' or 'bernstein -g \"goal\"' to initialise" if not sdd_ok else "",
    )

    # 6. Stale PID files
    stale_pids: list[str] = []
    for pid_name in ("server.pid", "spawner.pid", "watchdog.pid"):
        pid_path = workdir / ".sdd" / "runtime" / pid_name
        if pid_path.exists():
            try:
                pid_val = int(pid_path.read_text().strip())
                try:
                    os.kill(pid_val, 0)
                except OSError:
                    stale_pids.append(pid_name)
            except ValueError:
                stale_pids.append(pid_name)
    _check(
        "Stale PID files",
        len(stale_pids) == 0,
        f"found: {', '.join(stale_pids)}" if stale_pids else "none",
        "Run 'bernstein stop' to clean up" if stale_pids else "",
    )

    # 7. Guardrail stats
    from bernstein.core.guardrails import get_guardrail_stats

    guardrail_stats = get_guardrail_stats(workdir)
    g_total = guardrail_stats["total"]
    g_blocked = guardrail_stats["blocked"]
    g_flagged = guardrail_stats["flagged"]
    if g_total > 0:
        g_detail = f"{g_total} checked, {g_blocked} blocked, {g_flagged} flagged"
    else:
        g_detail = "no events recorded yet"
    _check("Guardrails", True, g_detail)

    # 8. CI tool dependencies (ruff, pytest, pyright)
    from bernstein.core.ci_fix import check_test_dependencies

    ci_dep_results = check_test_dependencies()
    for dep in ci_dep_results:
        _check(
            f"CI tool: {dep['name']}",
            dep["ok"] == "True",
            dep["detail"],
            dep["fix"],
        )

    # 8. Storage backend connectivity
    storage_backend = os.environ.get("BERNSTEIN_STORAGE_BACKEND", "memory")
    if storage_backend == "memory":
        _check("Storage backend", True, "memory (default, no external dependencies)", "")
    elif storage_backend == "postgres":
        db_url = os.environ.get("BERNSTEIN_DATABASE_URL")
        if db_url:
            try:
                import asyncpg  # type: ignore[import-untyped]

                async def _check_pg() -> bool:
                    conn = await asyncpg.connect(db_url)  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]
                    await conn.close()  # type: ignore[reportUnknownMemberType]
                    return True

                import asyncio

                asyncio.run(_check_pg())
                _check("Storage backend", True, f"postgres — connected ({db_url[:40]}...)", "")
            except ImportError:
                _check(
                    "Storage backend",
                    False,
                    "postgres — asyncpg not installed",
                    "pip install bernstein[postgres]",
                )
            except Exception as exc:
                _check(
                    "Storage backend",
                    False,
                    f"postgres — connection failed: {exc}",
                    "Check BERNSTEIN_DATABASE_URL and ensure PostgreSQL is running",
                )
        else:
            _check(
                "Storage backend",
                False,
                "postgres — BERNSTEIN_DATABASE_URL not set",
                "export BERNSTEIN_DATABASE_URL=postgresql://user:pass@localhost/bernstein",
            )
    elif storage_backend == "redis":
        db_url = os.environ.get("BERNSTEIN_DATABASE_URL")
        redis_url = os.environ.get("BERNSTEIN_REDIS_URL")
        storage_ok = True
        if not db_url:
            _check(
                "Storage backend (postgres)",
                False,
                "redis mode — BERNSTEIN_DATABASE_URL not set",
                "export BERNSTEIN_DATABASE_URL=postgresql://user:pass@localhost/bernstein",
            )
            storage_ok = False
        if not redis_url:
            _check(
                "Storage backend (redis)",
                False,
                "redis mode — BERNSTEIN_REDIS_URL not set",
                "export BERNSTEIN_REDIS_URL=redis://localhost:6379",
            )
            storage_ok = False
        if storage_ok:
            _check("Storage backend", True, "redis mode (pg + redis locking)", "")
    else:
        _check(
            "Storage backend",
            False,
            f"unknown backend: {storage_backend}",
            "Set BERNSTEIN_STORAGE_BACKEND to memory, postgres, or redis",
        )

    # 10. Overall readiness
    any_adapter_key = any_adapter and any_key
    _check(
        "Ready to run",
        py_ok and any_adapter_key,
        "yes" if (py_ok and any_adapter_key) else "missing adapter or API key",
        "Install an adapter (claude/codex/gemini) and set its API key" if not any_adapter_key else "",
    )

    if as_json:
        import json as _json

        click.echo(_json.dumps({"checks": checks}, indent=2))
        failed = [c for c in checks if not c["ok"]]
        if failed:
            raise SystemExit(1)
        return

    from rich.table import Table

    table = Table(title="Bernstein Doctor", header_style="bold cyan", show_lines=False)
    table.add_column("Check", min_width=22)
    table.add_column("Status", min_width=8)
    table.add_column("Detail", min_width=35)
    table.add_column("Fix")

    for c in checks:
        icon = "[green]✓[/green]" if c["ok"] else "[red]✗[/red]"
        table.add_row(
            c["name"],
            icon,
            c["detail"],
            f"[dim]{c['fix']}[/dim]" if c["fix"] else "",
        )

    console.print(table)

    failed_checks = [c for c in checks if not c["ok"]]
    if failed_checks:
        console.print(f"\n[red]{len(failed_checks)} issue(s) found.[/red]")
        raise SystemExit(1)
    else:
        console.print("\n[green]All checks passed.[/green]")
