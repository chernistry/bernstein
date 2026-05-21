#!/usr/bin/env python3
"""Build an air-gap wheel bundle for offline installs.

Resolves the full pinned dependency closure of Bernstein via
``uv export --format requirements-txt``, downloads each wheel
(no transitive resolution - the closure is already pinned),
writes them into ``dist/airgap-wheelhouse/<version>/``, and
emits a ``MANIFEST.json`` listing every wheel with its sha256.

Companion: ``scripts/sign_airgap_wheelhouse.sh`` produces detached
signatures. ``bernstein verify <path>`` validates checksums (and
signatures when present).

Usage:
    python scripts/build_airgap_wheelhouse.py --version 1.9.4
    python scripts/build_airgap_wheelhouse.py --output dist/wh
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


@dataclass
class WheelEntry:
    name: str
    sha256: str
    size: int


@dataclass
class BuildResult:
    output_dir: Path
    version: str
    wheels: list[WheelEntry] = field(default_factory=list)
    manifest_path: Path | None = None


def _read_project_version() -> str:
    text = PYPROJECT.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise RuntimeError("could not read project version from pyproject.toml")


def _export_requirements(workdir: Path) -> Path:
    """Resolve the pinned dependency closure via uv export."""
    out = workdir / "requirements.txt"
    cmd = [
        "uv",
        "export",
        "--format",
        "requirements-txt",
        "--no-emit-project",
        "--no-hashes",
        "-o",
        str(out),
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    return out


def _build_project_wheel(workdir: Path) -> Path:
    """Build the bernstein wheel itself via the standard PEP-517 backend."""
    cmd = [
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--outdir",
        str(workdir),
        str(REPO_ROOT),
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("python -m build is required (pip install build)") from exc
    wheels = sorted(workdir.glob("bernstein-*.whl"))
    if not wheels:
        raise RuntimeError("no bernstein wheel built")
    return wheels[-1]


def _download_deps(req_file: Path, target: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary",
        ":all:",
        "-r",
        str(req_file),
        "-d",
        str(target),
    ]
    subprocess.run(cmd, check=True)


def _hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _write_manifest(target: Path, version: str, wheels: list[WheelEntry]) -> Path:
    manifest_path = target / "MANIFEST.json"
    payload = {
        "version": version,
        "generated_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
        "wheels": [{"name": w.name, "sha256": w.sha256, "size": w.size} for w in sorted(wheels, key=lambda x: x.name)],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return manifest_path


def build(*, version: str | None, output: Path | None, skip_project: bool = False) -> BuildResult:
    """Build the wheelhouse and return the result.

    Args:
        version: Override the package version label. ``None`` means read
            from ``pyproject.toml``.
        output: Destination directory. ``None`` means
            ``dist/airgap-wheelhouse/<version>/``.
        skip_project: When True, skip building the bernstein wheel itself
            (used in tests where we only need a representative bundle).
    """
    resolved_version = version or _read_project_version()
    target = output or (REPO_ROOT / "dist" / "airgap-wheelhouse" / resolved_version)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        try:
            req_file = _export_requirements(tmpdir)
            _download_deps(req_file, target)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"warning: dep download skipped - {exc}", file=sys.stderr)

        if not skip_project:
            try:
                project_wheel = _build_project_wheel(tmpdir)
                shutil.copy2(project_wheel, target / project_wheel.name)
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                print(f"warning: project wheel build skipped - {exc}", file=sys.stderr)

    wheels: list[WheelEntry] = []
    for path in sorted(target.glob("*.whl")):
        sha, size = _hash_file(path)
        wheels.append(WheelEntry(name=path.name, sha256=sha, size=size))

    manifest_path = _write_manifest(target, resolved_version, wheels)
    return BuildResult(output_dir=target, version=resolved_version, wheels=wheels, manifest_path=manifest_path)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="version label (defaults to pyproject)")
    parser.add_argument("--output", default=None, help="output directory")
    parser.add_argument("--skip-project", action="store_true", help="skip building bernstein wheel")
    args = parser.parse_args()

    result = build(
        version=args.version,
        output=Path(args.output).resolve() if args.output else None,
        skip_project=args.skip_project,
    )
    print(f"wheelhouse: {result.output_dir}")
    print(f"wheels: {len(result.wheels)}")
    print(f"manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
