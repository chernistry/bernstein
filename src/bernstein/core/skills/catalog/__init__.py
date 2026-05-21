"""Skill catalog with signed manifest installs.

Promotes the MCP catalog browse / list / search / install / upgrade /
info / status surface to skill packs. Source variants are resolved
through the existing plugin installer (:mod:`bernstein.core.plugins_core.plugin_installer`)
so the catalog never duplicates download or extraction logic.

Public re-exports stick to the operator-facing surface; submodules are
free to evolve their internals without changing the published API.
"""

from __future__ import annotations

from bernstein.core.skills.catalog.audit import (
    AUDIT_ACTOR,
    AUDIT_RESOURCE_TYPE,
    EVENT_FETCH,
    EVENT_INSTALL,
    EVENT_SYNC,
    EVENT_TYPES,
    EVENT_UNINSTALL,
    EVENT_UPGRADE,
    SkillCatalogAuditor,
    compute_manifest_sha256,
)
from bernstein.core.skills.catalog.fetcher import (
    DEFAULT_CHECK_INTERVAL_SECONDS,
    DEFAULT_REVALIDATE_SECONDS,
    DEFAULT_SKILLS_CATALOG_URL,
    DEFAULT_SKILLS_MIRROR_URL,
    TTL_ENV,
    FetchResult,
    HTTPResponse,
    HTTPTransport,
    SkillCatalogFetcher,
    default_cache_path,
    env_ttl_seconds,
)
from bernstein.core.skills.catalog.installer import (
    CatalogInstallError,
    CatalogInstallResult,
    install_catalog_entry,
    remove_catalog_install,
    resolve_plugin_source,
)
from bernstein.core.skills.catalog.lockfile import (
    CATALOG_LOCK_FILENAME,
    RECEIPT_ADOPT,
    RECEIPT_INSTALL,
    RECEIPT_PIN,
    CatalogLockEntry,
    CatalogLockState,
    LineageReceipt,
    detect_drift,
    fresh_install_id,
    read_state,
    record_pin,
    remove_catalog_entry,
    upsert_catalog_install,
    worktree_id_for,
)
from bernstein.core.skills.catalog.manifest import (
    SkillCatalog,
    SkillCatalogEntry,
    SkillCatalogValidationError,
    SkillSourceSpec,
    validate_catalog,
)
from bernstein.core.skills.catalog.service import (
    CatalogStatus,
    InstallOutcome,
    SkillCatalogError,
    SkillCatalogService,
    SkillCatalogServiceConfig,
    UpgradeOutcome,
)
from bernstein.core.skills.catalog.signature import (
    ManifestSignatureError,
    VerificationOutcome,
    canonical_entry_bytes,
    generate_signer_keypair,
    sign_entry,
    verify_entry,
)

__all__ = [
    "AUDIT_ACTOR",
    "AUDIT_RESOURCE_TYPE",
    "CATALOG_LOCK_FILENAME",
    "DEFAULT_CHECK_INTERVAL_SECONDS",
    "DEFAULT_REVALIDATE_SECONDS",
    "DEFAULT_SKILLS_CATALOG_URL",
    "DEFAULT_SKILLS_MIRROR_URL",
    "EVENT_FETCH",
    "EVENT_INSTALL",
    "EVENT_SYNC",
    "EVENT_TYPES",
    "EVENT_UNINSTALL",
    "EVENT_UPGRADE",
    "RECEIPT_ADOPT",
    "RECEIPT_INSTALL",
    "RECEIPT_PIN",
    "TTL_ENV",
    "CatalogInstallError",
    "CatalogInstallResult",
    "CatalogLockEntry",
    "CatalogLockState",
    "CatalogStatus",
    "FetchResult",
    "HTTPResponse",
    "HTTPTransport",
    "InstallOutcome",
    "LineageReceipt",
    "ManifestSignatureError",
    "SkillCatalog",
    "SkillCatalogAuditor",
    "SkillCatalogEntry",
    "SkillCatalogError",
    "SkillCatalogFetcher",
    "SkillCatalogService",
    "SkillCatalogServiceConfig",
    "SkillCatalogValidationError",
    "SkillSourceSpec",
    "UpgradeOutcome",
    "VerificationOutcome",
    "canonical_entry_bytes",
    "compute_manifest_sha256",
    "default_cache_path",
    "detect_drift",
    "env_ttl_seconds",
    "fresh_install_id",
    "generate_signer_keypair",
    "install_catalog_entry",
    "read_state",
    "record_pin",
    "remove_catalog_entry",
    "remove_catalog_install",
    "resolve_plugin_source",
    "sign_entry",
    "upsert_catalog_install",
    "validate_catalog",
    "verify_entry",
    "worktree_id_for",
]
