"""Network reachability checks for provider endpoints.

Each known provider maps to a single hostname. The check opens a TCP
connection to port 443 with a 2-second timeout. DNS-resolution failures
are reported distinctly from connection failures so the operator can
tell a misconfigured resolver from a blocked egress.

When ``BERNSTEIN_OFFLINE=1`` is set every check returns ``status="skip"``
immediately - no DNS lookup, no socket, no telemetry.
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import TYPE_CHECKING

from bernstein.cli.doctor.report import DoctorResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


# Provider -> host. Keep ASCII-only; some operators feed these into other
# tooling that does not handle Unicode hostnames.
PROVIDER_HOSTS: dict[str, str] = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "google": "generativelanguage.googleapis.com",
    "openrouter": "openrouter.ai",
    "groq": "api.groq.com",
    "mistral": "api.mistral.ai",
    "deepseek": "api.deepseek.com",
}


OFFLINE_ENV_VAR = "BERNSTEIN_OFFLINE"

_DEFAULT_TIMEOUT_SECONDS = 2.0


def _is_offline() -> bool:
    return os.environ.get(OFFLINE_ENV_VAR, "").strip() == "1"


async def check_provider_reachability(
    provider: str,
    *,
    host: str | None = None,
    port: int = 443,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> DoctorResult:
    """Probe a provider's API hostname.

    Returns:
        DoctorResult with status:
          - ``skip`` if ``BERNSTEIN_OFFLINE=1`` or the provider is unknown,
          - ``ok`` if the TCP connection completed inside ``timeout``,
          - ``fail`` for DNS, connection-refused, blocked, or timed-out.
    """
    name = f"network:{provider}"
    if _is_offline():
        return DoctorResult(
            name=name,
            category="network",
            status="skip",
            detail=f"{OFFLINE_ENV_VAR}=1",
        )

    target_host = host or PROVIDER_HOSTS.get(provider)
    if target_host is None:
        return DoctorResult(
            name=name,
            category="network",
            status="skip",
            detail=f"unknown provider `{provider}`",
            remediation="Pass an explicit host or register the provider in PROVIDER_HOSTS",
        )

    try:
        reader_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, port),
            timeout=timeout,
        )
    except TimeoutError:
        return DoctorResult(
            name=name,
            category="network",
            status="fail",
            detail=f"connection to {target_host}:{port} timed out after {timeout:g}s",
            remediation="check network egress, proxy config, or set BERNSTEIN_OFFLINE=1",
        )
    except socket.gaierror as exc:
        return DoctorResult(
            name=name,
            category="network",
            status="fail",
            detail=f"DNS lookup failed for {target_host}: {exc}",
            remediation="check /etc/resolv.conf or your DNS resolver",
        )
    except OSError as exc:
        return DoctorResult(
            name=name,
            category="network",
            status="fail",
            detail=f"connection to {target_host}:{port} refused: {exc}",
            remediation="check network egress or proxy config",
        )

    _close(reader_writer)
    return DoctorResult(
        name=name,
        category="network",
        status="ok",
        detail=f"reachable: {target_host}:{port}",
    )


async def run_network_checks(
    provider_names: Iterable[str] | None = None,
    *,
    hosts: Mapping[str, str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[DoctorResult]:
    """Run reachability checks for every requested provider in parallel.

    When ``provider_names`` is ``None``, every entry of ``PROVIDER_HOSTS``
    is probed. When offline mode is active a single skip-row is emitted
    instead of one per provider to keep the report compact.
    """
    if _is_offline():
        return [
            DoctorResult(
                name="network:*",
                category="network",
                status="skip",
                detail=f"{OFFLINE_ENV_VAR}=1 - all network checks skipped",
            )
        ]

    table = dict(hosts) if hosts is not None else PROVIDER_HOSTS.copy()
    names = list(provider_names) if provider_names is not None else list(table.keys())
    if not names:
        return [
            DoctorResult(
                name="network:none",
                category="network",
                status="skip",
                detail="no providers configured",
            )
        ]

    coros = [check_provider_reachability(name, host=table.get(name), timeout=timeout) for name in names]
    return list(await asyncio.gather(*coros))


def _close(reader_writer: tuple[asyncio.StreamReader, asyncio.StreamWriter]) -> None:
    """Best-effort close of an open connection; never raise."""
    _, writer = reader_writer
    try:
        writer.close()
    except Exception:  # pragma: no cover - defensive
        return
