"""Bernstein scaffold subsystem.

Public API:
    SCAFFOLD_TEMPLATES, ScaffoldTemplate, ScaffoldError,
    list_template_names, pick_template, materialize_template.
"""

from bernstein.cli.scaffold.templates import (
    SCAFFOLD_TEMPLATES,
    ScaffoldError,
    ScaffoldTemplate,
    list_template_names,
    materialize_template,
    pick_template,
)

__all__ = [
    "SCAFFOLD_TEMPLATES",
    "ScaffoldError",
    "ScaffoldTemplate",
    "list_template_names",
    "materialize_template",
    "pick_template",
]
