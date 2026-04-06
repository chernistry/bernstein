"""CFG-008: Config templates for common use cases.

Provides pre-built bernstein.yaml templates for web-app, microservices,
and monorepo projects.  Templates can be listed, previewed, and applied
to initialize a new project configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConfigTemplate:
    """A named config template for a common use case.

    Attributes:
        name: Short identifier (e.g. "web-app", "monorepo").
        description: Human-readable description of the template.
        config: The template config dict ready for YAML serialization.
        tags: Searchable tags for discovery.
    """

    name: str
    description: str
    config: dict[str, Any]
    tags: tuple[str, ...] = ()

    def to_yaml(self) -> str:
        """Render the template config as YAML.

        Returns:
            YAML string representation of the config.
        """
        return yaml.dump(self.config, default_flow_style=False, sort_keys=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the template metadata to a dict.

        Returns:
            Dict with name, description, tags, and config.
        """
        return {
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "config": self.config,
        }


# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------

_WEB_APP_TEMPLATE = ConfigTemplate(
    name="web-app",
    description="Full-stack web application with frontend, backend, and QA agents.",
    config={
        "goal": "Build and maintain a web application",
        "cli": "auto",
        "max_agents": 4,
        "team": ["backend", "frontend", "qa"],
        "merge_strategy": "pr",
        "auto_merge": False,
        "quality_gates": {
            "enabled": True,
            "lint": True,
            "type_check": True,
            "tests": True,
        },
    },
    tags=("web", "fullstack", "frontend", "backend"),
)

_MICROSERVICES_TEMPLATE = ConfigTemplate(
    name="microservices",
    description="Microservices architecture with independent service agents.",
    config={
        "goal": "Develop and maintain microservices",
        "cli": "auto",
        "max_agents": 8,
        "team": ["backend", "devops", "qa", "security"],
        "merge_strategy": "pr",
        "auto_merge": False,
        "quality_gates": {
            "enabled": True,
            "lint": True,
            "type_check": True,
            "tests": True,
        },
        "constraints": [
            "Each service must be independently deployable",
            "Use API contracts between services",
        ],
    },
    tags=("microservices", "distributed", "api", "devops"),
)

_MONOREPO_TEMPLATE = ConfigTemplate(
    name="monorepo",
    description="Monorepo with multiple packages and shared infrastructure.",
    config={
        "goal": "Manage a monorepo with multiple packages",
        "cli": "auto",
        "max_agents": 6,
        "team": "auto",
        "merge_strategy": "pr",
        "auto_merge": True,
        "quality_gates": {
            "enabled": True,
            "lint": True,
            "type_check": True,
            "tests": True,
        },
        "constraints": [
            "Respect package boundaries",
            "Run only affected tests",
        ],
    },
    tags=("monorepo", "multi-package", "shared"),
)

_DATA_PIPELINE_TEMPLATE = ConfigTemplate(
    name="data-pipeline",
    description="Data processing pipeline with ETL and ML components.",
    config={
        "goal": "Build and maintain data processing pipelines",
        "cli": "auto",
        "max_agents": 4,
        "team": ["backend", "ml-engineer", "qa"],
        "merge_strategy": "pr",
        "auto_merge": False,
        "quality_gates": {
            "enabled": True,
            "lint": True,
            "tests": True,
        },
    },
    tags=("data", "pipeline", "etl", "ml"),
)

_LIBRARY_TEMPLATE = ConfigTemplate(
    name="library",
    description="Reusable library or SDK with strict quality gates.",
    config={
        "goal": "Develop a reusable library",
        "cli": "auto",
        "max_agents": 3,
        "team": ["backend", "qa", "docs"],
        "merge_strategy": "pr",
        "auto_merge": False,
        "quality_gates": {
            "enabled": True,
            "lint": True,
            "type_check": True,
            "tests": True,
        },
        "constraints": [
            "Maintain backward compatibility",
            "Document all public APIs",
        ],
    },
    tags=("library", "sdk", "package", "api"),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class TemplateRegistry:
    """Registry of available config templates.

    Supports listing, retrieval by name, and search by tag.

    Attributes:
        templates: Mapping from template name to template instance.
    """

    templates: dict[str, ConfigTemplate] = field(default_factory=dict)

    def register(self, template: ConfigTemplate) -> None:
        """Register a new config template.

        Args:
            template: Template to add to the registry.
        """
        self.templates[template.name] = template

    def get(self, name: str) -> ConfigTemplate | None:
        """Retrieve a template by name.

        Args:
            name: Template identifier.

        Returns:
            The template, or None if not found.
        """
        return self.templates.get(name)

    def list_all(self) -> list[ConfigTemplate]:
        """Return all registered templates sorted by name.

        Returns:
            Sorted list of templates.
        """
        return sorted(self.templates.values(), key=lambda t: t.name)

    def search(self, tag: str) -> list[ConfigTemplate]:
        """Find templates matching a tag.

        Args:
            tag: Tag to search for (case-insensitive).

        Returns:
            List of templates that have the matching tag.
        """
        tag_lower = tag.lower()
        return [t for t in self.templates.values() if tag_lower in (x.lower() for x in t.tags)]

    def names(self) -> list[str]:
        """Return all registered template names.

        Returns:
            Sorted list of template names.
        """
        return sorted(self.templates.keys())


def default_registry() -> TemplateRegistry:
    """Create a registry pre-loaded with all built-in templates.

    Returns:
        TemplateRegistry with web-app, microservices, monorepo,
        data-pipeline, and library templates.
    """
    registry = TemplateRegistry()
    for template in (
        _WEB_APP_TEMPLATE,
        _MICROSERVICES_TEMPLATE,
        _MONOREPO_TEMPLATE,
        _DATA_PIPELINE_TEMPLATE,
        _LIBRARY_TEMPLATE,
    ):
        registry.register(template)
    return registry
