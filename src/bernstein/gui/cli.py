"""CLI commands for the Bernstein web GUI.

The GUI ships with the wheel: pre-built static SPA in ``src/bernstein/gui/static/``
plus the Python mount in ``bernstein.gui``. The ``[gui]`` extras label is kept
in pyproject for forward-compat (so the install spec stays stable), but no
runtime gate is needed today — ``sse-starlette`` arrives transitively via core
deps and ``fastapi`` / ``uvicorn`` are already required.
"""

from __future__ import annotations

import click


@click.group("gui")
def gui_group() -> None:
    """Bernstein web GUI — operator dashboard.

    ``bernstein gui serve`` boots a FastAPI server with the SPA mounted at
    ``/ui`` and the full ``/api/v1/*`` surface attached.
    """


@gui_group.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option(
    "--port",
    default=8052,
    show_default=True,
    type=int,
    help="Bind port. Defaults to 8052 (canonical Bernstein orchestrator port).",
)
@click.option("--no-open", is_flag=True, help="Do not auto-open the browser.")
@click.option("--dev", is_flag=True, help="Dev mode — expect Vite on :5173 (no browser auto-open).")
@click.option(
    "--minimal",
    is_flag=True,
    help="Mount only the GUI + /gui-meta (skip the full Bernstein API). Useful for smoke tests.",
)
def serve(host: str, port: int, no_open: bool, dev: bool, minimal: bool) -> None:
    """Start a FastAPI server with the GUI mounted at /ui.

    By default also mounts the full Bernstein API surface from
    ``bernstein.core.server.server_app.create_app``. Pass ``--minimal`` to
    skip the full API (faster boot for smoke tests).
    """
    import uvicorn
    from fastapi import FastAPI

    from bernstein.gui import mount

    if minimal:
        app = FastAPI(title="Bernstein", description="Operator GUI (minimal)")
    else:
        try:
            from bernstein.core.server.server_app import create_app
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(f"Failed to import Bernstein API factory: {exc}") from exc
        app = create_app()

    mount(app)

    url = f"http://{host}:{port}/ui/"
    click.echo(f"Bernstein GUI — {url}")
    if dev:
        click.echo("Dev mode: run `cd web && npm run dev` in a second terminal for HMR.")
    if not no_open and not dev:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # pragma: no cover
            pass

    uvicorn.run(app, host=host, port=port, log_level="info")
