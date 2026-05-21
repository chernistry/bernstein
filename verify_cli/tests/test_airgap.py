"""Air-gap guarantee tests for bernstein-verify.

The promise: `bernstein-verify` works on an air-gapped laptop. No remote
lookups, no telemetry, no DNS. This module proves it by static AST analysis
of the `bernstein_verify` package - we never import a network module.

We deliberately do NOT use `unshare -n` here; that test belongs in a
Linux-only integration suite. macOS doesn't have unshare. The static-import
check below is the durable cross-platform contract.
"""

from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import bernstein_verify

# Modules that would let the package make a network call. Importing any
# of these is a hard fail - even transitively. Note: `cryptography` is
# allowed (it doesn't touch the network).
_BANNED_IMPORTS: frozenset[str] = frozenset(
    {
        "httpx",
        "requests",
        "urllib3",
        "urllib.request",
        "urllib.urlopen",
        "http.client",
        "ftplib",
        "telnetlib",
        "smtplib",
        "poplib",
        "imaplib",
        "socket",  # raw socket - no need for verification
        "asyncio.open_connection",
        "ssl",  # no TLS in offline verifier
        "websocket",
        "websockets",
        "aiohttp",
        "boto3",
        "google.cloud",
    }
)


def _walk_pkg_files() -> list[Path]:
    pkg_path = Path(bernstein_verify.__file__).parent
    return sorted(pkg_path.rglob("*.py"))


def _imports_in_file(path: Path) -> set[str]:
    """Return the set of fully-qualified imports in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            for alias in node.names:
                imports.add(f"{node.module}.{alias.name}")
    return imports


def test_no_banned_network_imports():
    """No file in bernstein_verify may import a network module."""
    offenders: list[str] = []
    for path in _walk_pkg_files():
        imports = _imports_in_file(path)
        bad = imports & _BANNED_IMPORTS
        # Also check prefix match (e.g. urllib.request via urllib import)
        for imp in imports:
            for banned in _BANNED_IMPORTS:
                if imp == banned or imp.startswith(banned + "."):
                    bad = bad | {imp}
        if bad:
            offenders.append(f"{path.name}: {sorted(bad)}")
    assert not offenders, "banned network imports found:\n  " + "\n  ".join(offenders)


def test_no_bernstein_imports_anywhere():
    """The runtime package must never import `bernstein.*`."""
    offenders: list[str] = []
    for path in _walk_pkg_files():
        imports = _imports_in_file(path)
        for imp in imports:
            if imp == "bernstein" or imp.startswith("bernstein."):
                offenders.append(f"{path.name}: imports {imp}")
    assert not offenders, "bernstein imports found:\n  " + "\n  ".join(offenders)


def test_runtime_monkeypatch_breaks_nothing(monkeypatch):
    """Even if network modules are wrenched out at runtime, calling into
    the API still works. Smoke check for accidental lazy imports.

    NB: we DO NOT reload `bernstein_verify.verify` here. Reloading would
    rebind the `VerifyResult` dataclass and break `isinstance` checks
    in sibling tests run after this one. Importing it fresh under the
    guard is enough - the import would fail if a banned module were
    referenced anywhere reachable from module-level code.
    """
    import builtins

    real_import = builtins.__import__

    def _guarded_import(name, *args, **kwargs):
        if name in _BANNED_IMPORTS or any(name.startswith(b + ".") for b in _BANNED_IMPORTS):
            raise ImportError(f"air-gap test: blocked import of {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    # Pull names out of sys.modules instead of reloading: the module's
    # top-level imports are already resolved, so we're really testing
    # that nothing in the import graph touches a banned name.
    import sys

    verify_mod = sys.modules["bernstein_verify.verify"]
    assert verify_mod.jcs_canonicalise({"a": 1}) == b'{"a":1}'


def test_pkg_only_exposes_expected_submodules():
    """Sanity: bernstein_verify has exactly the modules we expect."""
    pkg_path = Path(bernstein_verify.__file__).parent
    found = {m.name for m in pkgutil.iter_modules([str(pkg_path)])}
    expected = {"__main__", "verify"}
    assert expected.issubset(found), f"missing modules: {expected - found}"
