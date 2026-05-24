"""Regression coverage for Sonar invalid runtime type-check findings."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DISALLOWED_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "src/bernstein/core/autofix/daemon.py": (
        "sleep_fn: object",
        "now_fn: object",
    ),
    "src/bernstein/core/planning/routine_bridge.py": ("poster: object",),
    "src/bernstein/core/security/rfc3161_verifier.py": (
        "signer_info: object",
        "tuple[object, object, list[x509.Certificate], object]",
        "type: ignore[index]",
    ),
    "src/bernstein/core/substrate/host_registry.py": (
        "_path_resolver: object",
        "type: ignore[operator]",
    ),
    "src/bernstein/gui/cli.py": (
        "echo: object",
        "type: ignore[operator]",
    ),
}


def test_sonar_invalid_type_check_patterns_do_not_regress() -> None:
    """Call seams should use callable protocols instead of object escapes."""
    failures: list[str] = []

    for relative_path, fragments in DISALLOWED_FRAGMENTS.items():
        source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment in source:
                failures.append(f"{relative_path}: {fragment}")

    assert not failures
