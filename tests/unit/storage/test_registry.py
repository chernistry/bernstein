"""Unit tests for the artifact sink registry (oai-003)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bernstein.core.storage import registry as storage_registry
from bernstein.core.storage.sink import ArtifactSink, ArtifactStat

if TYPE_CHECKING:
    from collections.abc import Iterator


class _StubSink(ArtifactSink):
    """Minimal sink for registry roundtrip tests."""

    name: str = "stub"

    async def write(
        self,
        key: str,
        data: bytes,
        *,
        durable: bool = True,
        content_type: str | None = None,
    ) -> None:  # pragma: no cover - not invoked
        raise NotImplementedError

    async def read(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def list(self, prefix: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    async def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def exists(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def stat(self, key: str) -> ArtifactStat:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def fresh_registry() -> Iterator[None]:
    """Each test gets a pristine default registry."""
    storage_registry._reset_for_tests()
    yield
    storage_registry._reset_for_tests()


def test_default_registry_discovers_local_fs() -> None:
    names = storage_registry.list_sink_names()
    assert "local_fs" in names


def test_default_registry_advertises_cloud_sinks() -> None:
    """The names appear even when the optional SDKs are absent."""
    names = storage_registry.list_sink_names()
    # Cloud sinks must be advertised so operators know the vocabulary.
    for expected in ("s3", "gcs", "azure_blob", "r2"):
        assert expected in names


def test_get_local_fs_returns_instance() -> None:
    sink = storage_registry.get_sink("local_fs")
    assert sink.name == "local_fs"


def test_get_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError) as excinfo:
        storage_registry.get_sink("nonexistent")
    assert "Available" in str(excinfo.value)


def test_duplicate_registration_rejected() -> None:
    storage_registry.list_sink_names()  # ensure builtins loaded
    with pytest.raises(ValueError, match="Duplicate"):
        storage_registry.register_sink("local_fs", _StubSink())


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        storage_registry.register_sink("  ", _StubSink())


def test_custom_sink_registration_round_trip() -> None:
    stub = _StubSink()
    storage_registry.register_sink("stub", stub)
    assert storage_registry.get_sink("stub") is stub
    assert "stub" in storage_registry.list_sink_names()


def test_list_sinks_skips_broken_factory() -> None:
    """Missing cloud SDK factories are skipped from list_sinks."""
    # Cloud sink factories raise on instantiation when the SDK is absent;
    # list_sinks should warn and continue rather than explode.
    results = storage_registry.list_sinks()
    names = {s.name for s in results}
    # local_fs must always succeed.
    assert "local_fs" in names


def test_entry_point_failure_does_not_break_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeEntryPoint:
        name = "broken"

        def load(self) -> object:
            raise RuntimeError("boom")

    def _fake_entry_points(group: str = "") -> list[_FakeEntryPoint]:
        if group == "bernstein.storage_sinks":
            return [_FakeEntryPoint()]
        return []

    monkeypatch.setattr(storage_registry, "entry_points", _fake_entry_points)
    names = storage_registry.list_sink_names()
    assert "local_fs" in names
    assert "broken" not in names


def test_entry_point_class_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeEntryPoint:
        name = "plugin"

        def load(self) -> type[_StubSink]:
            return _StubSink

    def _fake_entry_points(group: str = "") -> list[_FakeEntryPoint]:
        if group == "bernstein.storage_sinks":
            return [_FakeEntryPoint()]
        return []

    monkeypatch.setattr(storage_registry, "entry_points", _fake_entry_points)
    assert "plugin" in storage_registry.list_sink_names()
    got = storage_registry.get_sink("plugin")
    assert isinstance(got, _StubSink)


def test_entry_point_duplicate_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry-point colliding with a built-in is dropped silently."""

    class _FakeEntryPoint:
        name = "local_fs"

        def load(self) -> type[_StubSink]:
            return _StubSink

    def _fake_entry_points(group: str = "") -> list[_FakeEntryPoint]:
        if group == "bernstein.storage_sinks":
            return [_FakeEntryPoint()]
        return []

    monkeypatch.setattr(storage_registry, "entry_points", _fake_entry_points)
    # The built-in local_fs wins
    sink = storage_registry.get_sink("local_fs")
    assert sink.name == "local_fs"


def test_unregister_removes_name() -> None:
    reg = storage_registry.default_registry()
    reg.list_names()  # ensure builtins
    reg.unregister("local_fs")
    assert "local_fs" not in reg.list_names()
