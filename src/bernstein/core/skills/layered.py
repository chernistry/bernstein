"""Three-layer skill customisation with deterministic merge.

A skill's effective definition is the deterministic merge of up to three
optional layers stacked in this order (lowest precedence first):

1. ``base``   - the in-package SKILL.md shipped with Bernstein.
2. ``team``   - a project-shared override committed to git.
3. ``user``   - a personal override that stays out of git.

The merge is pure (no I/O once layers are loaded), order-independent for
equal-precedence siblings, and stable across runs - identical inputs
always produce identical outputs (byte-identical when serialised to JSON
with ``sort_keys=True``).

Merge rules
-----------

Per-field, the strategy is declared in :class:`MergeSpec`:

- ``OVERRIDE``      scalar / opaque value: a higher layer replaces it.
- ``DEEP_MERGE``    table (mapping): recurse, applying per-key strategy.
- ``KEYED_REPLACE`` array of mappings keyed by ``name`` / ``id`` / ``code``:
                    higher-layer entries replace lower-layer entries with
                    the same key; entries with new keys are appended in
                    the higher layer's order.
- ``APPEND``        unkeyed array: higher-layer values are appended after
                    lower-layer values, preserving order.

The strategy table is exhaustive: every field of :class:`Skill` is
declared, so a typo in :class:`MergeSpec` is a hard error rather than a
silent ``OVERRIDE`` fallback.

The module is intentionally I/O-light at its core. :func:`merge_layers`
is the pure function the tests pin; :func:`load_skill` wires it up to
the filesystem layout under
``~/.local/share/bernstein/skills/base/``,
``~/.config/bernstein/skills/team/``, and
``~/.config/bernstein/skills/user/``.
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

# Keys we recognise for identifying an array entry in a KEYED_REPLACE
# strategy. Order matters: the first key present in an entry wins, so a
# list of dicts that all carry ``name`` will be keyed by name even when
# some also carry ``id``. This keeps the merge deterministic.
_KEYED_REPLACE_KEYS: tuple[str, ...] = ("name", "id", "code")


class SkillLayer(enum.Enum):
    """Origin layer of a skill fragment.

    Layers are ordered from lowest to highest precedence by their
    integer value. ``SkillLayer.USER`` always wins on conflict.
    """

    BASE = 0
    TEAM = 1
    USER = 2

    @property
    def label(self) -> str:
        """Lowercase human label used in CLI output and on-disk paths."""
        return self.name.lower()


class MergeStrategy(enum.Enum):
    """Per-field merge strategy."""

    OVERRIDE = "override"
    DEEP_MERGE = "deep-merge"
    KEYED_REPLACE = "keyed-replace"
    APPEND = "append"


@dataclass(frozen=True)
class MergeSpec:
    """Per-field merge strategy table.

    The default spec mirrors the shape of :class:`Skill`. Callers that
    introduce a new field MUST extend the spec; :func:`merge_layers`
    raises :class:`UnknownFieldError` for any field not listed here.
    """

    strategies: dict[str, MergeStrategy] = field(
        default_factory=lambda: {
            "name": MergeStrategy.OVERRIDE,
            "description": MergeStrategy.OVERRIDE,
            "version": MergeStrategy.OVERRIDE,
            "author": MergeStrategy.OVERRIDE,
            "body": MergeStrategy.OVERRIDE,
            "trigger_keywords": MergeStrategy.APPEND,
            "references": MergeStrategy.KEYED_REPLACE,
            "scripts": MergeStrategy.KEYED_REPLACE,
            "assets": MergeStrategy.KEYED_REPLACE,
            "metadata": MergeStrategy.DEEP_MERGE,
        }
    )

    def for_field(self, name: str) -> MergeStrategy:
        """Return the strategy for ``name`` or raise."""
        try:
            return self.strategies[name]
        except KeyError as exc:
            raise UnknownFieldError(name) from exc


class UnknownFieldError(KeyError):
    """Raised when a layer carries a field not declared in :class:`MergeSpec`."""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"unknown skill field {field_name!r}: extend MergeSpec to add it")
        self.field_name = field_name


class SkillNotFoundError(FileNotFoundError):
    """Raised when no layer provides a skill with the requested name."""

    def __init__(self, name: str, searched: list[Path]) -> None:
        joined = ", ".join(str(p) for p in searched)
        super().__init__(f"skill {name!r} not found in any layer (searched: {joined})")
        self.skill_name = name
        self.searched = searched.copy()


@dataclass(frozen=True)
class Skill:
    """The effective, merged skill returned by :func:`load_skill`.

    All fields are populated even when absent from on-disk data: the
    string fields fall back to ``""``, the array fields fall back to
    ``[]``, and ``metadata`` falls back to ``{}``. This keeps the
    downstream API total - callers never need ``getattr`` with a
    default.
    """

    name: str
    description: str
    version: str
    author: str
    body: str
    trigger_keywords: tuple[str, ...]
    references: tuple[dict[str, Any], ...]
    scripts: tuple[dict[str, Any], ...]
    assets: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]
    layers_present: tuple[SkillLayer, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-stable serialisation of the skill.

        The returned dict is plain (no tuples), so callers can hand it
        to ``json.dumps(..., sort_keys=True)`` and get a deterministic
        byte sequence.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "body": self.body,
            "trigger_keywords": list(self.trigger_keywords),
            "references": [r.copy() for r in self.references],
            "scripts": [s.copy() for s in self.scripts],
            "assets": [a.copy() for a in self.assets],
            "metadata": _deep_copy(self.metadata),
            "layers_present": [layer.label for layer in self.layers_present],
        }


# ---------------------------------------------------------------------------
# Pure merge core
# ---------------------------------------------------------------------------


def merge_layers(
    fragments: dict[SkillLayer, dict[str, Any]],
    *,
    spec: MergeSpec | None = None,
) -> dict[str, Any]:
    """Merge per-layer fragments into a single dict using ``spec``.

    Args:
        fragments: Mapping of layer -> raw skill dict. Missing layers
            are simply absent from the mapping.
        spec: Per-field strategy table. Defaults to :class:`MergeSpec`'s
            built-in table.

    Returns:
        A dict with every field declared in ``spec``, populated either
        from the layers or with the documented fallback default.

    Raises:
        UnknownFieldError: if any layer carries a key not in ``spec``.
    """
    effective_spec = spec or MergeSpec()
    ordered_layers = [layer for layer in (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER) if layer in fragments]

    # Defensive: reject unknown top-level fields up front so a typo in a
    # user override does not silently no-op.
    for layer in ordered_layers:
        for key in fragments[layer]:
            _ = effective_spec.for_field(key)

    out: dict[str, Any] = {}
    for field_name, strategy in effective_spec.strategies.items():
        values = [fragments[layer][field_name] for layer in ordered_layers if field_name in fragments[layer]]
        out[field_name] = _merge_field(field_name, strategy, values)
    return out


def _merge_field(field_name: str, strategy: MergeStrategy, values: list[Any]) -> Any:
    """Apply ``strategy`` to a left-to-right list of layer values."""
    if not values:
        return _default_for(strategy)
    if strategy == MergeStrategy.OVERRIDE:
        return values[-1]
    if strategy == MergeStrategy.APPEND:
        return _merge_append(field_name, values)
    if strategy == MergeStrategy.KEYED_REPLACE:
        return _merge_keyed_replace(field_name, values)
    if strategy == MergeStrategy.DEEP_MERGE:
        return _merge_deep(field_name, values)
    # Unreachable - all enum members covered. Re-raise so a future enum
    # extension that forgets to wire the merge is caught at runtime.
    raise AssertionError(f"unhandled merge strategy {strategy!r} for {field_name!r}")


def _default_for(strategy: MergeStrategy) -> Any:
    if strategy == MergeStrategy.OVERRIDE:
        return ""
    if strategy == MergeStrategy.APPEND:
        return []
    if strategy == MergeStrategy.KEYED_REPLACE:
        return []
    if strategy == MergeStrategy.DEEP_MERGE:
        return {}
    raise AssertionError(f"unhandled merge strategy {strategy!r}")


def _merge_append(field_name: str, values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for v in values:
        if not isinstance(v, list):
            raise TypeError(f"field {field_name!r} expects a list for APPEND merge, got {type(v).__name__}")
        result.extend(cast("list[Any]", v))
    return result


def _merge_keyed_replace(field_name: str, values: list[list[dict[str, Any]] | Any]) -> list[dict[str, Any]]:
    # Preserve insertion order across layers. We use a list-of-(key, entry)
    # rather than a dict so duplicate-key handling stays explicit.
    ordered_keys: list[str] = []
    by_key: dict[str, dict[str, Any]] = {}
    # Entries without a recognised key get appended in encounter order,
    # tagged with a sentinel so two unkeyed entries from the same layer
    # do not collapse onto each other.
    unkeyed: list[dict[str, Any]] = []

    for layer_value in values:
        if not isinstance(layer_value, list):
            raise TypeError(
                f"field {field_name!r} expects a list of mappings for KEYED_REPLACE, got {type(layer_value).__name__}"
            )
        entries = cast("list[Any]", layer_value)
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError(
                    f"field {field_name!r} expects a list of mappings; saw {type(entry).__name__} inside the list"
                )
            typed_entry = cast("dict[str, Any]", entry)
            key = _entry_key(typed_entry)
            if key is None:
                unkeyed.append(typed_entry)
                continue
            if key not in by_key:
                ordered_keys.append(key)
            by_key[key] = typed_entry

    result: list[dict[str, Any]] = [by_key[k] for k in ordered_keys]
    result.extend(unkeyed)
    return result


def _entry_key(entry: dict[str, Any]) -> str | None:
    for key_field in _KEYED_REPLACE_KEYS:
        if key_field in entry:
            value = entry[key_field]
            if isinstance(value, str) and value:
                return f"{key_field}:{value}"
    return None


def _merge_deep(field_name: str, values: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for v in values:
        if not isinstance(v, dict):
            raise TypeError(f"field {field_name!r} expects a mapping for DEEP_MERGE, got {type(v).__name__}")
        typed_v = cast("dict[str, Any]", v)
        result = _deep_merge_dicts(result, typed_v)
    return result


def _deep_merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = left.copy()
    for k, v in right.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dicts(cast("dict[str, Any]", out[k]), cast("dict[str, Any]", v))
        else:
            out[k] = _deep_copy(v)
    return out


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        typed_dict = cast("dict[Any, Any]", value)
        return {k: _deep_copy(v) for k, v in typed_dict.items()}
    if isinstance(value, list):
        typed_list = cast("list[Any]", value)
        return [_deep_copy(item) for item in typed_list]
    return value


# ---------------------------------------------------------------------------
# Filesystem wiring
# ---------------------------------------------------------------------------


def default_layer_root(layer: SkillLayer, *, env: dict[str, str] | None = None) -> Path:
    """Return the canonical directory for ``layer``.

    The defaults follow the XDG layout the task spec lists:

    - BASE: ``~/.local/share/bernstein/skills/base/``
    - TEAM: ``~/.config/bernstein/skills/team/``
    - USER: ``~/.config/bernstein/skills/user/``

    ``XDG_DATA_HOME`` / ``XDG_CONFIG_HOME`` overrides are honoured when
    set so the test-suite (and operators with non-standard layouts) can
    redirect the layers cleanly.
    """
    e = env if env is not None else os.environ
    xdg_data = e.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    xdg_config = e.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    if layer == SkillLayer.BASE:
        return Path(xdg_data) / "bernstein" / "skills" / "base"
    if layer == SkillLayer.TEAM:
        return Path(xdg_config) / "bernstein" / "skills" / "team"
    if layer == SkillLayer.USER:
        return Path(xdg_config) / "bernstein" / "skills" / "user"
    raise AssertionError(f"unhandled layer {layer!r}")


@dataclass(frozen=True)
class LayeredSkillPaths:
    """Resolved on-disk directories for the three layers."""

    base: Path
    team: Path
    user: Path

    @classmethod
    def defaults(cls, *, env: dict[str, str] | None = None) -> LayeredSkillPaths:
        return cls(
            base=default_layer_root(SkillLayer.BASE, env=env),
            team=default_layer_root(SkillLayer.TEAM, env=env),
            user=default_layer_root(SkillLayer.USER, env=env),
        )

    def for_layer(self, layer: SkillLayer) -> Path:
        if layer == SkillLayer.BASE:
            return self.base
        if layer == SkillLayer.TEAM:
            return self.team
        if layer == SkillLayer.USER:
            return self.user
        raise AssertionError(f"unhandled layer {layer!r}")


def _candidate_files(directory: Path, name: str) -> list[Path]:
    """Return possible on-disk locations for a skill fragment.

    Each layer may store a skill as either:

    - ``<dir>/<name>.yaml`` / ``<name>.yml`` - flat file fragment.
    - ``<dir>/<name>/SKILL.md`` - directory-form skill (frontmatter + body).
    - ``<dir>/<name>.toml`` - flat TOML fragment (read as YAML-compatible
      after :pep:`680` style flattening; we accept it via PyYAML only
      when the contents are also valid YAML, otherwise we ignore it).

    Order is intentional: YAML wins over the TOML form so an operator
    upgrading from a TOML override can drop a YAML file beside it
    without removing the old one first.
    """
    return [
        directory / f"{name}.yaml",
        directory / f"{name}.yml",
        directory / name / "SKILL.md",
        directory / f"{name}.toml",
    ]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    parsed: object = yaml.safe_load(raw)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(parsed).__name__}")
    typed_parsed = cast("dict[Any, Any]", parsed)
    cleaned: dict[str, Any] = {}
    for key, value in typed_parsed.items():
        if not isinstance(key, str):
            raise ValueError(f"{path}: non-string key {key!r}")
        cleaned[key] = value
    return cleaned


def _load_skill_md(path: Path) -> dict[str, Any]:
    """Parse a frontmatter + body file into a dict fragment."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        # No frontmatter: treat the whole file as the body.
        return {"body": text.strip()}
    lines = text.splitlines()
    close_idx: int | None = None
    for i in range(1, len(lines)):
        stripped = lines[i].rstrip()
        if stripped == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ValueError(f"{path}: unterminated YAML frontmatter")
    front = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1 :]).strip()
    parsed: object = yaml.safe_load(front) if front.strip() else {}
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping")
    typed_parsed = cast("dict[Any, Any]", parsed)
    cleaned: dict[str, Any] = {}
    for key, value in typed_parsed.items():
        if not isinstance(key, str):
            raise ValueError(f"{path}: non-string frontmatter key {key!r}")
        cleaned[key] = value
    if body:
        cleaned["body"] = body
    return cleaned


def _load_layer_fragment(directory: Path, name: str) -> dict[str, Any] | None:
    """Try every candidate file; return the first one that parses, or ``None``."""
    for candidate in _candidate_files(directory, name):
        if not candidate.is_file():
            continue
        if candidate.name == "SKILL.md":
            return _load_skill_md(candidate)
        return _load_yaml_mapping(candidate)
    return None


def collect_layers(
    name: str,
    *,
    paths: LayeredSkillPaths | None = None,
    env: dict[str, str] | None = None,
) -> dict[SkillLayer, dict[str, Any]]:
    """Load every available fragment for ``name`` keyed by its layer.

    Layers that have no on-disk file are simply absent from the result.
    """
    resolved_paths = paths or LayeredSkillPaths.defaults(env=env)
    fragments: dict[SkillLayer, dict[str, Any]] = {}
    for layer in (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER):
        directory = resolved_paths.for_layer(layer)
        if not directory.is_dir():
            continue
        fragment = _load_layer_fragment(directory, name)
        if fragment is not None:
            fragments[layer] = fragment
    return fragments


def load_skill(
    name: str,
    *,
    paths: LayeredSkillPaths | None = None,
    env: dict[str, str] | None = None,
    spec: MergeSpec | None = None,
) -> Skill:
    """Load ``name`` from every layer and return the merged :class:`Skill`.

    Raises:
        SkillNotFoundError: when no layer provides a fragment.
    """
    resolved_paths = paths or LayeredSkillPaths.defaults(env=env)
    fragments = collect_layers(name, paths=resolved_paths, env=env)
    if not fragments:
        searched = [resolved_paths.for_layer(layer) for layer in SkillLayer]
        raise SkillNotFoundError(name, searched)
    merged = merge_layers(fragments, spec=spec)
    layers_present = tuple(layer for layer in (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER) if layer in fragments)
    # ``name`` from the merged dict wins, but if no layer set one, fall
    # back to the requested name so the Skill is self-describing.
    effective_name = merged.get("name") or name
    return Skill(
        name=effective_name,
        description=str(merged.get("description") or ""),
        version=str(merged.get("version") or ""),
        author=str(merged.get("author") or ""),
        body=str(merged.get("body") or ""),
        trigger_keywords=tuple(merged.get("trigger_keywords") or []),
        references=tuple(merged.get("references") or []),
        scripts=tuple(merged.get("scripts") or []),
        assets=tuple(merged.get("assets") or []),
        metadata=dict(merged.get("metadata") or {}),
        layers_present=layers_present,
    )


def list_skills(
    *,
    paths: LayeredSkillPaths | None = None,
    env: dict[str, str] | None = None,
) -> list[tuple[str, tuple[SkillLayer, ...]]]:
    """Return every (name, layers-of-origin) pair across the three layers.

    Names are sorted alphabetically so the CLI output is deterministic.
    """
    resolved_paths = paths or LayeredSkillPaths.defaults(env=env)
    by_name: dict[str, set[SkillLayer]] = {}
    for layer in (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER):
        directory = resolved_paths.for_layer(layer)
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            skill_name = _skill_name_from_path(child)
            if skill_name is None:
                continue
            by_name.setdefault(skill_name, set()).add(layer)
    return [
        (
            name,
            tuple(layer for layer in (SkillLayer.BASE, SkillLayer.TEAM, SkillLayer.USER) if layer in by_name[name]),
        )
        for name in sorted(by_name)
    ]


def _skill_name_from_path(path: Path) -> str | None:
    if path.is_dir() and (path / "SKILL.md").is_file():
        return path.name
    if path.is_file() and path.suffix in {".yaml", ".yml", ".toml"}:
        return path.stem
    return None


def per_layer_view(
    name: str,
    *,
    paths: LayeredSkillPaths | None = None,
    env: dict[str, str] | None = None,
) -> dict[SkillLayer, dict[str, Any]]:
    """Return raw fragments for ``name`` keyed by layer.

    Convenience wrapper around :func:`collect_layers` for the CLI's
    ``skills show`` per-layer diff output.
    """
    return collect_layers(name, paths=paths, env=env)
