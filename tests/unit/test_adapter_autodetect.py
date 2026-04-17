"""Tests for adapter auto-detection (AGENT-015)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from bernstein.core.adapter_autodetect import (
    _KNOWN_BINARIES,
    DetectedAdapter,
    ScanResult,
    auto_register_adapters,
    scan_for_adapters,
)

# Adapter registry names that do not have a 1:1 source file under
# ``src/bernstein/adapters/`` (handled by a module with a different stem).
_ADAPTER_FILE_OVERRIDES: dict[str, str] = {
    "continue": "continue_dev.py",
}

# Adapter registry names whose binary is intentionally different from what the
# adapter's own ``cmd = [...]`` line spawns (e.g. ``ollama`` adapter shells out
# to ``aider`` but we detect the ollama server binary as a proxy for the
# integration being usable; ``iac`` adapter spawns ``bash`` to wrap the real
# tool call, so we match against the ``_TOOL_DEFS`` table instead).
_BINARY_FILE_OVERRIDES: dict[str, tuple[str, ...]] = {
    "ollama": ("ollama.py",),
    "terraform": ("iac.py",),
    "pulumi": ("iac.py",),
}


class TestScanForAdapters:
    def test_scan_finds_nothing_when_path_empty(self) -> None:
        with patch("bernstein.core.agents.adapter_autodetect.shutil.which", return_value=None):
            result = scan_for_adapters()
        assert len(result.found) == 0
        assert len(result.missing) == len(_KNOWN_BINARIES)

    def test_scan_finds_known_binary(self) -> None:
        def mock_which(name: str) -> str | None:
            if name == "claude":
                return "/usr/bin/claude"
            return None

        with patch("bernstein.core.agents.adapter_autodetect.shutil.which", side_effect=mock_which):
            result = scan_for_adapters()
        found_names = [d.adapter_name for d in result.found]
        assert "claude" in found_names
        claude = next(d for d in result.found if d.adapter_name == "claude")
        assert claude.binary_path == "/usr/bin/claude"

    def test_scan_extra_binaries(self) -> None:
        def mock_which(name: str) -> str | None:
            if name == "my-agent":
                return "/usr/local/bin/my-agent"
            return None

        with patch("bernstein.core.agents.adapter_autodetect.shutil.which", side_effect=mock_which):
            result = scan_for_adapters(extra_binaries={"my-agent": "myagent"})
        found_names = [d.adapter_name for d in result.found]
        assert "myagent" in found_names

    def test_scan_result_structure(self) -> None:
        result = ScanResult()
        assert result.found == []
        assert result.missing == []


class TestDetectedAdapter:
    def test_fields(self) -> None:
        da = DetectedAdapter(
            adapter_name="claude",
            binary_name="claude",
            binary_path="/usr/bin/claude",
        )
        assert da.adapter_name == "claude"
        assert da.binary_path == "/usr/bin/claude"


class TestAutoRegisterAdapters:
    def test_auto_register_no_crash(self) -> None:
        with patch("bernstein.core.agents.adapter_autodetect.shutil.which", return_value=None):
            result = auto_register_adapters()
        assert isinstance(result, ScanResult)

    def test_known_binaries_mapping(self) -> None:
        # Verify the mapping contains expected entries
        assert "claude" in _KNOWN_BINARIES
        assert "codex" in _KNOWN_BINARIES
        assert "gemini" in _KNOWN_BINARIES

    def test_kiro_binary_uses_cli_suffix(self) -> None:
        # Regression test for audit-130: real binary is ``kiro-cli``, not ``kiro``.
        assert "kiro-cli" in _KNOWN_BINARIES
        assert _KNOWN_BINARIES["kiro-cli"] == "kiro"
        assert "kiro" not in _KNOWN_BINARIES

    def test_iac_binaries_registered(self) -> None:
        # Regression test for audit-130: ``iac`` adapter needs terraform/pulumi entries.
        assert _KNOWN_BINARIES.get("terraform") == "iac"
        assert _KNOWN_BINARIES.get("pulumi") == "iac"


class TestKnownBinariesMatchAdapterSources:
    """Every ``_KNOWN_BINARIES`` entry must match what the adapter really spawns.

    Iterates the map and, for each ``(binary, adapter_name)`` pair, reads the
    corresponding adapter source file under ``src/bernstein/adapters/`` and
    asserts that the binary name appears verbatim as a quoted string
    (i.e. in a ``cmd = [...]`` call or an equivalent tool-definitions table).
    This catches autodetect/adapter drift like kiro→kiro when the adapter
    actually exec's ``kiro-cli``.
    """

    @staticmethod
    def _adapters_dir() -> Path:
        import bernstein.adapters as _adapters_pkg

        return Path(_adapters_pkg.__file__).parent

    def _adapter_files_for(self, binary: str, adapter_name: str) -> list[Path]:
        adapters_dir = self._adapters_dir()
        overrides = _BINARY_FILE_OVERRIDES.get(binary)
        if overrides is not None:
            return [adapters_dir / name for name in overrides]
        filename = _ADAPTER_FILE_OVERRIDES.get(adapter_name, f"{adapter_name}.py")
        return [adapters_dir / filename]

    @staticmethod
    def _binary_in_source(binary: str, source: str) -> bool:
        """Return True iff ``binary`` appears as a cmd/tool token in ``source``.

        Looks for the binary as a standalone quoted literal (``"kiro-cli"``)
        or as a bare identifier with CLI word boundaries (so ``kiro`` does not
        match ``kiro-cli``). The latter catches cases like ``ollama`` which
        is embedded in help text, env var names, and f-strings.
        """
        if f'"{binary}"' in source or f"'{binary}'" in source:
            return True
        # CLI word boundary: anything except another identifier char or a hyphen/slash.
        pattern = rf"(?<![A-Za-z0-9_\-/]){re.escape(binary)}(?![A-Za-z0-9_\-])"
        return re.search(pattern, source) is not None

    def test_every_binary_appears_in_adapter_source(self) -> None:
        missing: list[tuple[str, str, Path]] = []
        for binary, adapter_name in _KNOWN_BINARIES.items():
            for adapter_file in self._adapter_files_for(binary, adapter_name):
                assert adapter_file.is_file(), (
                    f"Adapter source file {adapter_file} does not exist "
                    f"for binary {binary!r} -> adapter {adapter_name!r}"
                )
                source = adapter_file.read_text(encoding="utf-8")
                if not self._binary_in_source(binary, source):
                    missing.append((binary, adapter_name, adapter_file))
        assert not missing, "Binary names in _KNOWN_BINARIES do not appear in their adapter source files: " + ", ".join(
            f"{b!r} ({a} @ {p.name})" for b, a, p in missing
        )

    def test_kiro_adapter_spawns_kiro_cli(self) -> None:
        """Regression guard for audit-130.

        The kiro adapter must spawn ``kiro-cli`` (not ``kiro``) as the
        first argument of its ``cmd`` list — if anyone changes that, the
        autodetect entry ``kiro-cli → kiro`` also needs to change.
        """
        adapter_file = self._adapters_dir() / "kiro.py"
        source = adapter_file.read_text(encoding="utf-8")
        assert '"kiro-cli"' in source
        # And the autodetect key must agree.
        assert "kiro-cli" in _KNOWN_BINARIES

    def test_every_adapter_name_is_registered(self) -> None:
        from bernstein.adapters.registry import _ADAPTERS

        unknown = {adapter for adapter in _KNOWN_BINARIES.values() if adapter not in _ADAPTERS}
        assert not unknown, f"Autodetect references adapters missing from registry: {sorted(unknown)}"
