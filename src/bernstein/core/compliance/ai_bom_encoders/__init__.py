"""AI-BOM encoders registered via :mod:`bernstein.core.compliance.ai_bom`.

Each encoder module exposes a single ``encode_<fmt>`` function that
accepts an :class:`bernstein.core.compliance.ai_bom.AIBOM` and returns
deterministic UTF-8 bytes. The dispatcher in ``ai_bom.py`` owns the
format-name registry; encoders themselves are stateless.
"""

from __future__ import annotations

__all__: list[str] = []
