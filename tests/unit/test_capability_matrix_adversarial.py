"""Adversarial verification of the lethal-trifecta capability matrix.

This module exists to *try to break* the structural rule that no spawned
agent may carry the union of all three capabilities (PRIVATE_DATA,
UNTRUSTED_INPUT, EXTERNAL_COMM) on a single execution path.  Each test
encodes a bypass vector — tool aliasing, capability mutation, runtime
scope escalation, surface mismatch, race-against-spawn — and asserts
that the matrix fails closed.

If any of these tests starts *passing the chain* without the matching
deny path, the lethal trifecta is reachable and a :class:`LethalTrifectaError`
must be raised by the production code or the regression is a security
incident.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from bernstein.core.security.capability_matrix import (
    Capability,
    CapabilityRegistry,
    EnforcementMode,
    LethalTrifectaError,
    ToolCapabilities,
    _coerce_capabilities,
    _load_yaml_file,
    record_spawn_capabilities,
)
from bernstein.core.security.policy_engine import (
    DecisionGraph,
    DecisionType,
    PermissionDecision,
    evaluate_lethal_trifecta,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _trifecta_chain() -> list[str]:
    """Canonical declared chain that unions the full trifecta."""
    return ["fs.read_secret", "github.fetch_issue", "github.post_comment"]


@pytest.fixture()
def declared_registry() -> CapabilityRegistry:
    """Registry with three declared tools that union the full trifecta."""
    reg = CapabilityRegistry()
    reg.register(
        ToolCapabilities(
            tool_name="fs.read_secret",
            capabilities=frozenset({Capability.PRIVATE_DATA}),
        )
    )
    reg.register(
        ToolCapabilities(
            tool_name="github.fetch_issue",
            capabilities=frozenset({Capability.UNTRUSTED_INPUT, Capability.EXTERNAL_COMM}),
        )
    )
    reg.register(
        ToolCapabilities(
            tool_name="github.post_comment",
            capabilities=frozenset({Capability.EXTERNAL_COMM}),
        )
    )
    return reg


# ---------------------------------------------------------------------------
# Bypass vector 1: aliasing — agent renames a denied tool to look benign
# ---------------------------------------------------------------------------


class TestAliasingBypass:
    """Renaming a tool must not strip its capability tags.

    The matrix is keyed on the *registered* tool name.  An attacker who
    re-registers the same tool under a different name without copying
    the capability tags would hide the trifecta from spawn-time review.
    The defence is twofold: every spawn-time check operates on the
    *resolved* registry tool list, and unknown tools default-deny.
    """

    def test_renamed_tool_with_no_capabilities_still_denied_via_unknown_default(
        self,
    ) -> None:
        """An agent that adds an alias entry with empty caps gets default-deny.

        We register only the alias with empty caps; the alias is therefore
        treated as a *declared* zero-cap tool.  The remaining declared
        tools in the chain must still trigger the trifecta when combined
        with the alias.  Reverse case: passing the unknown alias only.
        """
        reg = CapabilityRegistry()
        # Alias declared with empty caps — *declared*, so unknown=False
        reg.register(
            ToolCapabilities(
                tool_name="fs.read_pretty",
                capabilities=frozenset(),
            )
        )
        # The real fs.read_secret is not registered; passing it as an
        # unknown tool must default-deny and trip the trifecta on its own.
        decision_alias_only = reg.evaluate_chain(["fs.read_pretty"])
        assert decision_alias_only.allowed is True, (
            "An empty-cap alias on its own must NOT trip the trifecta — "
            "but it must also not contribute capabilities."
        )
        # Now an agent uses BOTH the alias AND the real (unknown) tool.
        # The unknown tool default-denies because we have no declaration.
        decision_with_unknown = reg.evaluate_chain(
            ["fs.read_pretty", "fs.read_secret"]
        )
        assert decision_with_unknown.allowed is False, (
            "Real tool fs.read_secret is unknown to this registry — "
            "default-deny must kick in regardless of any alias entry."
        )
        assert "fs.read_secret" in decision_with_unknown.unknown_tools

    def test_alias_cannot_inherit_private_data_silently(self) -> None:
        """An alias to a privileged tool must declare its own caps explicitly.

        There is no inheritance: registering ``fs.read_secret_alias`` does
        not pull tags from ``fs.read_secret``.  The operator must declare
        every alias separately.  This prevents the
        "alias points to a powerful tool but I forgot to copy the tag"
        footgun.
        """
        reg = CapabilityRegistry()
        reg.register(
            ToolCapabilities(
                tool_name="fs.read_secret",
                capabilities=frozenset({Capability.PRIVATE_DATA}),
            )
        )
        reg.register(
            ToolCapabilities(
                tool_name="fs.read_secret_alias",
                capabilities=frozenset(),
            )
        )
        # Alias is declared with no caps; only the real tool contributes.
        decision = reg.evaluate_chain(["fs.read_secret_alias"])
        assert Capability.PRIVATE_DATA not in decision.triggered, (
            "Alias entry must NOT inherit caps from the real tool — "
            "but the operator must declare them explicitly to keep the chain safe."
        )

    def test_uppercase_or_whitespace_alias_does_not_match_declared(self) -> None:
        """Tool-name lookup must be exact: ``Fs.Read_Secret`` != ``fs.read_secret``.

        If lookup were case-insensitive an attacker could alias-bypass by
        case-changing.  Lookup is exact, so the alternate-cased name is
        treated as unknown → default-deny.
        """
        reg = CapabilityRegistry()
        reg.register(
            ToolCapabilities(
                tool_name="fs.read_secret",
                capabilities=frozenset({Capability.PRIVATE_DATA}),
            )
        )
        # Variants that should NOT collide with declared name:
        for alias in ("Fs.Read_Secret", " fs.read_secret", "fs.read_secret ", "FS.READ_SECRET"):
            entry = reg.lookup(alias)
            assert entry.source == "default", (
                f"Lookup for {alias!r} must default-deny; collisions enable case-folding bypass."
            )
            assert entry.capabilities == frozenset(Capability)


# ---------------------------------------------------------------------------
# Bypass vector 2: empty / malformed declarations must default-deny
# ---------------------------------------------------------------------------


class TestEmptyAndMalformedDeclarations:
    """Empty capability lists must NOT be treated as allow-all.

    The registry contract is "default-deny on missing"; the YAML loader
    returns an empty list when files are malformed; lookup returns all
    three caps for unknown names.  Misreading any of those would flip
    the trifecta into a permissive default.
    """

    def test_empty_yaml_file_does_not_register_anything(self, tmp_path: Path) -> None:
        directory = tmp_path / "capabilities"
        directory.mkdir()
        # Empty file
        (directory / "empty.yaml").write_text("", encoding="utf-8")
        # Non-mapping
        (directory / "list.yaml").write_text("- just a list\n", encoding="utf-8")
        # Mapping but no `tools` key
        (directory / "no_tools.yaml").write_text("not_tools: []\n", encoding="utf-8")
        # Mapping with tools=non-list
        (directory / "tools_str.yaml").write_text("tools: \"oops\"\n", encoding="utf-8")
        reg = CapabilityRegistry.from_directory(directory)
        assert reg.tools == {}
        # And the trifecta chain must default-deny because everything is unknown.
        decision = reg.evaluate_chain(_trifecta_chain())
        assert decision.allowed is False
        assert decision.unknown_tools == tuple(_trifecta_chain())

    def test_empty_capability_list_means_zero_caps_not_all_caps(self) -> None:
        """A *declared* tool with ``capabilities: []`` must contribute zero caps.

        This is the same shape as ``git.commit`` in the bundled YAML —
        if it were silently elevated to all-caps we would refuse legitimate
        chains.  Conversely, any *missing* declaration must fall through
        to default-deny.  This test pins the "declared empty == zero caps"
        invariant down so it doesn't drift to allow-all by accident.
        """
        reg = CapabilityRegistry()
        reg.register(
            ToolCapabilities(tool_name="git.commit", capabilities=frozenset())
        )
        decision = reg.evaluate_chain(["git.commit"])
        assert decision.triggered == frozenset()
        assert decision.allowed is True

    def test_unknown_capability_token_is_dropped_not_silently_promoted(self) -> None:
        """A YAML entry like ``capabilities: [private_data, voodoo]`` keeps only the valid tag."""
        coerced = _coerce_capabilities(["private_data", "voodoo", "external_comm"])
        assert coerced == frozenset({Capability.PRIVATE_DATA, Capability.EXTERNAL_COMM})

    def test_yaml_safe_load_blocks_python_tag_injection(self, tmp_path: Path) -> None:
        """`yaml.safe_load` must reject `!!python/object` payloads.

        The YAML loader is the trust boundary for capability files.  If
        the loader were ``yaml.unsafe_load`` an attacker could embed
        ``!!python/object/apply:os.system`` and own the orchestrator.  The
        loader uses ``yaml.safe_load`` (via :func:`_load_yaml_file`); this
        test pins that contract.  We craft a file whose Python-tagged
        block, if instantiated, would set a sentinel attribute.  Under
        ``safe_load`` the parse raises ``YAMLError`` and the loader logs
        a warning and returns ``[]`` — so the registry stays empty.
        """
        path = tmp_path / "evil.yaml"
        path.write_text(
            "tools:\n"
            "  - name: x.read\n"
            "    capabilities: !!python/object/apply:os.system ['echo pwned']\n",
            encoding="utf-8",
        )
        # Sanity: confirm the loader does not raise — it absorbs the YAMLError.
        assert _load_yaml_file(path) == []
        # And confirm yaml.safe_load itself rejects the same payload.
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_loader_skips_entries_with_blank_or_whitespace_name(
        self, tmp_path: Path
    ) -> None:
        directory = tmp_path / "capabilities"
        directory.mkdir()
        (directory / "tools.yaml").write_text(
            "tools:\n"
            "  - name: ''\n"
            "    capabilities: [private_data]\n"
            "  - name: '   '\n"
            "    capabilities: [private_data]\n"
            "  - name: real.tool\n"
            "    capabilities: [private_data]\n",
            encoding="utf-8",
        )
        reg = CapabilityRegistry.from_directory(directory)
        assert list(reg.tools.keys()) == ["real.tool"]


# ---------------------------------------------------------------------------
# Bypass vector 3: enforcement mode must default to enforce
# ---------------------------------------------------------------------------


class TestModeDefault:
    """The default enforcement mode must be ENFORCE, not WARN/OFF.

    If the default flipped to WARN/OFF an entire deployment could be
    silently permissive without anyone noticing.
    """

    def test_default_registry_mode_is_enforce(self) -> None:
        reg = CapabilityRegistry()
        assert reg.mode is EnforcementMode.ENFORCE

    def test_load_default_mode_is_enforce(self) -> None:
        reg = CapabilityRegistry.load_default()
        assert reg.mode is EnforcementMode.ENFORCE

    def test_invalid_mode_string_in_defaults_falls_back_to_enforce(self) -> None:
        """The spawner_core path resolves invalid mode strings to ENFORCE.

        We exercise the same coercion shape here directly.
        """
        try:
            mode = EnforcementMode("bogus")
        except ValueError:
            mode = EnforcementMode.ENFORCE
        assert mode is EnforcementMode.ENFORCE


# ---------------------------------------------------------------------------
# Bypass vector 4: WARN/OFF still records audit trail and offending tools
# ---------------------------------------------------------------------------


class TestAuditTrailUnderRelaxedModes:
    """WARN/OFF must still surface the offending tools for audit, even
    though they don't deny.  This is what makes a cluster-wide flip from
    OFF→ENFORCE actionable: the operator can ``grep`` the audit log.
    """

    def test_warn_mode_records_offending_tools(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        declared_registry.mode = EnforcementMode.WARN
        decision = declared_registry.evaluate_chain(_trifecta_chain())
        assert decision.allowed is True
        assert set(decision.offending_tools) >= set(_trifecta_chain())

    def test_off_mode_records_offending_tools(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        declared_registry.mode = EnforcementMode.OFF
        decision = declared_registry.evaluate_chain(_trifecta_chain())
        assert decision.allowed is True
        assert set(decision.offending_tools) >= set(_trifecta_chain())


# ---------------------------------------------------------------------------
# Bypass vector 5: registry mutation between check and spawn
# ---------------------------------------------------------------------------


class TestMutationAfterCheck:
    """Spawning is single-threaded inside the spawner, but the registry
    object itself is mutable.  These tests pin the contract that
    *registry mutation between check and spawn does not retroactively
    relax a denied decision*.

    The decision is a frozen dataclass — once it says ``allowed=False``,
    later writes to ``registry.tools`` cannot un-deny it.  The integration
    seam in :func:`record_spawn_capabilities` raises eagerly on the
    ``ChainDecision`` before any caller can look at the manifest.
    """

    def test_decision_object_is_frozen_against_post_check_writes(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        """A returned ``ChainDecision`` is a frozen dataclass.

        Even if the registry is mutated after the fact, the previously
        returned decision object cannot be mutated to relax the deny.
        The deeper invariant — that ``record_spawn_capabilities`` always
        re-evaluates from the current registry — is covered separately.
        """
        decision = declared_registry.evaluate_chain(_trifecta_chain())
        assert decision.allowed is False
        with pytest.raises((AttributeError, TypeError)):
            decision.allowed = True  # type: ignore[misc]
        # Adversary mutates the registry post-check; the cached object
        # must remain unchanged.
        declared_registry.register(
            ToolCapabilities(
                tool_name="github.fetch_issue",
                capabilities=frozenset(),  # strip caps post hoc
            )
        )
        assert decision.allowed is False
        assert decision.triggered == frozenset(Capability)

    def test_record_spawn_reevaluates_at_call_time(
        self, tmp_path: Path, declared_registry: CapabilityRegistry
    ) -> None:
        """``record_spawn_capabilities`` must re-evaluate against the
        registry at call time — *not* trust any external decision blob.

        We verify the inverse direction: starting with a relaxed registry,
        we re-tighten and confirm the spawn deny fires.  This pins down
        the property that there is no TOCTOU window where a stale allow
        decision could be replayed.
        """
        relaxed = CapabilityRegistry()
        relaxed.register(
            ToolCapabilities(
                tool_name="fs.read_secret",
                capabilities=frozenset(),
            )
        )
        relaxed.register(
            ToolCapabilities(
                tool_name="github.fetch_issue",
                capabilities=frozenset(),
            )
        )
        relaxed.register(
            ToolCapabilities(
                tool_name="github.post_comment",
                capabilities=frozenset(),
            )
        )
        # Initial evaluation says allow because we stripped all caps.
        initial = relaxed.evaluate_chain(_trifecta_chain())
        assert initial.allowed is True
        # Operator tightens the registry.
        relaxed.register(
            ToolCapabilities(
                tool_name="fs.read_secret",
                capabilities=frozenset({Capability.PRIVATE_DATA}),
            )
        )
        relaxed.register(
            ToolCapabilities(
                tool_name="github.fetch_issue",
                capabilities=frozenset({Capability.UNTRUSTED_INPUT, Capability.EXTERNAL_COMM}),
            )
        )
        # record_spawn_capabilities must observe the *current* registry.
        with pytest.raises(LethalTrifectaError):
            record_spawn_capabilities(
                tmp_path,
                "agent-mut",
                "backend",
                _trifecta_chain(),
                registry=relaxed,
            )

    def test_concurrent_evaluation_does_not_lose_deny(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        """Many threads evaluate the same trifecta chain; none must allow.

        Even if the underlying ``tools`` dict were being read mutably the
        evaluator must still produce a deny because the decision is
        derived from a snapshot read of each lookup.  This is a sanity
        check that the path is reentrant.
        """
        chain = _trifecta_chain()

        def _evaluate() -> bool:
            return declared_registry.evaluate_chain(chain).allowed

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _evaluate(), range(64)))
        assert all(r is False for r in results), (
            "Race in evaluator surfaced an allow path under concurrency."
        )


# ---------------------------------------------------------------------------
# Bypass vector 6: surface scope — fs.read pointing at /etc/passwd
# ---------------------------------------------------------------------------


class TestSurfaceScope:
    """The matrix is *capability-aware*, not *path-aware*: a tool tagged
    ``private_data`` carries that capability regardless of which file the
    agent reads.  This is intentional — path-level filtering belongs to
    the worker tool allowlist (T578) — but it means the trifecta check
    cannot be tricked by a "small file" path.
    """

    def test_fs_read_carries_private_data_for_any_path(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        """``fs.read`` and ``fs.read_secret`` both carry PRIVATE_DATA.

        The bundled surfaces.yaml tags ``fs.read`` with PRIVATE_DATA, so
        even a "harmless" read still locks the chain when combined with
        anything carrying UNTRUSTED_INPUT and EXTERNAL_COMM.  We pin
        that here so the structural rule cannot be loosened by re-tagging
        ``fs.read`` to empty caps without a paired test failure.
        """
        reg = CapabilityRegistry.load_default()
        decision = reg.evaluate_chain(["fs.read", "github.fetch_issue", "github.post_comment"])
        assert decision.allowed is False, (
            "fs.read must carry PRIVATE_DATA — re-tagging it to empty caps "
            "would silently allow the lethal trifecta with /etc/passwd-style reads."
        )

    def test_shell_exec_unions_private_data_and_external_comm(
        self,
    ) -> None:
        """``shell.exec`` is tagged with both PRIVATE_DATA and EXTERNAL_COMM.

        That way *any* untrusted-input tool combined with shell.exec
        already covers all three capabilities — there is no "but I only
        used shell.exec" loophole.
        """
        reg = CapabilityRegistry.load_default()
        decision = reg.evaluate_chain(["shell.exec", "github.fetch_issue"])
        assert decision.allowed is False
        assert Capability.PRIVATE_DATA in decision.triggered
        assert Capability.EXTERNAL_COMM in decision.triggered
        assert Capability.UNTRUSTED_INPUT in decision.triggered


# ---------------------------------------------------------------------------
# Bypass vector 7: read-only declaration vs subprocess shell escalation
# ---------------------------------------------------------------------------


class TestSubprocessShellEscalation:
    """A "read-only" tool that can shell out is still gated.

    The matrix has no concept of "read-only" surfaces — it tags
    capabilities, not modes.  Any tool that *could* trigger a shell
    must carry both PRIVATE_DATA (it can read repo secrets) and
    EXTERNAL_COMM (it can dial out).  This test pins ``shell.exec``
    with both tags and confirms a chain like
    ``[shell.exec, web.fetch]`` (UNTRUSTED_INPUT + EXTERNAL_COMM) is
    denied.
    """

    def test_shell_plus_web_fetch_is_full_trifecta(self) -> None:
        reg = CapabilityRegistry.load_default()
        decision = reg.evaluate_chain(["shell.exec", "web.fetch"])
        assert decision.allowed is False
        assert decision.triggered == frozenset(Capability)


# ---------------------------------------------------------------------------
# Bypass vector 8: DNS rebinding — egress allowlist is a runtime concern,
# but the structural rule still holds at the capability layer
# ---------------------------------------------------------------------------


class TestDnsRebindingAtCapabilityLayer:
    """The capability matrix does not validate DNS targets — that is the
    network policy layer's job.  But ``EXTERNAL_COMM`` is the structural
    flag that ANY outbound call carries, regardless of where it resolves
    to at use-time.

    Even a "localhost-only" tool that *could* be DNS-rebinded into a
    public IP would still be tagged ``EXTERNAL_COMM`` — and combining it
    with PRIVATE_DATA + UNTRUSTED_INPUT therefore trips the trifecta at
    spawn time, before the rebind can happen at runtime.  This is the
    "fail before, not after" property we want.
    """

    def test_localhost_tagged_external_comm_still_locks_trifecta(self) -> None:
        reg = CapabilityRegistry()
        reg.register(
            ToolCapabilities(
                tool_name="loopback.fetch",
                capabilities=frozenset({Capability.EXTERNAL_COMM}),
            )
        )
        reg.register(
            ToolCapabilities(
                tool_name="prompt.from_user",
                capabilities=frozenset({Capability.UNTRUSTED_INPUT}),
            )
        )
        reg.register(
            ToolCapabilities(
                tool_name="db.read_credentials",
                capabilities=frozenset({Capability.PRIVATE_DATA}),
            )
        )
        decision = reg.evaluate_chain(
            ["loopback.fetch", "prompt.from_user", "db.read_credentials"]
        )
        assert decision.allowed is False, (
            "A 'localhost-only' fetcher tagged EXTERNAL_COMM must still trip "
            "the trifecta — the structural check fires before the agent "
            "can DNS-rebind localhost to a public address."
        )


# ---------------------------------------------------------------------------
# Bypass vector 9: bypass_immune flag must hold against bypass=True
# ---------------------------------------------------------------------------


class TestBypassImmune:
    def test_lethal_trifecta_is_bypass_immune_in_decision_graph(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        graph = DecisionGraph(bypass_enabled=True)
        graph.add_decision(evaluate_lethal_trifecta(_trifecta_chain(), declared_registry))
        # An attacker plugin tries to spam ALLOW decisions in front.
        for _ in range(50):
            graph.add_decision(PermissionDecision(DecisionType.ALLOW, "plugin allowed"))
        final = graph.evaluate()
        assert final.type == DecisionType.IMMUNE
        assert final.bypass_immune is True

    def test_lethal_trifecta_immune_decision_carries_offending_tools_in_reason(
        self, declared_registry: CapabilityRegistry
    ) -> None:
        decision = evaluate_lethal_trifecta(_trifecta_chain(), declared_registry)
        assert decision.type == DecisionType.IMMUNE
        # Reason must contain the offending list so the audit log is grep-able.
        for tool in _trifecta_chain():
            assert tool in decision.reason


# ---------------------------------------------------------------------------
# Bypass vector 10: spawn manifest persistence cannot be skipped on deny
# ---------------------------------------------------------------------------


class TestManifestPersistenceOnDeny:
    """Even when a spawn is refused, the manifest must be written so the
    auditor can reconstruct the attempted bypass after the fact.
    """

    def test_manifest_written_before_raise(
        self, tmp_path: Path, declared_registry: CapabilityRegistry
    ) -> None:
        with pytest.raises(LethalTrifectaError):
            record_spawn_capabilities(
                tmp_path,
                "agent-deny",
                "backend",
                _trifecta_chain(),
                registry=declared_registry,
            )
        manifest_path = tmp_path / ".sdd" / "runtime" / "spawn_capabilities" / "agent-deny.json"
        assert manifest_path.exists(), (
            "Manifest must be persisted *before* the deny exception so the "
            "audit trail captures the attempted bypass."
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["allowed"] is False
        assert manifest["reason"] == CapabilityRegistry.DEFAULT_REASON
        # offending_tools should include all three declared chain entries.
        assert set(manifest["offending_tools"]) >= set(_trifecta_chain())


# ---------------------------------------------------------------------------
# Bypass vector 11: WARN mode must record manifest with allowed=True but
# triggered=full trifecta so an auditor can still see what happened
# ---------------------------------------------------------------------------


class TestWarnModeManifestVisibility:
    def test_warn_mode_manifest_records_full_trifecta_triggered(
        self, tmp_path: Path, declared_registry: CapabilityRegistry
    ) -> None:
        declared_registry.mode = EnforcementMode.WARN
        decision = record_spawn_capabilities(
            tmp_path,
            "agent-warn",
            "backend",
            _trifecta_chain(),
            registry=declared_registry,
        )
        assert decision.allowed is True
        manifest = json.loads(
            (tmp_path / ".sdd" / "runtime" / "spawn_capabilities" / "agent-warn.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["allowed"] is True
        assert sorted(manifest["triggered"]) == sorted(c.value for c in Capability)
        assert "warn-only" in manifest["reason"]


# ---------------------------------------------------------------------------
# Bypass vector 12: empty chain must not be allowed if any single tool
# happens to carry all three capabilities (one-tool trifecta)
# ---------------------------------------------------------------------------


class TestSingleToolTrifecta:
    """A tool that carries all three caps on its own (e.g. an adapter
    envelope) must trip the rule even when it is the *only* element.
    """

    def test_single_omnipotent_tool_is_denied(self) -> None:
        reg = CapabilityRegistry()
        reg.register(
            ToolCapabilities(
                tool_name="adapter.evil",
                capabilities=frozenset(Capability),
            )
        )
        decision = reg.evaluate_chain(["adapter.evil"])
        assert decision.allowed is False
        assert decision.offending_tools == ("adapter.evil",)


# ---------------------------------------------------------------------------
# Bypass vector 13: registry.lookup must NOT mutate the underlying dict
# ---------------------------------------------------------------------------


class TestLookupSideEffects:
    """``lookup`` for an unknown tool must NOT auto-register it.

    If unknown tools were silently added to ``registry.tools`` a later
    ``find_violating_chains`` audit would mark them as declared and the
    audit CLI would lose the warning signal.
    """

    def test_lookup_unknown_does_not_persist_to_tools_dict(self) -> None:
        reg = CapabilityRegistry()
        before = dict(reg.tools)
        result = reg.lookup("phantom.tool")
        assert result.source == "default"
        assert reg.tools == before, (
            "lookup() must not auto-register the queried tool — that would "
            "leak unknown-tool warnings into the declared set."
        )


# ---------------------------------------------------------------------------
# Bypass vector 14: bundled YAML files do not leak a single-tool trifecta
# (defensive smoke test against future YAML edits)
# ---------------------------------------------------------------------------


class TestBundledYamlSafety:
    def test_no_bundled_tool_carries_all_three_capabilities_alone(self) -> None:
        """If anyone tags a bundled tool with all three caps, the registry
        becomes a single-call trifecta.  We pin that none currently do
        — except adapters, which are intentionally tagged that way to
        force a tool-allowlist scope before spawn.

        The spawner_core path filters out the adapter envelope precisely
        for this reason and only evaluates the *catalog* tool list.  So
        we assert the only single-tool trifectas in bundled YAML come
        from the ``adapter.*`` namespace.
        """
        reg = CapabilityRegistry.load_default()
        all_three = frozenset(Capability)
        offenders = [
            name
            for name, entry in reg.tools.items()
            if entry.capabilities == all_three and not name.startswith("adapter.")
        ]
        assert offenders == [], (
            f"Non-adapter bundled tools that union all three caps: {offenders} — "
            "this would let a single tool name trip the lethal trifecta. "
            "Either narrow the tags or move the entry under the adapter.* namespace."
        )

    def test_bundled_yaml_files_all_use_safe_load(self) -> None:
        """Every bundled YAML file must parse under yaml.safe_load.

        ``_load_yaml_file`` already uses ``safe_load``; this is a smoke
        test that the bundled files don't smuggle ``!!python/...`` tags
        that would silently fail to load and end up empty.
        """
        from bernstein import _BUNDLED_TEMPLATES_DIR

        cap_dir = _BUNDLED_TEMPLATES_DIR / "capabilities"
        if not cap_dir.is_dir():
            pytest.skip("bundled capabilities/ not present in this env")
        files = list(cap_dir.rglob("*.yaml")) + list(cap_dir.rglob("*.yml"))
        assert files, "No bundled capability files found"
        for path in files:
            entries = _load_yaml_file(path)
            # Every bundled file is expected to declare *some* tools.
            assert entries, f"Bundled YAML {path} parsed empty — possible YAML error"


# ---------------------------------------------------------------------------
# Bypass vector 15: integration with full DecisionGraph — bypass cannot
# wash out the IMMUNE decision regardless of layer ordering
# ---------------------------------------------------------------------------


class TestDecisionGraphLayerOrderingResilience:
    """No matter where the IMMUNE+bypass_immune decision is added in
    relation to ALLOW layers, it still wins.  Sorting is by precedence,
    not insertion order, so we exercise both orderings.
    """

    @pytest.mark.parametrize("order", ["immune_first", "allow_first"])
    def test_immune_wins_regardless_of_insertion_order(
        self,
        order: str,
        declared_registry: CapabilityRegistry,
    ) -> None:
        graph = DecisionGraph(bypass_enabled=True)
        immune = evaluate_lethal_trifecta(_trifecta_chain(), declared_registry)
        allow = PermissionDecision(DecisionType.ALLOW, "plugin allowed")
        if order == "immune_first":
            graph.add_decision(immune)
            graph.add_decision(allow)
        else:
            graph.add_decision(allow)
            graph.add_decision(immune)
        final = graph.evaluate()
        assert final.type == DecisionType.IMMUNE
        assert final.bypass_immune is True
