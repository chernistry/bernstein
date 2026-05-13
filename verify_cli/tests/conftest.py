"""Shared fixtures for bernstein-verify tests.

These tests live alongside `bernstein_verify` but the *runtime* package
must never import `bernstein`. Test code is allowed to import bernstein
to cross-verify byte-for-byte compatibility with the original signer.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `bernstein_verify` importable when running from verify_cli/.
# Hatch/editable install would do this too, but pytest invoked directly
# from the repo root needs the path.
_PKG_PARENT = Path(__file__).resolve().parent.parent
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))
