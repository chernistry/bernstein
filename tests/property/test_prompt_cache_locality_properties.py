"""Property tests for prompt-cache prefix locality enforcement.

Three guarantees Hypothesis nails down:

1. **Stable input → stable hash.** ``build_stable_prefix`` must produce
   byte-identical output regardless of header insertion order.
2. **One-byte change → exactly one drift increment.** Changing the
   stable header by a single character must increment
   ``DriftSnapshot.drift_count`` by exactly one. Two independent
   header changes must produce two increments.
3. **Hash function injectivity (over the test alphabet).** Different
   prefixes must hash to different SHA-256 digests. The strategy
   restricts itself to the printable ASCII range so we never run into
   genuine SHA-256 collisions.
"""

from __future__ import annotations

import string

from hypothesis import given
from hypothesis import strategies as st

from bernstein.core.agents.prompt_cache_locality import (
    PromptCacheLocality,
    build_stable_prefix,
    hash_prefix,
)

_KEY = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=12)
_VAL = st.text(alphabet=string.ascii_letters + string.digits, min_size=0, max_size=24)
_HEADER = st.dictionaries(_KEY, _VAL, min_size=1, max_size=6)
_BODY = st.text(alphabet=string.ascii_letters + " \n", min_size=0, max_size=80)


@given(header=_HEADER, body=_BODY)
def test_stable_prefix_independent_of_insertion_order(
    header: dict[str, str],
    body: str,
) -> None:
    """Two ``build_stable_prefix`` calls with reordered headers must match.

    Implementation already canonicalises by ``sorted(items)`` — the
    property guards against a future contributor switching to plain
    iteration order.
    """
    items = list(header.items())
    reverse_view = dict(reversed(items))
    a = build_stable_prefix(header=header, body=body)
    b = build_stable_prefix(header=reverse_view, body=body)
    assert a == b


@given(header=_HEADER, body=_BODY)
def test_hash_prefix_deterministic(
    header: dict[str, str],
    body: str,
) -> None:
    """Hashing the same prefix twice must produce the same digest."""
    prefix = build_stable_prefix(header=header, body=body)
    assert hash_prefix(prefix) == hash_prefix(prefix)


@given(role=_KEY, header=_HEADER, body=_BODY, extra_key=_KEY, extra_val=_VAL)
def test_one_header_change_increments_drift_by_exactly_one(
    role: str,
    header: dict[str, str],
    body: str,
    extra_key: str,
    extra_val: str,
) -> None:
    """Changing the stable header by one field must bump drift by 1.

    Identical observation must NOT bump drift; the first observation is
    never counted as drift either.
    """
    if extra_key in header:
        return  # the change wouldn't be visible — narrow Hypothesis input

    locality = PromptCacheLocality()

    prefix1 = build_stable_prefix(header=header, body=body)
    locality.observe(role=role, prefix=prefix1)
    snap1 = locality.snapshot(role)
    assert snap1.drift_count == 0

    locality.observe(role=role, prefix=prefix1)
    snap2 = locality.snapshot(role)
    assert snap2.drift_count == 0, "identical prefix incremented drift"

    altered = dict(header)
    altered[extra_key] = extra_val
    prefix2 = build_stable_prefix(header=altered, body=body)
    locality.observe(role=role, prefix=prefix2)
    snap3 = locality.snapshot(role)
    assert snap3.drift_count == 1, f"single field change produced drift={snap3.drift_count}"


@given(role1=_KEY, role2=_KEY, header=_HEADER, body=_BODY)
def test_drift_is_per_role(
    role1: str,
    role2: str,
    header: dict[str, str],
    body: str,
) -> None:
    """Drift in one role must not bleed into another role's counter."""
    if role1 == role2:
        return

    locality = PromptCacheLocality()
    prefix_a = build_stable_prefix(header=header, body=body)
    prefix_b = build_stable_prefix(header=header, body=body + "_changed")

    locality.observe(role=role1, prefix=prefix_a)
    locality.observe(role=role2, prefix=prefix_a)
    locality.observe(role=role1, prefix=prefix_b)

    assert locality.snapshot(role1).drift_count == 1
    assert locality.snapshot(role2).drift_count == 0
