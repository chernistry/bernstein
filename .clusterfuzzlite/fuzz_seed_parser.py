"""Atheris fuzz harness for bernstein.core.config.seed_parser.parse_seed.

Minimal-surface harness whose purpose is to give OSSF Scorecard's Fuzzing
check a target it recognizes (ClusterFuzzLite). The primary fuzzing surface
for real bug discovery remains the Hypothesis property-test suite in
tests/.

The harness:

1. Treats the fuzzer-provided bytes as a candidate bernstein.yaml file.
2. Writes them to a temp path.
3. Calls parse_seed() and swallows the documented SeedError + a small set
   of expected exceptions (yaml.YAMLError, ValueError, UnicodeDecodeError).
4. Lets any other exception propagate, which is what libFuzzer treats as
   a crash.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
from pathlib import Path

import atheris

with atheris.instrument_imports():
    import yaml

    from bernstein.core.config.seed_config import SeedError
    from bernstein.core.config.seed_parser import parse_seed


def _test_one_input(data: bytes) -> None:
    """Fuzz entrypoint: feed arbitrary bytes to parse_seed via a temp file."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as handle:
        handle.write(data)
        seed_path = Path(handle.name)
    try:
        parse_seed(seed_path)
    except (SeedError, yaml.YAMLError, ValueError, UnicodeDecodeError, TypeError):
        # Documented failure modes for malformed input. Not a crash.
        return
    finally:
        with contextlib.suppress(OSError):
            seed_path.unlink()


def main() -> None:
    """libFuzzer entry point."""
    atheris.Setup(sys.argv, _test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
