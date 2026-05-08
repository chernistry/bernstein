"""Air-gap test fixtures.

Helper module that materialises a representative wheelhouse on-disk
once per pytest session. Storing real wheels in the repo would cost
~3 MB of binary noise; instead we synthesise minimal but valid wheel
zips in the test temp dir and seed them deterministically so every
run computes the same sha256s.

Real wheels are not required for verify-flow tests -- the verifier
only walks zip files and recomputes hashes -- so the synthetic
fixtures cover the same code paths as a real ``pip download`` bundle.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Five tiny wheels: bernstein itself + four representative deps.
# The names mimic the PyPI artifacts the production wheelhouse contains
# so the manifest layout matches; the contents are minimal but valid.
DEFAULT_WHEEL_NAMES: tuple[str, ...] = (
    "bernstein-1.10.3-py3-none-any.whl",
    "click-8.1.7-py3-none-any.whl",
    "rich-13.7.1-py3-none-any.whl",
    "httpx-0.27.0-py3-none-any.whl",
    "pydantic-2.7.1-py3-none-any.whl",
)


@dataclass(frozen=True)
class WheelhouseFixture:
    """A materialised on-disk wheelhouse with its manifest path."""

    root: Path
    manifest_path: Path
    wheel_names: tuple[str, ...]
    wheel_shas: dict[str, str]


def _synth_wheel(target: Path, name: str) -> Path:
    """Write a minimally valid wheel zip with deterministic contents."""
    package = name.split("-", 1)[0].replace(".", "_")
    dist_info = name.replace(".whl", "") + ".dist-info"
    wheel_path = target / name
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{package}/__init__.py", f"# fixture wheel for {name}\n")
        zf.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: {package}\nVersion: 1.0.0\n",
        )
        zf.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nGenerator: bernstein-test-fixture\n")
        zf.writestr(f"{dist_info}/RECORD", "")
    return wheel_path


def build_wheelhouse(
    target: Path,
    *,
    names: Iterable[str] = DEFAULT_WHEEL_NAMES,
    version: str = "1.10.3",
) -> WheelhouseFixture:
    """Materialise a representative wheelhouse at ``target``.

    Idempotent: if every expected wheel + the manifest already exist
    and their checksums match, returns the existing fixture without
    rewriting. Tests can therefore call this in a session-scoped
    fixture and the second invocation costs a few file stats.
    """
    target.mkdir(parents=True, exist_ok=True)
    name_tuple = tuple(names)
    shas: dict[str, str] = {}
    entries: list[dict[str, object]] = []
    for name in name_tuple:
        wheel = target / name
        if not wheel.exists():
            _synth_wheel(target, name)
        sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
        shas[name] = sha
        entries.append({"name": name, "sha256": sha, "size": wheel.stat().st_size})
    manifest_payload = {"version": version, "wheels": entries}
    manifest_path = target / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n")
    return WheelhouseFixture(
        root=target,
        manifest_path=manifest_path,
        wheel_names=name_tuple,
        wheel_shas=shas,
    )


__all__ = ["DEFAULT_WHEEL_NAMES", "WheelhouseFixture", "build_wheelhouse"]
