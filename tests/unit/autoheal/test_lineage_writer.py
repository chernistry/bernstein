"""Unit tests for ``bernstein.core.autoheal.lineage_writer``."""

from __future__ import annotations

import json

from bernstein.core.autoheal.lineage_writer import (
    LINEAGE_KIND,
    PAYLOAD_SCHEMA_VERSION,
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


def test_payload_includes_schema_version() -> None:
    """Forward-compat: schema_version is part of the canonical shape."""
    out = render_payload(_mk())
    assert out["schema_version"] == PAYLOAD_SCHEMA_VERSION


def test_payload_meta_extension_is_merged_at_top_level() -> None:
    """Extension via ``meta`` surfaces new keys without a schema bump."""
    payload = _mk(meta={"decision_id": "dec-xyz", "replay_seed": 42})
    out = render_payload(payload)
    assert out["decision_id"] == "dec-xyz"
    assert out["replay_seed"] == 42
    # Core fields still authoritative.
    assert out["strategy"] == "ruff-format"


def test_payload_meta_cannot_overwrite_core_field() -> None:
    """A buggy caller must not be able to poison the canonical shape."""
    payload = _mk(meta={"strategy": "EVIL"})
    out = render_payload(payload)
    assert out["strategy"] == "ruff-format"


def test_payload_default_meta_is_independent() -> None:
    """Default-factory ``meta`` is not shared across instances."""
    a = _mk()
    b = _mk()
    a.meta["x"] = 1
    assert "x" not in b.meta
