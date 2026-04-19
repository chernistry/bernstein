"""WorkspaceManifest — declarative description of a sandbox workspace.

The manifest is the value object backends consume via
:meth:`SandboxBackend.create`. Phase 1 (oai-002) covered the minimum
surface every backend needs to materialise a workable checkout:
workspace root path, optional git clone source, byte-injected files,
and environment variables.

oai-003 extends the manifest with cloud-mount entries that describe
how spawned agents should see their artifact storage. When a non-local
sandbox backend is selected together with a remote
:class:`~bernstein.core.storage.sink.ArtifactSink`, the sandbox
translates each mount into the provider-specific filesystem binding
(``rclone mount`` inside the container for S3, ``gcsfuse`` for GCS,
etc.) so agent writes to the mount path stream directly to the
orchestrator's artifact store. The local-worktree backend ignores the
mount entries because everything already lives on the host filesystem.

A manifest is frozen once passed to :meth:`SandboxBackend.create`: the
dataclasses are immutable and any nested tuple is read-only. Backends
treat it as a pure value object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class GitRepoEntry:
    """A local git repository to clone into the sandbox root.

    Attributes:
        src_path: Local filesystem path on the orchestrator where the
            repo lives. Backends either push the branch to the sandbox
            (Docker volume mount, E2B upload) or clone the remote when
            the sandbox has network access.
        branch: Branch to check out inside the sandbox.
        sparse_paths: Optional tuple of paths to include via
            ``git sparse-checkout``. Empty tuple means full checkout.
    """

    src_path: str
    branch: str
    sparse_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileEntry:
    """A byte-injected file to place inside the sandbox root.

    Useful for seeding small config files (``.env``, ``bernstein.yaml``,
    ``.claude/settings.json``) without having them tracked in git.

    Attributes:
        path: Path inside the sandbox, relative to
            :attr:`WorkspaceManifest.root`.
        content: Raw bytes to write at :attr:`path`.
        mode: POSIX file mode. Best-effort on Windows.
    """

    path: str
    content: bytes
    mode: int = 0o644


@dataclass(frozen=True)
class S3Mount:
    """An S3 artifact mount to bind into the sandbox (oai-003).

    Cloud sandbox backends translate this into a ``rclone mount`` (or
    equivalent) so processes inside the sandbox see the bucket as a
    regular filesystem path. Worktree backends ignore the mount.

    Attributes:
        bucket: Target S3 bucket.
        prefix: Optional object-store prefix. Empty by default.
        mount_path: Path inside the sandbox where the bucket should be
            visible (e.g. ``/workspace/.sdd``).
        region: Optional AWS region override.
        endpoint_url: Optional endpoint (used by LocalStack, R2, MinIO).
        credentials_env: Tuple of env-var names the sandbox must forward
            to the mount helper (e.g. ``("AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY")``). The orchestrator strips these
            from the spawned agent's environment — only the mount tool
            receives them.
        read_only: When True, the mount is bound ``ro``. Useful for
            read-only artifact inspection jobs.
    """

    bucket: str
    prefix: str
    mount_path: str
    region: str | None = None
    endpoint_url: str | None = None
    credentials_env: tuple[str, ...] = ()
    read_only: bool = False


@dataclass(frozen=True)
class GCSMount:
    """A Google Cloud Storage artifact mount (oai-003).

    Translated by cloud sandbox backends into ``gcsfuse``.

    Attributes:
        bucket: GCS bucket to mount.
        prefix: Optional object-store prefix.
        mount_path: Path inside the sandbox.
        project: Optional project override.
        credentials_env: Env vars forwarded to the mount helper.
        read_only: Mount read-only when ``True``.
    """

    bucket: str
    prefix: str
    mount_path: str
    project: str | None = None
    credentials_env: tuple[str, ...] = ()
    read_only: bool = False


@dataclass(frozen=True)
class AzureBlobMount:
    """An Azure Blob Storage artifact mount (oai-003).

    Translated by cloud sandbox backends into ``blobfuse2``.

    Attributes:
        container: Target blob container.
        prefix: Optional prefix.
        mount_path: Path inside the sandbox.
        account_name: Storage account name.
        credentials_env: Env vars forwarded to the mount helper.
        read_only: Mount read-only when ``True``.
    """

    container: str
    prefix: str
    mount_path: str
    account_name: str | None = None
    credentials_env: tuple[str, ...] = ()
    read_only: bool = False


@dataclass(frozen=True)
class R2Mount:
    """A Cloudflare R2 artifact mount (oai-003).

    R2 is S3-compatible so cloud sandbox backends reuse the same
    ``rclone mount`` path as :class:`S3Mount`, only with the R2
    endpoint derived from *account_id*.

    Attributes:
        bucket: R2 bucket to mount.
        prefix: Optional prefix.
        mount_path: Path inside the sandbox.
        account_id: R2 account ID (determines the endpoint URL).
        credentials_env: Env vars forwarded to the mount helper.
        read_only: Mount read-only when ``True``.
    """

    bucket: str
    prefix: str
    mount_path: str
    account_id: str
    credentials_env: tuple[str, ...] = ()
    read_only: bool = False


#: Union type covering every provider-specific cloud mount entry.
ArtifactMount = S3Mount | GCSMount | AzureBlobMount | R2Mount


@dataclass(frozen=True)
class WorkspaceManifest:
    """Declarative description of a sandbox workspace.

    Backends consume a manifest in :meth:`SandboxBackend.create`.

    Attributes:
        root: Absolute path inside the sandbox where the workspace
            should live. ``/workspace`` is the convention for Docker
            and cloud backends; the worktree backend maps this to the
            host-side worktree directory.
        repo: Optional :class:`GitRepoEntry` to seed the sandbox with
            a git checkout. ``None`` leaves :attr:`root` empty (useful
            for untrusted-code sandboxes that should never see the
            parent repo).
        files: Additional byte-injected files.
        env: Environment variables set for every
            :meth:`SandboxSession.exec` invocation. Callers can still
            override per call via ``env=``.
        timeout_seconds: Default wall-clock timeout for
            :meth:`SandboxSession.exec` when the caller doesn't pass
            one explicitly. Not a hard session lifetime cap — individual
            backends may still honour longer sessions.
        artifact_mounts: Tuple of provider-specific cloud mounts
            (oai-003). Cloud sandbox backends translate these into
            provider-native filesystem bindings so agent writes stream
            directly to the orchestrator's artifact sink. Worktree
            backends ignore the field.
    """

    root: str = "/workspace"
    repo: GitRepoEntry | None = None
    files: tuple[FileEntry, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    timeout_seconds: int = 1800
    artifact_mounts: tuple[ArtifactMount, ...] = ()


__all__ = [
    "ArtifactMount",
    "AzureBlobMount",
    "FileEntry",
    "GCSMount",
    "GitRepoEntry",
    "R2Mount",
    "S3Mount",
    "WorkspaceManifest",
]
