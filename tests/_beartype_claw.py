"""Optional beartype import-hook activator.

Imported from the top-level conftest.py when ``BEARTYPE_USE_CLAW=enable``
is set in the environment. The hook installs a runtime type-checker on
the ``bernstein.core.security``, ``bernstein.core.cluster``, and
``bernstein.core.agents`` packages so ``@beartype``-decorated entry
points get full enforcement *and* every plain ``def f(x: int) -> str``
in those packages gets enforced too.

Why opt-in? Unconditional beartype across 300k+ LOC slows tests by
~15-25% on big suites. CI sets the env var explicitly on the
``beartype`` job; local runs default to off (no overhead).

Why not just decorate every function? Beartype's ``claws`` mode
auto-applies the decorator to every public function in a target
package without code changes — exactly what we need to catch type
errors that would otherwise only surface on the first production call.
"""

from __future__ import annotations

import os


def maybe_install_beartype_claw() -> None:
    """Install beartype's import hook iff ``BEARTYPE_USE_CLAW=enable``.

    Safe to call multiple times — ``beartype_packages`` is idempotent.
    """
    if os.environ.get("BEARTYPE_USE_CLAW", "").lower() not in {"enable", "1", "true", "yes"}:
        return
    try:
        from beartype.claw import beartype_packages
    except ImportError:  # pragma: no cover -- beartype is a dev-only dep
        return
    # Curated allow-list of *modules* where every public API is type-clean
    # under beartype's strict reduction. Start narrow and widen as
    # surfaces reach beartype-clean status. Loose `dict[str, Any]`
    # parameters that real callers pass as `dict[str, str]` etc. trip
    # beartype's variance check, so adding a whole package wholesale
    # generates more noise than signal until the surfaces are tightened.
    #
    # ``bernstein.core.persistence.lineage`` is excluded because
    # ``LineageReader.iter_records`` annotates the return as
    # ``Iterator[LineageRecord]`` with the import gated behind
    # ``TYPE_CHECKING`` — beartype's runtime claw cannot resolve the
    # forward reference and raises at decoration time. Promoting the
    # import out of ``TYPE_CHECKING`` is tracked as a follow-up.
    beartype_packages(
        ("bernstein.core.persistence.lineage_signer",),
    )
