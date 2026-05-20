"""Tests for the three-layer skill merge (issue 1624)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.skills.layered import (
    LayeredSkillPaths,
    MergeSpec,
    MergeStrategy,
    Skill,
    SkillLayer,
    SkillNotFoundError,
    UnknownFieldError,
    collect_layers,
    default_layer_root,
    list_skills,
    load_skill,
    merge_layers,
    per_layer_view,
)


def _base_fragment() -> dict[str, Any]:
    return {
        "name": "writer",
        "description": "base description",
        "version": "1.0.0",
        "author": "core",
        "body": "base body",
        "trigger_keywords": ["base-kw"],
        "references": [{"name": "style", "url": "base-style"}],
        "scripts": [{"name": "lint", "cmd": "base-lint"}],
        "assets": [],
        "metadata": {"limits": {"max_tokens": 1000, "temperature": 0.2}, "tags": ["base"]},
    }


def _team_fragment() -> dict[str, Any]:
    return {
        "description": "team description",
        "trigger_keywords": ["team-kw"],
        "references": [
            {"name": "style", "url": "team-style"},
            {"name": "team-conventions", "url": "team-conv"},
        ],
        "metadata": {"limits": {"temperature": 0.5}, "tags": ["team"]},
    }


def _user_fragment() -> dict[str, Any]:
    return {
        "author": "personal",
        "trigger_keywords": ["user-kw"],
        "references": [{"name": "style", "url": "user-style"}],
        "metadata": {"limits": {"max_tokens": 2000}, "personal": True},
    }


# ---------------------------------------------------------------------------
# Pure merge tests
# ---------------------------------------------------------------------------


class TestMergeLayers:
    def test_layer_precedence_override(self) -> None:
        """USER > TEAM > BASE for OVERRIDE strategy."""
        merged = merge_layers(
            {
                SkillLayer.BASE: _base_fragment(),
                SkillLayer.TEAM: _team_fragment(),
                SkillLayer.USER: _user_fragment(),
            }
        )
        # description: team wins over base, user has no description
        assert merged["description"] == "team description"
        # author: user wins
        assert merged["author"] == "personal"
        # body: only base provides it
        assert merged["body"] == "base body"

    def test_append_strategy_preserves_order(self) -> None:
        merged = merge_layers(
            {
                SkillLayer.BASE: _base_fragment(),
                SkillLayer.TEAM: _team_fragment(),
                SkillLayer.USER: _user_fragment(),
            }
        )
        assert merged["trigger_keywords"] == ["base-kw", "team-kw", "user-kw"]

    def test_keyed_replace_by_name(self) -> None:
        """Same ``name`` key gets replaced; new entries appended."""
        merged = merge_layers(
            {
                SkillLayer.BASE: _base_fragment(),
                SkillLayer.TEAM: _team_fragment(),
                SkillLayer.USER: _user_fragment(),
            }
        )
        refs = merged["references"]
        # 'style' overridden by user (last writer), 'team-conventions' appended
        assert refs == [
            {"name": "style", "url": "user-style"},
            {"name": "team-conventions", "url": "team-conv"},
        ]

    def test_keyed_replace_by_id(self) -> None:
        merged = merge_layers(
            {
                SkillLayer.BASE: {"references": [{"id": "r1", "v": "base"}]},
                SkillLayer.USER: {"references": [{"id": "r1", "v": "user"}, {"id": "r2", "v": "new"}]},
            }
        )
        assert merged["references"] == [{"id": "r1", "v": "user"}, {"id": "r2", "v": "new"}]

    def test_keyed_replace_unkeyed_entries_appended(self) -> None:
        merged = merge_layers(
            {
                SkillLayer.BASE: {"references": [{"v": "unkeyed-base"}]},
                SkillLayer.USER: {"references": [{"v": "unkeyed-user"}, {"name": "keyed", "v": "u"}]},
            }
        )
        refs = merged["references"]
        # Keyed first (insertion order), unkeyed appended in encounter order.
        assert refs == [
            {"name": "keyed", "v": "u"},
            {"v": "unkeyed-base"},
            {"v": "unkeyed-user"},
        ]

    def test_deep_merge_metadata(self) -> None:
        merged = merge_layers(
            {
                SkillLayer.BASE: _base_fragment(),
                SkillLayer.TEAM: _team_fragment(),
                SkillLayer.USER: _user_fragment(),
            }
        )
        # limits.max_tokens: user wins (2000), team contributes nothing
        # limits.temperature: team overrides base
        # tags: user does not provide -> team's value wins (last writer is team)
        # personal: only user
        assert merged["metadata"] == {
            "limits": {"max_tokens": 2000, "temperature": 0.5},
            "tags": ["team"],
            "personal": True,
        }

    def test_missing_layer_fall_through_base_only(self) -> None:
        merged = merge_layers({SkillLayer.BASE: _base_fragment()})
        assert merged["description"] == "base description"
        assert merged["trigger_keywords"] == ["base-kw"]
        assert merged["references"] == [{"name": "style", "url": "base-style"}]

    def test_missing_layer_fall_through_user_only(self) -> None:
        merged = merge_layers({SkillLayer.USER: {"description": "user-only"}})
        assert merged["description"] == "user-only"
        # body defaults to empty string per spec.
        assert merged["body"] == ""
        # references defaults to empty list per spec.
        assert merged["references"] == []

    def test_missing_layer_fall_through_team_user(self) -> None:
        """Skipping BASE layer is supported - TEAM/USER alone still merge."""
        merged = merge_layers(
            {
                SkillLayer.TEAM: {"description": "team", "trigger_keywords": ["t"]},
                SkillLayer.USER: {"trigger_keywords": ["u"]},
            }
        )
        assert merged["description"] == "team"
        assert merged["trigger_keywords"] == ["t", "u"]

    def test_no_layers_returns_defaults(self) -> None:
        merged = merge_layers({})
        assert merged == {
            "name": "",
            "description": "",
            "version": "",
            "author": "",
            "body": "",
            "trigger_keywords": [],
            "references": [],
            "scripts": [],
            "assets": [],
            "metadata": {},
        }

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(UnknownFieldError) as exc_info:
            merge_layers({SkillLayer.USER: {"not_a_field": 1}})
        assert exc_info.value.field_name == "not_a_field"

    def test_deterministic_across_runs(self) -> None:
        """Same inputs produce byte-identical JSON output every time."""
        fragments = {
            SkillLayer.BASE: _base_fragment(),
            SkillLayer.TEAM: _team_fragment(),
            SkillLayer.USER: _user_fragment(),
        }
        first = json.dumps(merge_layers(fragments), sort_keys=True)
        second = json.dumps(merge_layers(fragments), sort_keys=True)
        third = json.dumps(merge_layers(fragments), sort_keys=True)
        assert first == second == third

    def test_per_field_override_granularity(self) -> None:
        """User can override one scalar without touching others."""
        merged = merge_layers(
            {
                SkillLayer.BASE: _base_fragment(),
                SkillLayer.USER: {"author": "me"},
            }
        )
        # author overridden, description preserved from base, body preserved.
        assert merged["author"] == "me"
        assert merged["description"] == "base description"
        assert merged["body"] == "base body"

    def test_append_type_mismatch(self) -> None:
        with pytest.raises(TypeError):
            merge_layers({SkillLayer.BASE: {"trigger_keywords": "not-a-list"}})

    def test_keyed_replace_type_mismatch(self) -> None:
        with pytest.raises(TypeError):
            merge_layers({SkillLayer.BASE: {"references": "nope"}})

    def test_keyed_replace_non_mapping_entry(self) -> None:
        with pytest.raises(TypeError):
            merge_layers({SkillLayer.BASE: {"references": ["not-a-mapping"]}})

    def test_deep_merge_type_mismatch(self) -> None:
        with pytest.raises(TypeError):
            merge_layers({SkillLayer.BASE: {"metadata": "not-a-mapping"}})

    def test_custom_merge_spec(self) -> None:
        """A custom MergeSpec can change a field's strategy."""
        spec = MergeSpec(
            strategies={
                "name": MergeStrategy.OVERRIDE,
                "description": MergeStrategy.OVERRIDE,
                "version": MergeStrategy.OVERRIDE,
                "author": MergeStrategy.OVERRIDE,
                "body": MergeStrategy.OVERRIDE,
                # Force OVERRIDE so the user wholly replaces the keyword list.
                "trigger_keywords": MergeStrategy.OVERRIDE,
                "references": MergeStrategy.KEYED_REPLACE,
                "scripts": MergeStrategy.KEYED_REPLACE,
                "assets": MergeStrategy.KEYED_REPLACE,
                "metadata": MergeStrategy.DEEP_MERGE,
            }
        )
        merged = merge_layers(
            {
                SkillLayer.BASE: {"trigger_keywords": ["base"]},
                SkillLayer.USER: {"trigger_keywords": ["user"]},
            },
            spec=spec,
        )
        assert merged["trigger_keywords"] == ["user"]


# ---------------------------------------------------------------------------
# Filesystem integration tests
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.fixture
def layered_paths(tmp_path: Path) -> LayeredSkillPaths:
    base = tmp_path / "base"
    team = tmp_path / "team"
    user = tmp_path / "user"
    base.mkdir(parents=True)
    team.mkdir(parents=True)
    user.mkdir(parents=True)
    return LayeredSkillPaths(base=base, team=team, user=user)


class TestLoadSkill:
    def test_loads_from_base_only(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.base / "writer.yaml", _base_fragment())
        skill = load_skill("writer", paths=layered_paths)
        assert isinstance(skill, Skill)
        assert skill.description == "base description"
        assert skill.layers_present == (SkillLayer.BASE,)

    def test_loads_with_all_three_layers(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.base / "writer.yaml", _base_fragment())
        _write_yaml(layered_paths.team / "writer.yaml", _team_fragment())
        _write_yaml(layered_paths.user / "writer.yaml", _user_fragment())
        skill = load_skill("writer", paths=layered_paths)
        assert skill.layers_present == (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER)
        assert skill.description == "team description"
        assert skill.author == "personal"
        assert skill.trigger_keywords == ("base-kw", "team-kw", "user-kw")

    def test_team_only_then_user_added(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.team / "writer.yaml", {"description": "team-only"})
        skill = load_skill("writer", paths=layered_paths)
        assert skill.layers_present == (SkillLayer.TEAM,)
        assert skill.description == "team-only"

        _write_yaml(layered_paths.user / "writer.yaml", {"description": "user-override"})
        skill_after = load_skill("writer", paths=layered_paths)
        assert skill_after.layers_present == (SkillLayer.TEAM, SkillLayer.USER)
        assert skill_after.description == "user-override"

    def test_raises_when_no_layer_provides_skill(self, layered_paths: LayeredSkillPaths) -> None:
        with pytest.raises(SkillNotFoundError):
            load_skill("missing", paths=layered_paths)

    def test_skill_md_form(self, layered_paths: LayeredSkillPaths) -> None:
        (layered_paths.base / "writer").mkdir()
        (layered_paths.base / "writer" / "SKILL.md").write_text(
            "---\nname: writer\ndescription: from skill md\n---\nbody-line-1\nbody-line-2\n",
            encoding="utf-8",
        )
        skill = load_skill("writer", paths=layered_paths)
        assert skill.description == "from skill md"
        assert skill.body == "body-line-1\nbody-line-2"

    def test_deterministic_serialisation(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.base / "writer.yaml", _base_fragment())
        _write_yaml(layered_paths.team / "writer.yaml", _team_fragment())
        _write_yaml(layered_paths.user / "writer.yaml", _user_fragment())
        first = json.dumps(load_skill("writer", paths=layered_paths).as_dict(), sort_keys=True)
        second = json.dumps(load_skill("writer", paths=layered_paths).as_dict(), sort_keys=True)
        assert first == second


class TestListSkills:
    def test_lists_layer_of_origin(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.base / "writer.yaml", _base_fragment())
        _write_yaml(layered_paths.team / "writer.yaml", {"description": "t"})
        _write_yaml(layered_paths.user / "writer.yaml", {"description": "u"})
        _write_yaml(layered_paths.team / "team-only.yaml", {"description": "t-only"})
        _write_yaml(layered_paths.user / "user-only.yaml", {"description": "u-only"})

        entries = list_skills(paths=layered_paths)
        names = [name for name, _ in entries]
        assert names == sorted(names)
        as_dict = {name: layers for name, layers in entries}
        assert as_dict["writer"] == (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER)
        assert as_dict["team-only"] == (SkillLayer.TEAM,)
        assert as_dict["user-only"] == (SkillLayer.USER,)

    def test_empty_dirs_return_empty_list(self, layered_paths: LayeredSkillPaths) -> None:
        assert list_skills(paths=layered_paths) == []


class TestPerLayerView:
    def test_returns_raw_fragments(self, layered_paths: LayeredSkillPaths) -> None:
        _write_yaml(layered_paths.base / "writer.yaml", {"description": "base", "author": "b"})
        _write_yaml(layered_paths.user / "writer.yaml", {"author": "u"})
        fragments = per_layer_view("writer", paths=layered_paths)
        assert fragments == {
            SkillLayer.BASE: {"description": "base", "author": "b"},
            SkillLayer.USER: {"author": "u"},
        }

    def test_collect_layers_skips_missing_dirs(self, tmp_path: Path) -> None:
        # Pointing to non-existent dirs is fine - missing-layer fall-through.
        paths = LayeredSkillPaths(
            base=tmp_path / "nope-base",
            team=tmp_path / "nope-team",
            user=tmp_path / "nope-user",
        )
        assert collect_layers("anything", paths=paths) == {}


class TestDefaultLayerRoot:
    def test_xdg_overrides_honoured(self, tmp_path: Path) -> None:
        env = {
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        }
        assert default_layer_root(SkillLayer.BASE, env=env) == tmp_path / "data" / "bernstein" / "skills" / "base"
        assert default_layer_root(SkillLayer.TEAM, env=env) == tmp_path / "config" / "bernstein" / "skills" / "team"
        assert default_layer_root(SkillLayer.USER, env=env) == tmp_path / "config" / "bernstein" / "skills" / "user"

    def test_default_paths_under_home_when_xdg_absent(self) -> None:
        env: dict[str, str] = {}
        base = default_layer_root(SkillLayer.BASE, env=env)
        team = default_layer_root(SkillLayer.TEAM, env=env)
        user = default_layer_root(SkillLayer.USER, env=env)
        # Concrete shape check: ends with bernstein/skills/<layer>
        assert base.parent.parent.name == "bernstein"
        assert team.parent.parent.name == "bernstein"
        assert user.parent.parent.name == "bernstein"
        assert base.name == "base"
        assert team.name == "team"
        assert user.name == "user"
