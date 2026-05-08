"""Fake-CLI test harness for end-to-end adapter integration tests.

Real upstream CLIs (claude, codex, gemini, aider, ollama) are not installed
on CI runners.  This package ships a tiny self-contained Python script
(``fake_cli.py``) that impersonates those binaries: it parses argv, prints
a profile-shaped stdout, optionally writes sidecar files the adapter
expects, and exits 0 (or non-zero on opt-in flag).

The companion fixtures in :mod:`tests.integration.conftest_adapters`
prepend a tempdir of symlinks pointing at this script onto ``PATH`` so
adapters spawn the fake binary when they call ``subprocess.Popen([
'claude', ...])``.

The harness is stdlib-only — no extra dependencies.
"""
