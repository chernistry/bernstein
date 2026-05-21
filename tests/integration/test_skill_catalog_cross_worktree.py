"""Integration test for end-to-end skill catalog installs (issue #1796).

Covers:

- Round-trip install -> audit chain entry -> lockfile row -> CI gate accept.
- Two parallel worktrees launched from the same chain head observe
  identical lockfile digests.
- An upstream upgrade in wt-a produces a lineage receipt that wt-b can
  consult to decide deterministically between adopt and pin.
- The CI lineage gate rejects a PR whose lockfile sha is not present in
  the chain's known-good set.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from bernstein.core.lineage.gate import check_skill_lockfile
from bernstein.core.plugins_core.plugin_installer import PluginInstallResult
from bernstein.core.skills.catalog import (
    CATALOG_LOCK_FILENAME,
    RECEIPT_ADOPT,
    RECEIPT_INSTALL,
    RECEIPT_PIN,
    CatalogLockEntry,
    SkillCatalog,
    SkillCatalogAuditor,
    SkillCatalogEntry,
    SkillCatalogService,
    SkillCatalogServiceConfig,
    SkillSourceSpec,
    generate_signer_keypair,
    read_state,
    record_pin,
    sign_entry,
    upsert_catalog_install,
)
from bernstein.core.skills.catalog.signature import attach_signature
from bernstein.core.skills.lifecycle import compute_skill_digest


def _write_fixture_skill(root: Path, *, name: str, version: str) -> Path:
    """Materialise a SKILL.md tree mimicking an installable github fixture."""
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        textwrap.dedent(
            f"""
            ---
            name: {name}
            description: {name} catalog fixture at version {version}.
            ---

            Body for {name} v{version}.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return skill


def _make_installer(staging_factory: Callable[[Path], Path]) -> Callable[..., PluginInstallResult]:
    def _installer(source, install_dir):  # type: ignore[no-untyped-def]
        staged = staging_factory(install_dir)
        return PluginInstallResult(
            success=True,
            install_path=staged,
            source_kind=source.kind,
        )

    return _installer


def _catalog_with(entry: SkillCatalogEntry, pub: str) -> SkillCatalog:
    return SkillCatalog(
        version=1,
        generated_at="2026-05-21T00:00:00Z",
        entries=(entry,),
        signer_pubkey=pub,
    )


def _entry_for(*, version: str, digest: str) -> SkillCatalogEntry:
    return SkillCatalogEntry(
        id="example",
        name="example",
        version=version,
        description=f"Integration fixture v{version}.",
        source=SkillSourceSpec(kind="github", repo="acme/example", tag=f"v{version}"),
        content_digest=digest,
        signature=None,
        homepage="",
        tags=("integration",),
        verified=True,
    )


@pytest.fixture(autouse=True)
def isolate_audit_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BERNSTEIN_AUDIT_KEY_PATH", str(tmp_path / "audit.key"))


def test_install_then_lineage_gate_accepts(tmp_path: Path) -> None:
    """End-to-end: install -> chain entry -> lockfile row -> CI gate accept."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    priv, pub = generate_signer_keypair()
    # Build a staging tree and pre-compute its digest.
    expected_root = tmp_path / "_expected"
    expected_root.mkdir()
    _write_fixture_skill(expected_root, name="example", version="1.0.0")
    expected_digest = compute_skill_digest(expected_root / "example").digest

    entry = _entry_for(version="1.0.0", digest=expected_digest)
    signed = attach_signature(entry, sign_entry(entry, priv))
    catalog = _catalog_with(signed, pub)

    def stage(install_dir: Path) -> Path:
        _write_fixture_skill(install_dir, name="example", version="1.0.0")
        return install_dir / "example"

    service = SkillCatalogService(
        config=SkillCatalogServiceConfig(workdir=workdir),
        preloaded_catalog=catalog,
        auditor=SkillCatalogAuditor(audit_dir=audit_dir),
        plugin_installer=_make_installer(stage),
    )
    outcome = service.install("example")
    assert outcome.verified

    # Lockfile populated.
    lockfile = workdir / CATALOG_LOCK_FILENAME
    state = read_state(lockfile)
    assert len(state.catalog) == 1
    row = state.catalog[0]
    assert row.id == "example"
    assert row.chain_head == outcome.chain_head

    # CI gate accepts when the lockfile sha is in the known-good set.
    auditor = SkillCatalogAuditor(audit_dir=audit_dir)
    known = auditor.known_good_manifest_shas()
    assert row.manifest_sha256 in known
    result = check_skill_lockfile(lockfile, frozenset(known))
    assert result.ok, result.failures

    # CI gate rejects when known-good is empty (lockfile not anchored).
    rejection = check_skill_lockfile(lockfile, frozenset())
    assert not rejection.ok


def test_cross_worktree_consistent_install_yields_same_digest(tmp_path: Path) -> None:
    """Two worktrees installing from the same chain head see equal lockfiles."""
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir()
    wt_b.mkdir()
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    priv, pub = generate_signer_keypair()
    expected_root = tmp_path / "_expected"
    expected_root.mkdir()
    _write_fixture_skill(expected_root, name="example", version="1.0.0")
    expected_digest = compute_skill_digest(expected_root / "example").digest

    entry = _entry_for(version="1.0.0", digest=expected_digest)
    signed = attach_signature(entry, sign_entry(entry, priv))
    catalog = _catalog_with(signed, pub)

    def stage(install_dir: Path) -> Path:
        _write_fixture_skill(install_dir, name="example", version="1.0.0")
        return install_dir / "example"

    auditor = SkillCatalogAuditor(audit_dir=audit_dir)

    # Both worktrees use the same shared audit log (so chain heads agree).
    SkillCatalogService(
        config=SkillCatalogServiceConfig(workdir=wt_a),
        preloaded_catalog=catalog,
        auditor=auditor,
        plugin_installer=_make_installer(stage),
    ).install("example")

    SkillCatalogService(
        config=SkillCatalogServiceConfig(workdir=wt_b),
        preloaded_catalog=catalog,
        auditor=auditor,
        plugin_installer=_make_installer(stage),
    ).install("example")

    state_a = read_state(wt_a / CATALOG_LOCK_FILENAME)
    state_b = read_state(wt_b / CATALOG_LOCK_FILENAME)
    # The content_digest of each install is identical (same chain head).
    assert state_a.catalog[0].content_digest == state_b.catalog[0].content_digest
    assert state_a.catalog[0].manifest_sha256 == state_b.catalog[0].manifest_sha256


def test_upgrade_in_wt_a_produces_receipt_wt_b_can_pin(tmp_path: Path) -> None:
    """Upstream upgrade applied to wt-a's lockfile produces a lineage
    receipt that wt-b can use to pin deterministically."""
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir()
    wt_b.mkdir()

    initial = CatalogLockEntry(
        id="shared",
        name="shared",
        version="1.0.0",
        manifest_url="github://acme/shared@v1",
        manifest_sha256="1" * 64,
        content_digest="2" * 64,
        install_id="install-1",
        chain_head="1111" + "0" * 60,
        installed_at="2026-05-21T00:00:00Z",
    )
    upgraded = replace(
        initial,
        version="1.1.0",
        manifest_url="github://acme/shared@v1.1.0",
        manifest_sha256="3" * 64,
        install_id="install-2",
        chain_head="2222" + "0" * 60,
    )

    # wt-a installs then upgrades.
    upsert_catalog_install(wt_a / CATALOG_LOCK_FILENAME, initial, workdir=wt_a)
    upsert_catalog_install(wt_a / CATALOG_LOCK_FILENAME, upgraded, workdir=wt_a)
    state_a = read_state(wt_a / CATALOG_LOCK_FILENAME)
    actions = [r.action for r in state_a.receipts]
    assert RECEIPT_INSTALL in actions
    assert RECEIPT_ADOPT in actions

    # wt-b installs at the same chain head, then decides to pin.
    upsert_catalog_install(wt_b / CATALOG_LOCK_FILENAME, initial, workdir=wt_b)
    pin_state = record_pin(
        wt_b / CATALOG_LOCK_FILENAME,
        entry_id="shared",
        chain_head=initial.chain_head,
        manifest_sha256=initial.manifest_sha256,
        workdir=wt_b,
    )
    assert pin_state.receipts[-1].action == RECEIPT_PIN

    # CI gate over wt-a's lockfile rejects when only the older sha is anchored.
    res = check_skill_lockfile(wt_a / CATALOG_LOCK_FILENAME, frozenset({initial.manifest_sha256}))
    assert not res.ok
    # CI gate over wt-a's lockfile accepts when the upgraded sha is anchored.
    res_ok = check_skill_lockfile(
        wt_a / CATALOG_LOCK_FILENAME,
        frozenset({initial.manifest_sha256, upgraded.manifest_sha256}),
    )
    assert res_ok.ok
