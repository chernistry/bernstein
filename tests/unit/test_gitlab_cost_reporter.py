"""Unit tests for ``bernstein.gitlab_app.cost_reporter``."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bernstein.gitlab_app.cost_reporter import (
    COST_NOTE_MARKER,
    aggregate_mr_cost,
    build_cost_summary,
    post_mr_cost_comment,
)


class TestAggregateMrCost:
    def test_sum(self) -> None:
        assert aggregate_mr_cost([{"cost_usd": 1.0}, {"cost_usd": 2.5}]) == 3.5

    def test_missing_key_zero(self) -> None:
        assert aggregate_mr_cost([{"other": 1.0}, {"cost_usd": 0.5}]) == 0.5

    def test_empty(self) -> None:
        assert aggregate_mr_cost([]) == 0.0

    def test_string_value_coerced(self) -> None:
        assert aggregate_mr_cost([{"cost_usd": "0.25"}]) == 0.25


class TestBuildCostSummary:
    def test_contains_marker(self) -> None:
        out = build_cost_summary(0.1234, 3, "claude")
        assert COST_NOTE_MARKER in out

    def test_format(self) -> None:
        out = build_cost_summary(0.12, 4, "claude-sonnet-4-6")
        assert "Tasks completed: 4" in out
        assert "$0.1200" in out
        assert "claude-sonnet-4-6" in out

    def test_zero_cost(self) -> None:
        assert "$0.0000" in build_cost_summary(0.0, 0, "x")


class _Response:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class TestPostMrCostComment:
    def test_no_token(self) -> None:
        assert post_mr_cost_comment(1, 1, 0.0, token="") is False

    def test_no_project(self) -> None:
        assert post_mr_cost_comment("", 1, 0.0, token="t") is False

    def test_no_iid(self) -> None:
        assert post_mr_cost_comment(1, 0, 0.0, token="t") is False

    def test_creates_when_no_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap: list[dict[str, Any]] = []

        class _FakeHttpx:
            @staticmethod
            def get(url: str, headers: dict[str, str], params: dict[str, str], timeout: float) -> _Response:
                return _Response(200, [])

            @staticmethod
            def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                cap.append({"verb": "post", "url": url, "json": json})
                return _Response(201, {"id": 1})

            @staticmethod
            def put(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                cap.append({"verb": "put", "url": url, "json": json})
                return _Response(200, {"id": 1})

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)

        assert post_mr_cost_comment(42, 7, 0.5, task_count=2, model="m", token="tok") is True
        assert cap[0]["verb"] == "post"
        assert "merge_requests/7/notes" in cap[0]["url"]

    def test_updates_when_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap: list[dict[str, Any]] = []
        existing = [{"id": 555, "body": COST_NOTE_MARKER + "\nold"}]

        class _FakeHttpx:
            @staticmethod
            def get(url: str, headers: dict[str, str], params: dict[str, str], timeout: float) -> _Response:
                return _Response(200, existing)

            @staticmethod
            def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                cap.append({"verb": "post"})
                return _Response(201, {"id": 1})

            @staticmethod
            def put(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                cap.append({"verb": "put", "url": url})
                return _Response(200, {"id": 555})

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)

        assert post_mr_cost_comment(42, 7, 1.0, token="tok") is True
        assert cap[0]["verb"] == "put"
        assert "/notes/555" in cap[0]["url"]

    def test_create_500_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeHttpx:
            @staticmethod
            def get(url: str, headers: dict[str, str], params: dict[str, str], timeout: float) -> _Response:
                return _Response(200, [])

            @staticmethod
            def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                return _Response(500)

            @staticmethod
            def put(*_args: object, **_kwargs: object) -> _Response:
                return _Response(200)

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)
        assert post_mr_cost_comment(42, 7, 0.0, token="tok") is False

    def test_search_returns_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # search 401 → falls back to create
        class _FakeHttpx:
            @staticmethod
            def get(url: str, headers: dict[str, str], params: dict[str, str], timeout: float) -> _Response:
                return _Response(401)

            @staticmethod
            def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                return _Response(201)

            @staticmethod
            def put(*_args: object, **_kwargs: object) -> _Response:
                return _Response(200)

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)
        assert post_mr_cost_comment(42, 7, 0.0, token="tok") is True

    def test_get_raises_falls_back_to_create(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeHttpx:
            @staticmethod
            def get(*_args: object, **_kwargs: object) -> _Response:
                raise RuntimeError("net")

            @staticmethod
            def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float) -> _Response:
                return _Response(201)

            @staticmethod
            def put(*_args: object, **_kwargs: object) -> _Response:
                return _Response(200)

        monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx)
        assert post_mr_cost_comment(42, 7, 0.0, token="tok") is True
