"""Tests for the Sonar insights surface and ``bernstein doctor sonar``.

Covers the API client adapters, baseline persistence, nudge logic,
soft-fail behaviour when env vars are missing, and the Click wiring.
Every HTTP interaction is patched with a tiny stub transport so the
suite stays hermetic and fast.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bernstein.cli.commands.advanced_cmd import doctor as doctor_group
from bernstein.cli.commands.doctor_sonar_cmd import run_doctor_sonar
from bernstein.core.observability.sonar import (
    DEFAULT_PROJECT_KEY,
    DEFAULT_SMELL_NUDGE,
    ENV_HOST,
    ENV_PROJECT_KEY,
    ENV_TOKEN,
    HotspotFile,
    SonarConfig,
    SonarInsights,
    baseline_path,
    collect_insights,
    evaluate_nudge,
    fetch_complexity_hotspots,
    fetch_measures,
    fetch_smell_severities,
    load_baseline,
    load_config,
    save_baseline,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


_CFG = SonarConfig(host="https://sonar.example.test", token="t0k", project_key="bernstein")


def _client(handler: Any) -> httpx.Client:
    """Build an ``httpx.Client`` whose requests are served by ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler), auth=("t0k", ""))


def _measures_response(values: dict[str, str]) -> dict[str, Any]:
    return {
        "component": {
            "key": "bernstein",
            "measures": [{"metric": k, "value": v} for k, v in values.items()],
        }
    }


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_returns_none_when_env_missing() -> None:
    """No host or token -> None so the doctor can soft-fail."""
    assert load_config({}) is None
    assert load_config({ENV_HOST: "https://x"}) is None
    assert load_config({ENV_TOKEN: "abc"}) is None


def test_load_config_uses_env_defaults() -> None:
    cfg = load_config({ENV_HOST: "https://sonar.example.test/", ENV_TOKEN: "tok"})
    assert cfg is not None
    assert cfg.host == "https://sonar.example.test"
    assert cfg.project_key == DEFAULT_PROJECT_KEY


def test_load_config_respects_project_key_override() -> None:
    cfg = load_config(
        {
            ENV_HOST: "https://sonar.example.test",
            ENV_TOKEN: "tok",
            ENV_PROJECT_KEY: "my-project",
        }
    )
    assert cfg is not None
    assert cfg.project_key == "my-project"


# ---------------------------------------------------------------------------
# fetch_measures
# ---------------------------------------------------------------------------


def test_fetch_measures_returns_metric_map() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/measures/component"
        return httpx.Response(200, json=_measures_response({"coverage": "87.5", "bugs": "3"}))

    with _client(handler) as client:
        result = fetch_measures(_CFG, client=client)
    assert result == {"coverage": "87.5", "bugs": "3"}


def test_fetch_measures_returns_none_on_404() -> None:
    with _client(lambda _r: httpx.Response(404, json={"errors": []})) as client:
        assert fetch_measures(_CFG, client=client) is None


def test_fetch_measures_returns_none_on_network_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with _client(handler) as client:
        assert fetch_measures(_CFG, client=client) is None


def test_fetch_measures_returns_none_on_bad_json_shape() -> None:
    with _client(lambda _r: httpx.Response(200, json={"hello": "world"})) as client:
        assert fetch_measures(_CFG, client=client) is None


# ---------------------------------------------------------------------------
# fetch_smell_severities
# ---------------------------------------------------------------------------


def test_fetch_smell_severities_zero_fills_missing_buckets() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "facets": [
                    {
                        "property": "severities",
                        "values": [
                            {"val": "MAJOR", "count": 12},
                            {"val": "CRITICAL", "count": 2},
                        ],
                    }
                ]
            },
        )

    with _client(handler) as client:
        counts = fetch_smell_severities(_CFG, client=client)
    assert counts == {
        "BLOCKER": 0,
        "CRITICAL": 2,
        "MAJOR": 12,
        "MINOR": 0,
        "INFO": 0,
    }


def test_fetch_smell_severities_returns_zeros_on_failure() -> None:
    with _client(lambda _r: httpx.Response(500, text="server error")) as client:
        counts = fetch_smell_severities(_CFG, client=client)
    assert all(v == 0 for v in counts.values())


# ---------------------------------------------------------------------------
# fetch_complexity_hotspots
# ---------------------------------------------------------------------------


def test_fetch_complexity_hotspots_extracts_top_n_in_order() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "components": [
                    {
                        "path": "src/bernstein/orchestrator.py",
                        "measures": [{"metric": "cognitive_complexity", "value": "240"}],
                    },
                    {
                        "path": "src/bernstein/cli/main.py",
                        "measures": [{"metric": "cognitive_complexity", "value": "180"}],
                    },
                ]
            },
        )

    with _client(handler) as client:
        hotspots = fetch_complexity_hotspots(_CFG, client=client, top_n=5)
    assert hotspots == (
        HotspotFile(path="src/bernstein/orchestrator.py", cognitive_complexity=240),
        HotspotFile(path="src/bernstein/cli/main.py", cognitive_complexity=180),
    )


def test_fetch_complexity_hotspots_returns_empty_on_failure() -> None:
    with _client(lambda _r: httpx.Response(500)) as client:
        assert fetch_complexity_hotspots(_CFG, client=client) == ()


def test_fetch_complexity_hotspots_returns_empty_when_top_n_non_positive() -> None:
    with _client(lambda _r: httpx.Response(200, json={"components": []})) as client:
        assert fetch_complexity_hotspots(_CFG, client=client, top_n=0) == ()


# ---------------------------------------------------------------------------
# collect_insights
# ---------------------------------------------------------------------------


def test_collect_insights_merges_three_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/measures/component":
            return httpx.Response(
                200,
                json=_measures_response(
                    {
                        "coverage": "82.4",
                        "code_smells": "73",
                        "bugs": "1",
                        "vulnerabilities": "0",
                        "security_hotspots": "4",
                        "cognitive_complexity": "1234",
                        "ncloc": "98765",
                    }
                ),
            )
        if request.url.path == "/api/issues/search":
            return httpx.Response(
                200,
                json={
                    "facets": [
                        {
                            "property": "severities",
                            "values": [{"val": "MAJOR", "count": 50}, {"val": "MINOR", "count": 23}],
                        }
                    ]
                },
            )
        if request.url.path == "/api/measures/component_tree":
            return httpx.Response(
                200,
                json={
                    "components": [
                        {
                            "path": "src/bernstein/foo.py",
                            "measures": [{"metric": "cognitive_complexity", "value": "99"}],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _client(handler) as client:
        insights = collect_insights(_CFG, client=client, top_n_hotspots=5)
    assert insights.fetched is True
    assert insights.coverage_pct == pytest.approx(82.4)
    assert insights.code_smells_total == 73
    assert insights.bugs == 1
    assert insights.vulnerabilities == 0
    assert insights.security_hotspots == 4
    assert insights.cognitive_complexity == 1234
    assert insights.ncloc == 98765
    assert insights.smells_by_severity["MAJOR"] == 50
    assert insights.smells_by_severity["MINOR"] == 23
    assert insights.hotspots == (HotspotFile(path="src/bernstein/foo.py", cognitive_complexity=99),)


def test_collect_insights_returns_unfetched_on_measures_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        insights = collect_insights(_CFG, client=client)
    assert insights.fetched is False
    assert insights.note


# ---------------------------------------------------------------------------
# baseline persistence
# ---------------------------------------------------------------------------


def _sample_insights(**overrides: Any) -> SonarInsights:
    base = SonarInsights(
        project_key="bernstein",
        coverage_pct=80.0,
        code_smells_total=10,
        smells_by_severity={"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 5, "MINOR": 4, "INFO": 0},
        bugs=2,
        vulnerabilities=0,
        security_hotspots=1,
        cognitive_complexity=100,
        ncloc=1000,
        hotspots=(HotspotFile(path="x.py", cognitive_complexity=50),),
        fetched=True,
    )
    if not overrides:
        return base
    data: dict[str, Any] = {
        "project_key": base.project_key,
        "coverage_pct": base.coverage_pct,
        "code_smells_total": base.code_smells_total,
        "smells_by_severity": base.smells_by_severity,
        "bugs": base.bugs,
        "vulnerabilities": base.vulnerabilities,
        "security_hotspots": base.security_hotspots,
        "cognitive_complexity": base.cognitive_complexity,
        "ncloc": base.ncloc,
        "hotspots": base.hotspots,
        "fetched": base.fetched,
    }
    data.update(overrides)
    return SonarInsights(**data)


def test_save_and_load_baseline_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "baseline.json"
    insights = _sample_insights(vulnerabilities=3)
    save_baseline(insights, path=target)
    loaded = load_baseline(target)
    assert loaded["vulnerabilities"] == 3
    assert loaded["smells_by_severity"]["MAJOR"] == 5
    assert loaded["hotspots"][0]["path"] == "x.py"


def test_load_baseline_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_baseline(tmp_path / "absent.json") == {}


def test_load_baseline_returns_empty_on_malformed(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("not json ::", encoding="utf-8")
    assert load_baseline(target) == {}


def test_baseline_path_honours_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    resolved = baseline_path()
    assert resolved == tmp_path / "bernstein" / "sonar-baseline.json"


# ---------------------------------------------------------------------------
# evaluate_nudge
# ---------------------------------------------------------------------------


def test_nudge_quiet_when_unfetched() -> None:
    nudge = evaluate_nudge(SonarInsights(project_key="bernstein", fetched=False), baseline={})
    assert nudge.should_nudge is False


def test_nudge_fires_on_smell_threshold() -> None:
    insights = _sample_insights(code_smells_total=DEFAULT_SMELL_NUDGE + 1)
    nudge = evaluate_nudge(insights, baseline={})
    assert nudge.should_nudge is True
    assert any("code smells" in r for r in nudge.reasons)


def test_nudge_fires_on_new_vulnerability() -> None:
    insights = _sample_insights(vulnerabilities=2)
    nudge = evaluate_nudge(insights, baseline={"vulnerabilities": 1})
    assert nudge.should_nudge is True
    assert any("new vulnerability" in r for r in nudge.reasons)


def test_nudge_quiet_when_vulns_stable() -> None:
    insights = _sample_insights(vulnerabilities=1)
    nudge = evaluate_nudge(insights, baseline={"vulnerabilities": 1})
    assert nudge.should_nudge is False


def test_nudge_threshold_is_override_aware() -> None:
    insights = _sample_insights(code_smells_total=10)
    quiet = evaluate_nudge(insights, baseline={}, smell_threshold=20)
    loud = evaluate_nudge(insights, baseline={}, smell_threshold=5)
    assert quiet.should_nudge is False
    assert loud.should_nudge is True


# ---------------------------------------------------------------------------
# run_doctor_sonar entry point
# ---------------------------------------------------------------------------


def test_run_doctor_sonar_soft_fails_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV_HOST, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert run_doctor_sonar() == 0


def test_run_doctor_sonar_json_payload_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_HOST, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    rc = run_doctor_sonar(as_json=True)
    assert rc == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["configured"] is False
    assert "SONAR_HOST_URL" in payload["note"]


# ---------------------------------------------------------------------------
# Click wiring
# ---------------------------------------------------------------------------


def test_doctor_group_exposes_sonar_subcommand() -> None:
    assert "sonar" in doctor_group.commands


def test_cli_sonar_soft_fail_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV_HOST, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["sonar"])
    assert result.exit_code == 0
    assert "not configured" in result.output


def test_cli_sonar_json_flag_emits_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV_HOST, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(doctor_group, ["sonar", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["configured"] is False
