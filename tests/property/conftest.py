"""Shared Hypothesis configuration for the property-test suite.

PR-time runs the ``smoke`` profile (50 examples, 5 s deadline) so each
file completes in under ~30 s on a GitHub-hosted runner. The nightly
``deep`` profile lifts the example budget to 1 000 and removes the
deadline so rare counter-examples still surface.

Profile selection follows ``HYPOTHESIS_PROFILE`` (the variable name
hypothesis itself reads via ``settings.load_profile``); if unset the
``smoke`` profile is used. CI workflows export this explicitly so
behaviour is unambiguous regardless of caller.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, Verbosity, settings

# ``smoke`` — PR-time. Tight budget; flake-resistant.
settings.register_profile(
    "smoke",
    max_examples=50,
    deadline=5_000,  # 5 s per example — generous for property cases that
    # touch the filesystem (WAL writer, audit log).
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
    verbosity=Verbosity.normal,
)

# ``deep`` — nightly. Thoroughness over speed.
settings.register_profile(
    "deep",
    max_examples=1_000,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.filter_too_much,
        HealthCheck.function_scoped_fixture,
    ],
    verbosity=Verbosity.verbose,
    print_blob=True,
)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "smoke"))
