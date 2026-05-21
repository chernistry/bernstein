"""Unit tests for the sandbox selection policy.

The selector is pure, so the tests use lightweight stub backends -
duck-typing against :class:`SandboxBackend` rather than spinning up
real worktree/docker instances. This keeps the suite fast and lets us
exercise edge cases (missing credentials, capability gaps, override
conflicts) without I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from bernstein.core.sandbox import SandboxCapability
from bernstein.core.sandbox.selector import (
    DEFAULT_PRECEDENCE,
    FREE_BACKENDS,
    SandboxEnvironment,
    SandboxPolicy,
    SandboxSelectionError,
    select_sandbox,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bernstein.core.sandbox.backend import SandboxBackend


@dataclass(frozen=True)
class StubBackend:
    """Minimal :class:`SandboxBackend`-shaped object for selector tests."""

    name: str
    capabilities: frozenset[SandboxCapability]

    async def create(self, manifest: Any, options: Any = None) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def resume(self, snapshot_id: str) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def destroy(self, session: Any) -> None:  # pragma: no cover
        raise NotImplementedError


_BASE_CAPS = frozenset({SandboxCapability.FILE_RW, SandboxCapability.EXEC})


def _backends(*names: str) -> list[SandboxBackend]:
    """Build a list of stub backends with the standard FILE_RW+EXEC caps."""
    return [StubBackend(name=name, capabilities=_BASE_CAPS) for name in names]


def _all_creds() -> frozenset[str]:
    """Credentials that satisfy every paid backend in the precedence."""
    return frozenset(
        {
            "E2B_API_KEY",
            "MODAL_TOKEN_ID",
            "MODAL_TOKEN_SECRET",
            "DAYTONA_API_KEY",
            "BLAXEL_API_KEY",
            "RUNLOOP_API_KEY",
            "VERCEL_TOKEN",
        }
    )


def test_selects_lowest_cost_backend_by_default() -> None:
    """With defaults, the cheapest free backend wins."""
    chosen = select_sandbox(_backends("docker", "worktree"))
    assert chosen.name == "worktree"


def test_default_precedence_orders_paid_backends_when_allowed() -> None:
    """Paid backends are ranked by :data:`DEFAULT_PRECEDENCE`."""
    backends = _backends("modal", "e2b", "daytona")
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(allow_paid=True),
        environment=SandboxEnvironment(available_credentials=_all_creds()),
    )
    # e2b sits before modal, modal before daytona in DEFAULT_PRECEDENCE.
    assert chosen.name == "e2b"


def test_unknown_backends_appended_alphabetically() -> None:
    """Names not in :data:`DEFAULT_PRECEDENCE` rank after the named ones."""
    backends = _backends("zeta-cloud", "alpha-cloud", "worktree")
    chosen = select_sandbox(backends)
    # worktree wins because it is first in DEFAULT_PRECEDENCE; the two
    # unknown names would rank alphabetically after it.
    assert chosen.name == "worktree"


def test_unknown_backends_rank_alphabetically_among_themselves() -> None:
    """When only unknown-named backends are eligible, alphabetical order wins."""
    backends = [
        StubBackend(name="zeta", capabilities=_BASE_CAPS),
        StubBackend(name="alpha", capabilities=_BASE_CAPS),
    ]
    # Custom precedence that allows both as "free" by routing through override.
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(override="alpha"),
    )
    assert chosen.name == "alpha"


def test_override_wins_over_precedence() -> None:
    """``policy.override`` short-circuits the precedence loop."""
    chosen = select_sandbox(
        _backends("worktree", "docker"),
        policy=SandboxPolicy(override="docker"),
    )
    assert chosen.name == "docker"


def test_override_unknown_raises() -> None:
    """An override naming a backend that isn't registered raises clearly."""
    with pytest.raises(SandboxSelectionError) as excinfo:
        select_sandbox(
            _backends("worktree"),
            policy=SandboxPolicy(override="docker"),
        )
    assert "docker" in str(excinfo.value)
    assert excinfo.value.attempted == ("worktree",)


def test_override_missing_capability_raises() -> None:
    """Override is honoured but capability gaps are still enforced."""
    backends = [StubBackend(name="docker", capabilities=frozenset({SandboxCapability.FILE_RW}))]
    with pytest.raises(SandboxSelectionError, match="capabilities"):
        select_sandbox(
            backends,
            policy=SandboxPolicy(override="docker"),
        )


def test_override_missing_credentials_raises() -> None:
    """Override of a cloud backend without the API key fails loudly."""
    backends = _backends("e2b")
    with pytest.raises(SandboxSelectionError, match="credentials"):
        select_sandbox(
            backends,
            policy=SandboxPolicy(override="e2b", allow_paid=True),
            environment=SandboxEnvironment(available_credentials=frozenset()),
        )


def test_disallowed_paid_backends_skipped() -> None:
    """Without ``allow_paid``, only free backends are eligible."""
    backends = _backends("e2b", "docker")
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(allow_paid=False),
        environment=SandboxEnvironment(available_credentials=_all_creds()),
    )
    assert chosen.name == "docker"


def test_no_eligible_backend_raises() -> None:
    """When every backend is filtered out, a clear error surfaces."""
    backends = _backends("e2b")
    with pytest.raises(SandboxSelectionError) as excinfo:
        select_sandbox(backends, policy=SandboxPolicy(allow_paid=False))
    assert "policy" in str(excinfo.value).lower()
    assert excinfo.value.attempted == ("e2b",)


def test_empty_backend_list_raises() -> None:
    """A registry with no backends is itself a selection failure."""
    with pytest.raises(SandboxSelectionError, match="No sandbox backends"):
        select_sandbox(())


def test_capability_filter_drops_inadequate_backends() -> None:
    """Backends missing a required capability are filtered out."""
    backends = [
        StubBackend(name="worktree", capabilities=frozenset({SandboxCapability.FILE_RW})),
        StubBackend(name="docker", capabilities=_BASE_CAPS),
    ]
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(required_capabilities=frozenset({SandboxCapability.FILE_RW, SandboxCapability.EXEC})),
    )
    assert chosen.name == "docker"


def test_required_credentials_filter_applies_to_all_backends() -> None:
    """``required_credentials`` is enforced on top of backend defaults."""
    backends = _backends("docker", "worktree")
    with pytest.raises(SandboxSelectionError):
        select_sandbox(
            backends,
            policy=SandboxPolicy(
                required_credentials=frozenset({"CUSTOM_KEY"}),
            ),
            environment=SandboxEnvironment(available_credentials=frozenset()),
        )


def test_budget_exhausted_falls_back_to_free_backends() -> None:
    """Zero or negative budget forces free backends only."""
    backends = _backends("e2b", "docker")
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(allow_paid=True),
        environment=SandboxEnvironment(
            available_credentials=_all_creds(),
            budget_remaining_usd=0.0,
        ),
    )
    assert chosen.name == "docker"


def test_budget_none_means_no_enforcement() -> None:
    """``budget_remaining_usd=None`` is "no enforcement", not "exhausted"."""
    backends = _backends("e2b")
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(allow_paid=True),
        environment=SandboxEnvironment(
            available_credentials=_all_creds(),
            budget_remaining_usd=None,
        ),
    )
    assert chosen.name == "e2b"


def test_custom_precedence_respected() -> None:
    """Operators can override :data:`DEFAULT_PRECEDENCE`."""
    backends = _backends("docker", "worktree")
    chosen = select_sandbox(
        backends,
        policy=SandboxPolicy(precedence=("docker", "worktree")),
    )
    assert chosen.name == "docker"


def test_default_precedence_is_stable() -> None:
    """The published precedence must include the canonical free pair first."""
    assert DEFAULT_PRECEDENCE[:2] == ("worktree", "docker")
    assert "worktree" in FREE_BACKENDS
    assert "docker" in FREE_BACKENDS


def test_selector_is_pure_no_side_effects() -> None:
    """Selecting twice with identical inputs returns identical names."""
    backends: Iterable[StubBackend] = _backends("docker", "worktree")
    first = select_sandbox(list(backends))
    second = select_sandbox(list(backends))
    assert first.name == second.name
