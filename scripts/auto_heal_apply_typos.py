"""Append vendor-field-shaped tokens to ``typos.toml``'s extend-words.

Called by ``.github/workflows/auto-heal.yml`` after
``scripts/auto_heal_typos.py`` has filtered the failing tokens. Kept as a
standalone module (instead of an inline workflow heredoc) so it stays
unit-testable and pyright-clean.

Idempotency: tokens already present in any ``key = "..."`` assignment
inside the file are skipped, so re-running the script never duplicates
entries.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

_MARKER: Final[str] = "[default.extend-words]"
_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=",
    re.MULTILINE,
)


def existing_keys(text: str) -> set[str]:
    """Return all left-hand-side identifiers from ``key = ...`` lines."""
    return set(_KEY_RE.findall(text))


def render_additions(tokens: list[str], existing: set[str]) -> list[str]:
    """Return the lines to insert for ``tokens`` not already in
    ``existing``. Each line is a self-referential entry plus a comment.
    """
    out: list[str] = []
    for token in tokens:
        if not token or token in existing:
            continue
        out.append(f'{token} = "{token}"  # auto-heal: vendor field token')
    return out


def apply(config_path: Path, tokens: list[str]) -> bool:
    """Mutate ``config_path`` in place to include ``tokens``. Returns
    True when the file changed, False otherwise.
    """
    text = config_path.read_text()
    if _MARKER not in text:
        text += f"\n{_MARKER}\n"
    additions = render_additions(tokens, existing_keys(text))
    if not additions:
        return False
    insertion = "\n".join(additions) + "\n"
    new_text = text.replace(_MARKER + "\n", _MARKER + "\n" + insertion, 1)
    if new_text == text:
        return False
    config_path.write_text(new_text)
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--tokens", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.config.exists():
        sys.stderr.write(f"config not found: {args.config}\n")
        return 1
    if not args.tokens.exists():
        sys.stderr.write(f"tokens file not found: {args.tokens}\n")
        return 1
    tokens = [line.strip() for line in args.tokens.read_text().splitlines() if line.strip()]
    changed = apply(args.config, tokens)
    sys.stdout.write("changed\n" if changed else "no_op\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
