"""Planned cost modules (wanted-but-unwired).

Modules in this sub-package have unit tests but no production wiring
yet. They are kept here, segregated from the live cost pipeline, until
a follow-up ticket wires them into the orchestrator. Do not import
from elsewhere in ``src/`` until that wiring lands.

Current members:
    - ``api_usage``: per-provider/tier API call tracking
    - ``cheaper_retry``: downgrade model on retry
    - ``retry_budget``: per-task and per-run retry limits
"""
