#!/usr/bin/env python3
"""Apply reviewed SonarQube security-hotspot decisions from a manifest.

The manifest is intentionally explicit: every hotspot key must be listed with
the reviewed resolution and a short comment. The script first fetches the
current ``TO_REVIEW`` hotspot set and only mutates keys that are still present,
so stale entries from a prior scan are skipped instead of failing the run.

Env vars:
  - ``SONAR_HOST_URL`` e.g. ``https://sonar.bernstein.run``
  - ``SONAR_TOKEN`` user token with permission to review security hotspots
  - ``SONAR_PROJECT_KEY`` optional, defaults to the manifest project key or
    ``bernstein``
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import cast

import httpx

DEFAULT_PROJECT_KEY = "bernstein"
DEFAULT_PAGE_SIZE = 500
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_PAGES = 40
VALID_RESOLUTIONS = frozenset({"SAFE", "FIXED"})


class ManifestError(ValueError):
    """Raised when a hotspot review manifest is malformed."""


@dataclasses.dataclass(frozen=True)
class ReviewConfig:
    """SonarQube API connection settings."""

    host: str
    token: str
    project_key: str = DEFAULT_PROJECT_KEY


@dataclasses.dataclass(frozen=True)
class HotspotDecision:
    """One reviewed hotspot decision from the manifest."""

    key: str
    rule_key: str
    component: str
    line: int | None
    resolution: str
    comment: str


@dataclasses.dataclass(frozen=True)
class ReviewManifest:
    """Validated hotspot review manifest."""

    project_key: str
    decisions: tuple[HotspotDecision, ...]


@dataclasses.dataclass(frozen=True)
class CurrentHotspot:
    """One current SonarQube hotspot awaiting review."""

    key: str
    rule_key: str
    component: str
    line: int | None


@dataclasses.dataclass(frozen=True)
class ReviewResult:
    """Summary of one review run."""

    reviewed: int
    skipped: int
    failed: int
    dry_run: bool


def load_manifest(path: Path) -> ReviewManifest:
    """Load and validate a hotspot review manifest."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON manifest: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ManifestError("manifest must be a JSON object")
    raw = cast("dict[str, object]", loaded)
    project_key = _required_str(raw, "project_key")
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise ManifestError("manifest decisions must be a non-empty list")
    raw_decision_list = cast("list[object]", raw_decisions)

    seen: set[str] = set()
    decisions: list[HotspotDecision] = []
    for idx, raw_decision_obj in enumerate(raw_decision_list, start=1):
        if not isinstance(raw_decision_obj, dict):
            raise ManifestError(f"decision {idx} must be an object")
        raw_decision = cast("dict[str, object]", raw_decision_obj)
        decision = _parse_decision(raw_decision, idx)
        if decision.key in seen:
            raise ManifestError(f"duplicate hotspot key: {decision.key}")
        seen.add(decision.key)
        decisions.append(decision)
    return ReviewManifest(project_key=project_key, decisions=tuple(decisions))


def apply_decisions(
    config: ReviewConfig,
    manifest: ReviewManifest,
    *,
    dry_run: bool,
    client: httpx.Client | None = None,
) -> ReviewResult:
    """Apply manifest decisions for hotspots that are still awaiting review."""
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, auth=(config.token, ""))
    assert client is not None
    try:
        current = _fetch_current_hotspots(config, client)
        current_by_key = {hotspot.key: hotspot for hotspot in current}
        reviewed = 0
        skipped = 0
        failed = 0
        for decision in manifest.decisions:
            hotspot = current_by_key.get(decision.key)
            if hotspot is None:
                skipped += 1
                print(f"skip {decision.key}: not currently TO_REVIEW")
                continue
            if not _matches_manifest(decision, hotspot):
                failed += 1
                print(f"fail {decision.key}: current hotspot does not match manifest metadata", file=sys.stderr)
                continue
            if dry_run:
                reviewed += 1
                print(f"dry-run {decision.key}: would mark {decision.resolution}")
                continue
            _change_status(config, decision, client)
            reviewed += 1
            print(f"reviewed {decision.key}: marked {decision.resolution}")
        return ReviewResult(reviewed=reviewed, skipped=skipped, failed=failed, dry_run=dry_run)
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Path to a hotspot review manifest JSON file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print planned changes without mutating Sonar.",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        config = _config_from_env(manifest)
        result = apply_decisions(config, manifest, dry_run=bool(args.dry_run))
    except (ManifestError, RuntimeError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "sonar-hotspot-review: "
        f"reviewed={result.reviewed} skipped={result.skipped} failed={result.failed} dry_run={int(result.dry_run)}"
    )
    return 1 if result.failed else 0


def _parse_decision(raw: dict[str, object], idx: int) -> HotspotDecision:
    resolution = _required_str(raw, "resolution").upper()
    if resolution not in VALID_RESOLUTIONS:
        valid = ", ".join(sorted(VALID_RESOLUTIONS))
        raise ManifestError(f"decision {idx} has invalid resolution {resolution!r}; expected one of: {valid}")
    return HotspotDecision(
        key=_required_str(raw, "key"),
        rule_key=_required_str(raw, "rule_key"),
        component=_required_str(raw, "component"),
        line=_optional_int(raw, "line"),
        resolution=resolution,
        comment=_required_str(raw, "comment"),
    )


def _fetch_current_hotspots(config: ReviewConfig, client: httpx.Client) -> list[CurrentHotspot]:
    url = f"{config.host}/api/hotspots/search"
    hotspots: list[CurrentHotspot] = []
    page = 1
    while page <= MAX_PAGES:
        response = client.get(
            url,
            params={
                "projectKey": config.project_key,
                "status": "TO_REVIEW",
                "ps": str(DEFAULT_PAGE_SIZE),
                "p": str(page),
            },
        )
        response.raise_for_status()
        payload_obj: object = response.json()
        if not isinstance(payload_obj, dict):
            raise RuntimeError("hotspot search returned a non-object payload")
        payload = cast("dict[str, object]", payload_obj)
        raw_hotspots = payload.get("hotspots")
        if isinstance(raw_hotspots, list):
            raw_hotspot_list = cast("list[object]", raw_hotspots)
            for raw_hotspot_obj in raw_hotspot_list:
                if not isinstance(raw_hotspot_obj, dict):
                    continue
                raw_hotspot = cast("dict[str, object]", raw_hotspot_obj)
                hotspot = _normalise_current_hotspot(raw_hotspot)
                if hotspot is not None:
                    hotspots.append(hotspot)
        paging_obj = payload.get("paging")
        paging = cast("dict[str, object]", paging_obj) if isinstance(paging_obj, dict) else {}
        try:
            total = _coerce_required_int(paging.get("total", 0), "total")
            page_idx = _coerce_required_int(paging.get("pageIndex", page), "pageIndex")
            page_size = _coerce_required_int(paging.get("pageSize", DEFAULT_PAGE_SIZE), "pageSize")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("hotspot search returned invalid paging metadata") from exc
        if page_size <= 0 or page_idx * page_size >= total:
            break
        page += 1
    return hotspots


def _change_status(config: ReviewConfig, decision: HotspotDecision, client: httpx.Client) -> None:
    response = client.post(
        f"{config.host}/api/hotspots/change_status",
        data={
            "hotspot": decision.key,
            "status": "REVIEWED",
            "resolution": decision.resolution,
            "comment": decision.comment,
        },
    )
    response.raise_for_status()


def _normalise_current_hotspot(raw: dict[str, object]) -> CurrentHotspot | None:
    key = _optional_str(raw.get("key"))
    rule_key = _optional_str(raw.get("ruleKey") or raw.get("rule"))
    component = _optional_str(raw.get("component"))
    if key is None or rule_key is None or component is None:
        return None
    return CurrentHotspot(key=key, rule_key=rule_key, component=component, line=_coerce_optional_int(raw.get("line")))


def _matches_manifest(decision: HotspotDecision, hotspot: CurrentHotspot) -> bool:
    return (
        decision.rule_key == hotspot.rule_key
        and decision.component == hotspot.component
        and decision.line == hotspot.line
    )


def _config_from_env(manifest: ReviewManifest) -> ReviewConfig:
    host = os.environ.get("SONAR_HOST_URL", "").strip().rstrip("/")
    token = os.environ.get("SONAR_TOKEN", "").strip()
    project_key = os.environ.get("SONAR_PROJECT_KEY", "").strip() or manifest.project_key or DEFAULT_PROJECT_KEY
    if not host:
        raise RuntimeError("SONAR_HOST_URL must be set")
    if not token:
        raise RuntimeError("SONAR_TOKEN must be set")
    return ReviewConfig(host=host, token=token, project_key=project_key)


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(raw: dict[str, object], key: str) -> int | None:
    return _coerce_optional_int(raw.get(key))


def _coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _coerce_required_int(value, "line")


def _coerce_required_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ManifestError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError as exc:
            raise ManifestError(f"{field} must be an integer") from exc
    raise ManifestError(f"{field} must be an integer")


if __name__ == "__main__":
    raise SystemExit(main())
