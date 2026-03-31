"""Unit tests for CI log parser registry."""

from __future__ import annotations

from dataclasses import dataclass

from bernstein.core.ci_fix import CIFailure, CIFailureKind
from bernstein.core.ci_log_parser import get_parser, list_parsers, register_parser


@dataclass
class _FakeParser:
    name: str

    def parse(self, raw_log: str) -> list[CIFailure]:
        return [
            CIFailure(
                kind=CIFailureKind.UNKNOWN,
                job=self.name,
                summary=f"lines={len(raw_log.splitlines())}",
            )
        ]


def test_register_and_get_parser() -> None:
    parser = _FakeParser(name="ext008_ci_one")
    register_parser(parser)

    loaded = get_parser("ext008_ci_one")

    assert loaded is parser
    assert loaded is not None
    assert loaded.parse("a\nb")[0].summary == "lines=2"


def test_get_unknown_parser_returns_none() -> None:
    assert get_parser("ext008_missing") is None


def test_list_parsers_is_sorted() -> None:
    register_parser(_FakeParser(name="ext008_ci_b"))
    register_parser(_FakeParser(name="ext008_ci_a"))

    names = list_parsers()

    assert "ext008_ci_a" in names
    assert "ext008_ci_b" in names
    assert names == sorted(names)


def test_register_overwrites_same_name() -> None:
    first = _FakeParser(name="ext008_ci_dup")
    second = _FakeParser(name="ext008_ci_dup")
    register_parser(first)
    register_parser(second)

    assert get_parser("ext008_ci_dup") is second
