"""Unit tests for ``bernstein.core.autoheal.lineage_writer``."""

from __future__ import annotations

import json

from bernstein.core.autoheal.lineage_writer import (
    LINEAGE_KIND,
    AutohealLineagePayload,
    render_canonical_bytes,
    render_payload,
)


def _mk(**kw: object) -> AutohealLineagePayload:
    base: dict[str, object] = dict(
        failed_run_id="run-1",
        head_sha="abc",
        classification="safe",
        strategy="ruff-format",
        patch_sha="dead",
        llm_calls=0,
        cost_usd=0.0,
        outcome="applied",
        confidence=0.9,
    )
    base.update(kw)
    return AutohealLineagePayload(**base)  # type: ignore[arg-type]


def test_lineage_kind_is_namespaced() -> None:
    assert LINEAGE_KIND == "autoheal.action"


def test_render_payload_returns_full_dict() -> None:
    out = render_payload(_mk())
    assert out["failed_run_id"] == "run-1"
    assert out["classification"] == "safe"
    assert out["strategy"] == "ruff-format"


def test_render_canonical_bytes_is_stable() -> None:
    a = render_canonical_bytes(_mk())
    b = render_canonical_bytes(_mk())
    assert a == b


def test_render_canonical_bytes_uses_sorted_keys() -> None:
    raw = render_canonical_bytes(_mk())
    parsed = json.loads(raw)
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_render_canonical_bytes_compact_separators() -> None:
    raw = render_canonical_bytes(_mk())
    s = raw.decode("utf-8")
    assert ", " not in s
    assert ": " not in s
