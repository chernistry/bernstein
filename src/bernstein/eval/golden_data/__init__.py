"""Packaged golden benchmark fixtures (ships in wheel via package-data).

These markdown fixtures are loaded as a fallback when the operator has not
seeded their own copies under ``.sdd/eval/golden/<tier>/`` in the working
repo.  They keep ``bernstein eval run --tier smoke`` working out of the
box on a fresh ``pip install bernstein`` install.

Files are discovered via :func:`importlib.resources.files` -- see
``bernstein.eval.golden._packaged_tier_files``.
"""
