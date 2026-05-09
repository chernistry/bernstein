"""Unit tests for the custom-guardrail example plugin."""

from __future__ import annotations

import pytest
from custom_guardrail import NoSecretsGuardrail, NoSecretsGuardrailPlugin

from bernstein.core.security.guardrail_pipeline import GuardrailPipeline


class TestNoSecretsGuardrail:
    """Pattern matching contract."""

    @pytest.mark.parametrize(
        "prompt",
        [
            "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "sk-aaaaaaaaaaaaaaaaaaaaaaaa",
            "xoxb-1234567890-abcdefghij",
            "GITHUB_TOKEN=ghs_aaaaaaaaaaaaaaaaaaaaaa",
        ],
    )
    def test_secret_shaped_input_is_rejected(self, prompt: str) -> None:
        """Each canonical secret family must trip the guardrail."""
        guardrail = NoSecretsGuardrail()
        result = guardrail.check_input(prompt, {})
        assert not result.passed
        assert result.violations
        # The violation message must NOT echo the matched secret value.
        for violation in result.violations:
            assert prompt not in violation, "guardrail leaked the matched secret in its violation message"

    def test_benign_prompt_passes(self) -> None:
        """A prompt with no secret-shaped tokens must pass cleanly."""
        guardrail = NoSecretsGuardrail()
        result = guardrail.check_input("please refactor the user model", {})
        assert result.passed
        assert result.violations == []

    def test_extra_patterns_extend_defaults(self) -> None:
        """Operator-supplied patterns are appended, not replaced."""
        guardrail = NoSecretsGuardrail(extra_patterns=(r"INTERNAL_KEY=[a-z0-9]{8,}",))
        # Custom pattern fires
        result = guardrail.check_input("INTERNAL_KEY=abcd1234", {})
        assert not result.passed
        # Default pattern still fires
        result = guardrail.check_input("AKIAIOSFODNN7EXAMPLE", {})
        assert not result.passed

    def test_output_check_is_a_no_op(self) -> None:
        """The plugin only owns the input side of the pipeline."""
        guardrail = NoSecretsGuardrail()
        result = guardrail.check_output("this contains AKIAIOSFODNN7EXAMPLE", {})
        # Output side intentionally passes — operators rely on the
        # in-tree SecretLeakGuardrail for output checking.
        assert result.passed


class TestPluginIntegration:
    """The plugin's class wires the guardrail into a pipeline."""

    def test_build_guardrail_returns_fresh_instance(self) -> None:
        plugin = NoSecretsGuardrailPlugin()
        guardrail = plugin.build_guardrail()
        assert isinstance(guardrail, NoSecretsGuardrail)
        # Guardrails must not share mutable state across instances.
        guardrail2 = plugin.build_guardrail()
        assert guardrail is not guardrail2

    def test_pipeline_pickup_via_configure_hook(self) -> None:
        """The hookimpl appends the guardrail to a supplied pipeline."""
        pipeline = GuardrailPipeline()
        assert pipeline.guardrails == []
        plugin = NoSecretsGuardrailPlugin()
        plugin.configure_guardrails(pipeline=pipeline)
        # Pipeline now has exactly one guardrail with the plugin's name.
        assert len(pipeline.guardrails) == 1
        assert pipeline.guardrails[0].name == NoSecretsGuardrail.name

        # End-to-end: a leaky prompt fails the pipeline.
        results = pipeline.check_input("ghp_" + "a" * 36, {})
        assert any(not r.passed for r in results)
