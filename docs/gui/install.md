---
title: Install and launch the GUI
description: Install the Bernstein GUI extras and start the dashboard at /ui/.
tags:
  - gui
  - install
---

# Install and launch the GUI

## Install the extras

```bash
pip install 'bernstein[gui]'
```

The `gui` extra adds one runtime dep — `sse-starlette>=2.1.0` — for the streaming endpoints. The React bundle is committed under `src/bernstein/gui/static/` and ships in the wheel, so **no Node toolchain is required at install time**.

The extras list lives in `pyproject.toml` (`[project.optional-dependencies]`, key `gui`).

## Launch

```bash
bernstein gui serve
```

Defaults:

| Flag         | Default     | Notes                                                   |
|--------------|-------------|---------------------------------------------------------|
| `--host`     | `127.0.0.1` | Loopback only. Bind a routable address explicitly.      |
| `--port`     | `8000`      | Same port as the Bernstein API.                         |
| `--no-open`  | off         | By default, the launcher opens `/ui/` in your browser.  |
| `--dev`      | off         | Skips browser auto-open. Pair with `cd web && npm run dev` for HMR on `:5173`. |
| `--minimal`  | off         | Mounts only the GUI + `/api/v1/gui-meta`. Skips full API. Smoke-test only. |

The CLI is defined in `src/bernstein/gui/cli.py`. The mount logic is `src/bernstein/gui/__init__.py` (`mount(app)` attaches the SPA at `/ui/` and registers `/api/v1/gui-meta`).

URL after launch: `http://127.0.0.1:8000/ui/`.

## First-run notes

1. **Auth token.** If `BERNSTEIN_AUTH_TOKEN` is unset, the server auto-generates one for the session and logs it to stderr. Copy it into the browser:

    ```js
    localStorage.setItem("bernstein_token", "<token>")
    ```

    Then refresh `/ui/`. The SPA reads `localStorage.bernstein_token` on every API request.

2. **Disable auth (dev only).** `BERNSTEIN_AUTH_DISABLED=1 bernstein gui serve` — middleware logs a loud warning. Never expose this on a network-reachable host.

3. **Port collision.** If `:8000` is taken, pass `--port`. The launcher does not auto-fallback.

4. **Static assets missing.** `bernstein gui serve` will exit with `GUI static assets not found at …` if the wheel was built without the `static/` bundle. Rebuild with `cd web && npm install && npm run build` (writes to `../src/bernstein/gui/static/`).

5. **GUI extras missing.** `pip install 'bernstein[gui]'` is mandatory — `bernstein gui --help` works without it, but `bernstein gui serve` prints the install hint and exits non-zero.

## Verify

```bash
curl -fsS -H "Authorization: Bearer $BERNSTEIN_AUTH_TOKEN" \
  http://127.0.0.1:8000/api/v1/gui-meta
```

Returns `{"version": "...", "commit": "...", "build_time": "..."}`.
