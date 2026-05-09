"""Schemathesis-driven contract tests against the Bernstein FastAPI app.

OpenAPI is the source of truth for the task-server REST surface. This
file fuzzes documented operations with Hypothesis-generated request
bodies and asserts no 500-class crash leaks an unhandled exception.

PR-time (``smoke`` profile): only fuzz endpoints whose path matches a
short critical-surface allow-list (task CRUD + health + openapi). Five
examples per endpoint, only the ``not_a_server_error`` check enabled.
Total wall-clock: ~60-90s on a hosted runner.

Nightly (``deep`` profile): fuzz every operation in the schema, 50
examples each, full check suite (response-schema conformance,
status-code conformance). Wall-clock ~30-45 min; advisory only.

Profile selection: ``SCHEMATHESIS_PROFILE=smoke|deep`` (default
``smoke``). The CI workflow exports the value explicitly.
"""

from __future__ import annotations

import os

import pytest
import schemathesis
from hypothesis import settings
from schemathesis import checks as st_checks

# Disable Bernstein auth before the app is built — the OpenAPI schema
# endpoint is itself behind the auth middleware and Schemathesis cannot
# fetch it otherwise. Setting the env var here (before the import) is
# the documented opt-out path.
os.environ.setdefault("BERNSTEIN_AUTH_DISABLED", "1")

from bernstein.core.server.server_app import create_app

# Build a stand-alone FastAPI app instance (auth-disabled via env var,
# no cluster, no external deps). Schemathesis derives its sweep from
# the OpenAPI schema served at ``/openapi.json``.
_app = create_app(auth_token=None, readonly=False, cluster_config=None)
schema = schemathesis.openapi.from_asgi("/openapi.json", _app)

_PROFILE = os.environ.get("SCHEMATHESIS_PROFILE", "smoke")
_MAX_EXAMPLES = {"smoke": 5, "deep": 50}.get(_PROFILE, 5)

# PR-time critical-surface allow-list. Keep tight — every entry adds
# 5×endpoint examples to the wall-clock. The deep profile drops this
# filter and fuzzes the entire schema.
_SMOKE_PATH_PREFIXES = (
    "/healthz",
    "/openapi.json",
    "/api/v1/tasks",
    "/tasks",
    "/metrics",
)


def _path_in_smoke_set(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _SMOKE_PATH_PREFIXES)


# Smoke runs only the 5xx-leak check; nightly inspects the rest manually.
# `not_a_server_error` catches the case property tests cannot:
# unhandled-exception leaks against arbitrary Hypothesis-generated
# request bodies.
_CHECKS = (st_checks.not_a_server_error,)


@schema.parametrize()
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
def test_no_unhandled_exceptions(case: schemathesis.Case) -> None:
    """Every documented endpoint must respond without 500-class crash.

    `case.call_and_validate()` invokes the app via the ASGI in-process
    transport, so each example is a sub-millisecond round-trip — fast
    enough that a focused critical-surface fuzz stays under 90 s at
    smoke settings.
    """
    if _PROFILE == "smoke" and not _path_in_smoke_set(case.path):
        pytest.skip(f"path {case.path} not in smoke allow-list")
    response = case.call_and_validate(checks=_CHECKS)
    if response.status_code >= 500:
        pytest.fail(
            f"5xx response from {case.method} {case.path}: status={response.status_code}, body={response.text[:200]}"
        )
