"""Terminal QR code rendering for the PWA onboarding flow.

The :func:`render_ascii_qr` helper wraps the third-party ``qrcode`` library
when available, and falls back to a tiny pure-Python placeholder block
when it is not. The placeholder is intentionally NOT a real QR code - it
is a human-readable diagnostic that tells the operator the ``qrcode``
extra is missing.

The wrapping behaviour is split out so it can be unit-tested without
shelling out to a real binary.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

#: Two-character black tile (full block) - renders as a solid square on
#: every monospaced terminal (including macOS Terminal, iTerm, Windows
#: Terminal, and modern Linux terminal emulators).
QR_DARK: Final[str] = "██"

#: Two-character white tile (space pair) - kept symmetric with QR_DARK so
#: the QR remains a true 1:1 aspect ratio when scanned with a phone camera.
QR_LIGHT: Final[str] = "  "

#: Half-block (upper) - used for the "compact" renderer which packs two
#: QR rows into a single terminal row using foreground/background colour.
QR_HALF: Final[str] = "▀"


def _qrcode_available() -> bool:
    """Return whether the ``qrcode`` library can be imported.

    Centralised so tests can monkeypatch this single helper to exercise
    both code paths without removing the package from ``sys.modules``.
    """
    try:
        import qrcode  # pyright: ignore[reportUnusedImport, reportMissingTypeStubs]  # noqa: F401

        return True
    except Exception:  # pragma: no cover - extremely rare path
        return False


def render_ascii_qr(data: str, *, border: int = 2, error_correction: str = "M") -> str:
    """Render ``data`` as an ASCII QR code suitable for terminal output.

    Args:
        data: The string to encode. Empty strings are rejected.
        border: Quiet-zone width in modules around the QR. Must be ``>= 0``.
        error_correction: One of ``"L"``, ``"M"``, ``"Q"``, or ``"H"`` -
            mapped to the ``qrcode`` library's correction constants.

    Returns:
        A multi-line string. Each module is rendered as two characters
        wide to keep the QR aspect ratio square in a monospaced font.

    Raises:
        ValueError: If ``data`` is empty, ``border`` is negative, or
            ``error_correction`` is not one of L/M/Q/H.
    """
    if not data:
        raise ValueError("QR data must be a non-empty string")
    if border < 0:
        raise ValueError("QR border must be >= 0")
    if error_correction not in ("L", "M", "Q", "H"):
        raise ValueError(f"error_correction must be L/M/Q/H, got {error_correction!r}")

    if not _qrcode_available():
        return _fallback_placeholder(data, border=border)

    return _render_with_qrcode(data, border=border, error_correction=error_correction)


def _render_with_qrcode(data: str, *, border: int, error_correction: str) -> str:
    """Render via the ``qrcode`` library. Caller guarantees availability."""
    import qrcode  # type: ignore[import-untyped]
    from qrcode.constants import (  # type: ignore[import-untyped]
        ERROR_CORRECT_H,
        ERROR_CORRECT_L,
        ERROR_CORRECT_M,
        ERROR_CORRECT_Q,
    )

    ec_map = {
        "L": ERROR_CORRECT_L,
        "M": ERROR_CORRECT_M,
        "Q": ERROR_CORRECT_Q,
        "H": ERROR_CORRECT_H,
    }
    qr = qrcode.QRCode(
        version=None,  # let the library pick the smallest version
        error_correction=ec_map[error_correction],
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    return _matrix_to_text(matrix)


def _matrix_to_text(matrix: list[list[bool]]) -> str:
    """Render a boolean matrix to the dark/light ASCII representation.

    Args:
        matrix: 2-D boolean list, ``True`` for dark modules.

    Returns:
        Multi-line ASCII rendering.
    """
    lines: list[str] = []
    for row in matrix:
        line_parts: list[str] = [QR_DARK if cell else QR_LIGHT for cell in row]
        lines.append("".join(line_parts))
    return "\n".join(lines)


def _fallback_placeholder(data: str, *, border: int) -> str:
    """Render a visible diagnostic when the ``qrcode`` library is missing.

    The output is NOT a scannable QR - it explicitly tells the operator
    what to install. Kept inside a hash-fence so it is easy to spot in
    captured logs.
    """
    pad = " " * border
    inner_lines = [
        "###  QR rendering unavailable  ###",
        "Install the qrcode extra to print a scannable QR:",
        "  pip install qrcode",
        "Tunnel URL (open it on your phone):",
        f"  {data}",
    ]
    width = max(len(line) for line in inner_lines) + 2 * len(pad)
    border_line = "#" * width
    body = "\n".join(pad + line.ljust(width - 2 * len(pad)) + pad for line in inner_lines)
    return f"{border_line}\n{body}\n{border_line}"
