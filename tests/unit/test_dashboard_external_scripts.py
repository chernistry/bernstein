"""Tests for dashboard external script integrity controls."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "dashboard" / "templates" / "index.html"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "bernstein" / "dashboard" / "static"


class ExpectedScriptMeta(TypedDict):
    integrity: str


_EXPECTED_REMOTE_SCRIPTS: dict[str, ExpectedScriptMeta] = {
    "/dashboard/static/tailwind-3.4.17.min.js": {
        "integrity": "sha384-igm5BeiBt36UU4gqwWS7imYmelpTsZlQ45FZf+XBn9MuJbn4nQr7yx1yFydocC/K",
    },
    "/dashboard/static/alpinejs-3.14.8.min.js": {
        "integrity": "sha384-X9kJyAubVxnP0hcA+AMMs21U445qsnqhnUF8EBlEpP3a42Kh/JwWjlv2ZcvGfphb",
    },
}


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append({name: value or "" for name, value in attrs})


def _script_tags() -> list[dict[str, str]]:
    parser = _ScriptParser()
    parser.feed(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return parser.scripts


def _is_remote_script_src(src: str) -> bool:
    return src.strip().lower().startswith(("http://", "https://", "//"))


def test_remote_dashboard_scripts_are_pinned_and_integrity_checked() -> None:
    remote_scripts = [script for script in _script_tags() if _is_remote_script_src(script.get("src", ""))]
    dashboard_scripts = [script for script in _script_tags() if script.get("src", "").startswith("/dashboard/static/")]

    assert not remote_scripts
    assert {script["src"] for script in dashboard_scripts} == set(_EXPECTED_REMOTE_SCRIPTS)
    for script in dashboard_scripts:
        assert "latest" not in script["src"]
        expected = _EXPECTED_REMOTE_SCRIPTS[script["src"]]
        assert script["integrity"] == expected["integrity"]
        assert script.get("crossorigin", "") == expected.get("crossorigin", "")


def test_protocol_relative_script_urls_are_classified_as_remote() -> None:
    assert _is_remote_script_src("//cdn.example.test/app.js")


def test_dashboard_script_integrity_hashes_match_vendored_assets() -> None:
    for src, expected in _EXPECTED_REMOTE_SCRIPTS.items():
        asset_name = src.rsplit("/", maxsplit=1)[-1]
        digest = hashlib.sha384((_STATIC_DIR / asset_name).read_bytes()).digest()
        integrity = "sha384-" + base64.b64encode(digest).decode("ascii")

        assert integrity == expected["integrity"]
