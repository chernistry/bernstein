"""Property tests for the action-cache ``fingerprint`` primitive.

``fingerprint(fn, *args, **kwargs)`` is the cache-key generator for
the orchestrator's memoised function calls. Determinism here is
load-bearing: when ``PYTHONHASHSEED`` varies across workers, two
processes computing the same fingerprint must agree on the bytes, or
every cache lookup misses and the orchestrator's cost-saving
guarantees evaporate.

Properties:

* **Determinism across calls** — two calls with the same args / kwargs
  produce the same digest.

* **Order independence for kwargs** — kwargs are sorted; supplying
  them in any order produces the same digest.

* **Set / frozenset member-order independence** — the canonicaliser
  sorts set members by repr so processes with different hash seeds
  agree. Without this, action-cache hit rate plummets to ~0% across
  workers.

* **Distinct args → distinct digests** — at least for primitive
  scalars and obviously-different containers, the digest discriminates.
  A regression that collapses everything to the same digest would
  serve stale cached values to every caller.

* **Digest length is 32 bytes** — the ``_DIGEST_BYTES = 32`` invariant
  is part of the on-disk format; a change would corrupt every existing
  ``MemoStore`` entry.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from bernstein.core.persistence.fingerprint import fingerprint

# JSON-friendly primitives. Restricting floats to non-NaN keeps the
# property well-defined (NaN != NaN under ``==``).
_SCALAR = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**40), max_value=2**40)
    | st.floats(allow_nan=False, allow_infinity=False, width=64)
    | st.text(min_size=0, max_size=16)
)


def _sample_fn(*args: Any, **kwargs: Any) -> str:
    """Stand-in for the memoised target.

    Pure function so the AST/source caching path is exercised once.
    """
    return f"args={args} kwargs={sorted(kwargs.items())}"


@given(args=st.lists(_SCALAR, max_size=5), kwargs=st.dictionaries(st.text(min_size=1, max_size=4), _SCALAR, max_size=5))
def test_fingerprint_deterministic(args: list[Any], kwargs: dict[str, Any]) -> None:
    """Two calls with the same inputs produce the same digest.

    The minimum bar for a cache key. Catches accidental insertion of
    a clock read, random seed, or other non-pure input into the digest
    computation.
    """
    a = fingerprint(_sample_fn, *args, **kwargs)
    b = fingerprint(_sample_fn, *args, **kwargs)
    assert a == b


@given(
    kwargs=st.dictionaries(
        st.text(min_size=1, max_size=4),
        _SCALAR,
        min_size=2,
        max_size=5,
    ),
)
def test_kwargs_order_does_not_change_digest(kwargs: dict[str, Any]) -> None:
    """Kwargs are sorted; presenting them in reverse order is equivalent.

    Catches regressions in the kwargs sort step. Without sorting, two
    Python versions or even two dict literal orderings would produce
    different digests for equivalent calls.
    """
    reversed_kwargs = dict(reversed(list(kwargs.items())))
    a = fingerprint(_sample_fn, **kwargs)
    b = fingerprint(_sample_fn, **reversed_kwargs)
    assert a == b


@given(args=st.lists(_SCALAR, max_size=4))
def test_digest_length(args: list[Any]) -> None:
    """Every digest is exactly 32 bytes long.

    Pins the on-disk format invariant. A change would corrupt every
    existing MemoStore entry.
    """
    fp = fingerprint(_sample_fn, *args)
    assert isinstance(fp, bytes)
    assert len(fp) == 32


@given(a=st.integers(min_value=-1000, max_value=1000), b=st.integers(min_value=-1000, max_value=1000))
def test_distinct_int_args_distinct_digests(a: int, b: int) -> None:
    """Two distinct int args yield distinct digests.

    Sanity discriminator. A regression that produced the same digest
    for all numeric inputs would silently return the same cached value
    for every memoised call.
    """
    if a == b:
        return
    fa = fingerprint(_sample_fn, a)
    fb = fingerprint(_sample_fn, b)
    assert fa != fb


@given(
    members=st.frozensets(
        st.integers(min_value=-100, max_value=100),
        min_size=1,
        max_size=8,
    ),
)
def test_set_member_order_does_not_affect_digest(members: frozenset[int]) -> None:
    """A ``frozenset`` arg yields the same digest regardless of insertion order.

    The canonicaliser sorts set members by repr to compensate for
    Python's hash randomisation. Without it, two workers spawned with
    different ``PYTHONHASHSEED`` would compute different fingerprints
    for the same set arg — destroying cache hit rate across the fleet.
    """
    listed = list(members)
    reordered = frozenset(reversed(listed))
    a = fingerprint(_sample_fn, members)
    b = fingerprint(_sample_fn, reordered)
    assert a == b


@given(
    payload=st.dictionaries(
        st.text(min_size=1, max_size=4),
        st.frozensets(st.integers(min_value=-100, max_value=100), max_size=4),
        min_size=1,
        max_size=4,
    ),
)
def test_nested_set_in_dict_is_canonical(payload: dict[str, frozenset[int]]) -> None:
    """A dict-of-sets canonicalises recursively.

    Catches regressions where the canonicaliser stops at the first
    level of a nested structure. Without recursion, nested sets keep
    their hash-randomised order and the digest drifts between workers.
    """
    a = fingerprint(_sample_fn, payload)
    # Build an equivalent payload with the inner sets reconstructed
    # from a reversed list. Functionally identical; ordering different
    # in CPython internals.
    rebuilt = {k: frozenset(reversed(list(v))) for k, v in payload.items()}
    b = fingerprint(_sample_fn, rebuilt)
    assert a == b


@given(arg=_SCALAR)
def test_pos_arg_vs_kwarg_disambiguated(arg: Any) -> None:
    """A value passed positionally differs from the same value as a kwarg.

    Documents the calling-convention contract. A regression that
    collapsed positional and keyword arguments into the same canonical
    form would let two different function calls share a cache slot.
    """
    fa = fingerprint(_sample_fn, arg)
    fb = fingerprint(_sample_fn, x=arg)
    assert fa != fb
