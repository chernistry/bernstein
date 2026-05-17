"""Bernstein web GUI — Vite + React SPA mounted on FastAPI.

Optional component installed via ``pip install bernstein[gui]``.

Public surface:
  - ``mount(app)``  — attach SPA + meta endpoint to a FastAPI app
  - ``STATIC_DIR``  — path to the built Vite assets

The Python deps are minimal (``sse-starlette`` for streaming endpoints,
which downstream tickets will use). The React build itself is committed
under ``src/bernstein/gui/static/`` so the wheel ships pre-built — no Node
required at install time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

__all__ = ["STATIC_DIR", "mount"]

STATIC_DIR = Path(__file__).parent / "static"


def mount(app: FastAPI) -> None:
    """Mount the GUI on a FastAPI app at ``/ui`` and add ``/api/v1/gui-meta``."""
    from fastapi import APIRouter
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    if not STATIC_DIR.exists() or not (STATIC_DIR / "index.html").exists():
        raise RuntimeError(
            f"GUI static assets not found at {STATIC_DIR}. Build them with: `cd web && npm install && npm run build`"
        )

    # Bidirectional parity: register on BOTH the root app AND under /api/v1/.
    # AUDIT-126's `test_every_v1_route_has_root_counterpart` asserts every
    # versioned route has a root mirror — keeping the router prefix-less and
    # mounting it twice satisfies both directions of the parity test.
    router = APIRouter(tags=["gui"])

    # NB: ``from __future__ import annotations`` (top of this file) turns every
    # return annotation into a string. FastAPI's OpenAPI builder then tries to
    # resolve ``JSONResponse`` / ``FileResponse`` as response *models* (via
    # pydantic ``TypeAdapter``), fails, and crashes ``/openapi.json`` with a
    # PydanticUserError. Declaring them as ``response_class`` instead — and
    # dropping the return annotation — keeps the endpoint's runtime behaviour
    # identical while signalling to FastAPI that the response is a Starlette
    # ``Response`` subclass that should NOT be schema-modelled.
    @router.get("/gui-meta", response_class=JSONResponse)
    def gui_meta():
        return JSONResponse(
            {
                "version": _package_version(),
                "commit": _git_sha(),
                "build_time": _build_time(),
            },
        )

    app.include_router(router)
    app.include_router(router, prefix="/api/v1")

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/ui/assets", StaticFiles(directory=assets_dir), name="gui-assets")

    @app.get("/ui", include_in_schema=False, response_class=FileResponse)
    @app.get("/ui/{full_path:path}", include_in_schema=False, response_class=FileResponse)
    def gui_index(full_path: str = ""):
        # Client-side routing: every /ui/* request returns index.html unless
        # it's an asset (handled by the StaticFiles mount above).
        # full_path is FastAPI path-capture; consumed by the SPA router, not Python.
        del full_path
        return FileResponse(STATIC_DIR / "index.html")


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("bernstein")
    except Exception:  # pragma: no cover
        return "dev"


def _git_sha() -> str:
    try:
        import subprocess

        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _build_time() -> str:
    marker = STATIC_DIR / "index.html"
    if not marker.exists():
        return ""
    import datetime as _dt

    return _dt.datetime.fromtimestamp(marker.stat().st_mtime).isoformat(timespec="seconds")
