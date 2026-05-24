"""Unit tests for ``scripts/review_sonar_hotspots.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "review_sonar_hotspots.py"
HOST = "https://sonar.test"
PROJECT = "bernstein"


@pytest.fixture
def reviewer() -> ModuleType:
    """Load scripts/review_sonar_hotspots.py as a module."""
    spec = importlib.util.spec_from_file_location("review_sonar_hotspots_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(*decisions: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": PROJECT,
        "decisions": list(decisions),
    }


def _decision(
    key: str,
    *,
    rule_key: str = "python:S5332",
    component: str = "bernstein:src/bernstein/core/observability/apm_integration.py",
    line: int = 282,
    resolution: str = "SAFE",
    comment: str = "Reviewed for #1785: local transport endpoint.",
) -> dict[str, Any]:
    return {
        "key": key,
        "rule_key": rule_key,
        "component": component,
        "line": line,
        "resolution": resolution,
        "comment": comment,
    }


def test_load_manifest_rejects_duplicate_hotspot_keys(tmp_path: Path, reviewer: ModuleType) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(_decision("HS-1"), _decision("HS-1"))), encoding="utf-8")

    with pytest.raises(reviewer.ManifestError, match="duplicate hotspot key"):
        reviewer.load_manifest(manifest_path)


@respx.mock
def test_apply_decisions_posts_safe_review_for_current_hotspot(tmp_path: Path, reviewer: ModuleType) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(_decision("HS-1"))), encoding="utf-8")
    manifest = reviewer.load_manifest(manifest_path)
    config = reviewer.ReviewConfig(host=HOST, token="token", project_key=PROJECT)
    respx.get(f"{HOST}/api/hotspots/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "hotspots": [
                    {
                        "key": "HS-1",
                        "ruleKey": "python:S5332",
                        "component": "bernstein:src/bernstein/core/observability/apm_integration.py",
                        "line": 282,
                        "status": "TO_REVIEW",
                    }
                ],
                "paging": {"pageIndex": 1, "pageSize": 500, "total": 1},
            },
        )
    )
    change = respx.post(f"{HOST}/api/hotspots/change_status").mock(return_value=httpx.Response(204))

    result = reviewer.apply_decisions(config, manifest, dry_run=False)

    assert result.reviewed == 1
    assert result.skipped == 0
    assert change.called
    request = change.calls.last.request
    body = dict(httpx.QueryParams(request.content.decode()))
    assert body == {
        "comment": "Reviewed for #1785: local transport endpoint.",
        "hotspot": "HS-1",
        "resolution": "SAFE",
        "status": "REVIEWED",
    }


@respx.mock
def test_apply_decisions_skips_hotspots_missing_from_current_review_set(tmp_path: Path, reviewer: ModuleType) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(_decision("HS-1"))), encoding="utf-8")
    manifest = reviewer.load_manifest(manifest_path)
    config = reviewer.ReviewConfig(host=HOST, token="token", project_key=PROJECT)
    respx.get(f"{HOST}/api/hotspots/search").mock(
        return_value=httpx.Response(200, json={"hotspots": [], "paging": {"pageIndex": 1, "pageSize": 500, "total": 0}})
    )
    change = respx.post(f"{HOST}/api/hotspots/change_status").mock(return_value=httpx.Response(204))

    result = reviewer.apply_decisions(config, manifest, dry_run=False)

    assert result.reviewed == 0
    assert result.skipped == 1
    assert not change.called
