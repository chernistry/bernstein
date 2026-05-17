"""commands sub-package.

Re-exports the ``adapters`` Click group so callers can do::

    from bernstein.cli.commands import adapters_group

without depending on the historical layout under ``adapter_cmd``.
"""

from __future__ import annotations

from bernstein.cli.commands.adapter_cmd import adapters_group
from bernstein.cli.commands.adapters_cmd import (
    adapters_check_cmd,
    adapters_list_status_cmd,
)

__all__ = [
    "adapters_check_cmd",
    "adapters_group",
    "adapters_list_status_cmd",
]
