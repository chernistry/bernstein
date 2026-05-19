"""Tests for the :func:`bernstein.core.security.redactor.mask` helper.

These are the regression tests for the credential-leak fixes introduced
to address Semgrep
``python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure``
alerts: every changed call site relies on ``mask`` returning ``"***"``
(or ``"***xxxx"`` with a short suffix) so the secret value never
reaches the log output.
"""

from __future__ import annotations

import logging

import pytest

from bernstein.core.security.redactor import mask


class TestMaskBasics:
    def test_none_renders_as_placeholder(self) -> None:
        assert mask(None) == "<none>"

    def test_empty_string_renders_as_placeholder(self) -> None:
        assert mask("") == "<empty>"

    def test_non_empty_string_collapses_to_stars(self) -> None:
        assert mask("super-secret-token") == "***"

    def test_secret_value_does_not_appear_in_result(self) -> None:
        secret = "ghp_1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        out = mask(secret)
        assert secret not in out
        assert out == "***"

    def test_int_and_other_scalars_are_stringified(self) -> None:
        # Defensive: callers sometimes pass non-string values; the
        # helper must still hide them rather than crashing.
        assert mask(12345) == "***"


class TestMaskKeepSuffix:
    def test_keep_zero_is_default(self) -> None:
        assert mask("abcdef") == "***"

    def test_keep_reveals_only_trailing_chars(self) -> None:
        assert mask("abcdef", keep=2) == "***ef"

    def test_keep_is_clamped_to_four(self) -> None:
        # Anything above 4 risks leaking short secrets; the helper
        # silently clamps so callers can't misconfigure.
        out = mask("abcdefgh", keep=99)
        assert out == "***efgh"

    def test_keep_larger_than_input_collapses_to_stars(self) -> None:
        # If the visible suffix would be the whole string, we degrade
        # to the safe default rather than emit the full value.
        assert mask("ab", keep=4) == "***"

    def test_negative_keep_is_treated_as_zero(self) -> None:
        assert mask("abcdef", keep=-3) == "***"


class TestMaskIntegratesWithLogging:
    """End-to-end: a real :mod:`logging` handler must never see the
    secret payload when ``mask`` is used at the call site.
    """

    @pytest.fixture
    def caplog_at_debug(self, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
        caplog.set_level(logging.DEBUG)
        return caplog

    def test_mask_keeps_secret_out_of_log_record(
        self,
        caplog_at_debug: pytest.LogCaptureFixture,
    ) -> None:
        log = logging.getLogger("test_mask_keeps_secret_out_of_log_record")
        secret = "sk-live-9f3a2b1c4d5e6f7a8b9c0d1e2f3a4b5c"

        log.info("token issued: %s", mask(secret))

        records = [r for r in caplog_at_debug.records if r.name == log.name]
        assert records, "expected the test logger to capture at least one record"
        rendered = records[-1].getMessage()
        assert "***" in rendered
        assert secret not in rendered

    def test_mask_with_keep_suffix_does_not_leak_full_secret(
        self,
        caplog_at_debug: pytest.LogCaptureFixture,
    ) -> None:
        log = logging.getLogger("test_mask_with_keep_suffix_does_not_leak_full_secret")
        secret = "AKIAEXAMPLE1234567890QWERTYUIOPASDF"

        log.info("api key issued: %s", mask(secret, keep=4))

        records = [r for r in caplog_at_debug.records if r.name == log.name]
        rendered = records[-1].getMessage()
        # Only the last 4 chars may surface; everything else is hidden.
        assert rendered.endswith(secret[-4:])
        assert secret not in rendered


class TestMaskIdempotent:
    def test_mask_is_idempotent_on_its_own_output(self) -> None:
        # Defensive: re-masking an already-masked value must be a no-op
        # so accidental double-wrapping does not corrupt audit trails.
        assert mask(mask("secret")) == "***"
        assert mask(mask("secret", keep=2)) == "***"
