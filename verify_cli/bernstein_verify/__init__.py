"""bernstein-verify - standalone auditor CLI for Bernstein lineage v1 packs.

This package MUST NOT import from `bernstein.*` at runtime. The whole point
of shipping it as a separate wheel is that an auditor on an air-gapped
laptop with only `cryptography` + `click` can verify a compliance pack
without installing the full orchestration stack.

See verify_cli/README.md and docs/decisions/009-lineage-v1.md §9.
"""

__version__ = "1.0.0"
