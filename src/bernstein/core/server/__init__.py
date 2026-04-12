"""server sub-package — re-exports for backward compatibility.

Delegates to the canonical parent-level modules (server_app, server_models,
server_middleware) so that ``from bernstein.core.server import X`` resolves
to the same objects as ``from bernstein.core.server_app import X``.
"""

from bernstein.core.server_app import *  # noqa: F403
from bernstein.core.server_models import *  # noqa: F403
from bernstein.core.server_middleware import *  # noqa: F403

# Re-export TaskStore for backward compatibility
from bernstein.core.task_store import TaskStore as TaskStore

from typing import Any as _Any


def __getattr__(name: str) -> _Any:
    """Lazy module-level attribute for ``app``."""
    if name == "app":
        from bernstein.core import server_app

        return server_app.__getattr__("app")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
