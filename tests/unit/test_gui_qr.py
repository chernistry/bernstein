"""Unit tests for the QR renderer (#1218)."""

from __future__ import annotations

import pytest

from bernstein.gui import qr as qr_module


def test_render_rejects_empty_data() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        qr_module.render_ascii_qr("")


def test_render_rejects_negative_border() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        qr_module.render_ascii_qr("https://example.com", border=-1)


@pytest.mark.parametrize("ec", ["X", "", "low", "Lwest"])
def test_render_rejects_invalid_error_correction(ec: str) -> None:
    with pytest.raises(ValueError, match="error_correction"):
        qr_module.render_ascii_qr("https://example.com", error_correction=ec)


@pytest.mark.parametrize("ec", ["L", "M", "Q", "H"])
def test_render_accepts_all_valid_ec_levels(ec: str) -> None:
    out = qr_module.render_ascii_qr("https://example.com/x", error_correction=ec)
    assert isinstance(out, str)
    assert out.strip()


def test_render_returns_str() -> None:
    out = qr_module.render_ascii_qr("https://example.com")
    assert isinstance(out, str)


@pytest.mark.skipif(
    not qr_module._qrcode_available(),
    reason="qrcode extra not installed; render_ascii_qr emits fallback diagnostic",
)
def test_render_contains_dark_modules() -> None:
    out = qr_module.render_ascii_qr("https://example.com")
    assert qr_module.QR_DARK in out


def test_render_is_rectangular() -> None:
    out = qr_module.render_ascii_qr("https://example.com")
    rows = out.splitlines()
    # Every row should have the same width (square QR).
    assert len({len(r) for r in rows}) == 1


@pytest.mark.skipif(
    not qr_module._qrcode_available(),
    reason="qrcode extra not installed; fallback diagnostic is not square",
)
def test_render_is_square() -> None:
    out = qr_module.render_ascii_qr("https://example.com/abcd")
    rows = out.splitlines()
    # Width is in "characters" -> each module is 2 chars wide, so the
    # rendered width equals 2 * row count.
    assert len(rows[0]) == 2 * len(rows)


def test_render_is_deterministic() -> None:
    a = qr_module.render_ascii_qr("https://example.com/snapshot", border=2)
    b = qr_module.render_ascii_qr("https://example.com/snapshot", border=2)
    assert a == b


def test_render_smaller_border_smaller_output() -> None:
    big = qr_module.render_ascii_qr("https://x.example.com", border=4)
    small = qr_module.render_ascii_qr("https://x.example.com", border=0)
    assert len(big) > len(small)


def test_render_fallback_when_qrcode_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qr_module, "_qrcode_available", lambda: False)
    out = qr_module.render_ascii_qr("https://example.com/x", border=1)
    assert "QR rendering unavailable" in out
    assert "https://example.com/x" in out
    assert "pip install qrcode" in out


def test_render_fallback_is_multiline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qr_module, "_qrcode_available", lambda: False)
    out = qr_module.render_ascii_qr("https://example.com", border=0)
    assert out.count("\n") >= 4


def test_render_uses_qrcode_when_available() -> None:
    pytest.importorskip("qrcode")
    out = qr_module.render_ascii_qr("https://example.com/")
    # Real QR modules render as the full block sentinel
    assert qr_module.QR_DARK in out


def test_matrix_to_text_handles_empty_matrix() -> None:
    assert qr_module._matrix_to_text([]) == ""


def test_matrix_to_text_renders_simple_row() -> None:
    text = qr_module._matrix_to_text([[True, False, True]])
    assert text == qr_module.QR_DARK + qr_module.QR_LIGHT + qr_module.QR_DARK


def test_qrcode_available_true_under_normal_runtime() -> None:
    # The dev env installs ``qrcode`` via the [gui] extra.
    pytest.importorskip("qrcode")
    assert qr_module._qrcode_available() is True
