"""``bernstein cluster``: cluster lifecycle helpers (mTLS bootstrap, etc.)."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import click
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bernstein.cli.helpers import console

DEFAULT_CLUSTER_DIR = Path.home() / ".bernstein" / "cluster"
KEY_SIZE = 4096
SERIAL_BITS = 64
CA_VALID_DAYS = 3650
LEAF_VALID_DAYS = 825


@click.group("cluster")
def cluster_group() -> None:
    """Cluster lifecycle helpers.

    \b
      bernstein cluster bootstrap-ca   # generate self-signed CA + server/node certs
    """


def _build_name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Bernstein Cluster"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def _write_pem(path: Path, data: bytes, *, mode: int) -> None:
    path.write_bytes(data)
    os.chmod(path, mode)


def _build_ca(out_dir: Path) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _generate_key()
    name = _build_name("Bernstein Self-Signed CA")
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_pem(out_dir / "ca.crt", cert.public_bytes(serialization.Encoding.PEM), mode=0o644)
    _write_pem(
        out_dir / "ca.key",
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        mode=0o600,
    )
    return cert, key


def _issue_leaf(
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    out_dir: Path,
    *,
    name_prefix: str,
    common_name: str,
    san_dns: list[str],
    is_server: bool,
) -> None:
    key = _generate_key()
    now = datetime.datetime.now(datetime.UTC)
    eku = (
        x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH, x509.ExtendedKeyUsageOID.CLIENT_AUTH])
        if is_server
        else x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH])
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(_build_name(common_name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=LEAF_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(eku, critical=False)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(d) for d in san_dns]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_pem(out_dir / f"{name_prefix}.crt", cert.public_bytes(serialization.Encoding.PEM), mode=0o644)
    _write_pem(
        out_dir / f"{name_prefix}.key",
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        mode=0o600,
    )


@cluster_group.command("bootstrap-ca")
@click.option(
    "--out-dir",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory to write cert artifacts. Default: ~/.bernstein/cluster/",
)
@click.option(
    "--server-cn",
    "server_cn",
    default="bernstein-central",
    help="Common name + primary DNS SAN for the server cert.",
)
@click.option(
    "--node-cn",
    "node_cn",
    default="bernstein-node",
    help="Common name for the node (worker) cert template.",
)
@click.option(
    "--server-san",
    "server_san",
    multiple=True,
    help="Additional DNS SANs for the server cert (repeat for multiple).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing artifacts in --out-dir.",
)
def bootstrap_ca(
    out_dir: Path | None,
    server_cn: str,
    node_cn: str,
    server_san: tuple[str, ...],
    force: bool,
) -> None:
    """Generate a self-signed CA, server cert, and node cert template.

    Writes ``ca.crt``, ``ca.key``, ``server.crt``, ``server.key``,
    ``node.crt``, and ``node.key`` to ``--out-dir`` (default
    ``~/.bernstein/cluster/``). Private keys are written 0600.

    This is a self-hosted, self-signed CA — appropriate for internal
    clusters on infrastructure you control. For production deployments
    use your existing PKI (step-ca, cert-manager, Vault, etc.).
    """
    target = out_dir or DEFAULT_CLUSTER_DIR
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    existing = [p for p in ("ca.crt", "ca.key", "server.crt", "node.crt") if (target / p).exists()]
    if existing and not force:
        console.print(
            f"[red]Refusing to overwrite existing artifacts in {target}: {existing}.[/red] "
            "Re-run with --force to replace them."
        )
        raise SystemExit(1)

    ca_cert, ca_key = _build_ca(target)
    sans = [server_cn, *server_san, "localhost"]
    _issue_leaf(
        ca_cert,
        ca_key,
        target,
        name_prefix="server",
        common_name=server_cn,
        san_dns=list(dict.fromkeys(sans)),
        is_server=True,
    )
    _issue_leaf(
        ca_cert,
        ca_key,
        target,
        name_prefix="node",
        common_name=node_cn,
        san_dns=[node_cn],
        is_server=False,
    )

    console.print(f"[green]Wrote cluster mTLS artifacts to[/green] {target}")
    console.print(
        "\n[bold]Next steps:[/bold]\n"
        f"  1. On the central node, point ClusterConfig.tls at {target}/server.crt + server.key + ca.crt.\n"
        f"  2. Distribute {target}/ca.crt + node.crt + node.key to each worker (out-of-band, e.g. scp).\n"
        f"  3. Set ClusterConfig.tls.verify_mode='required' on both sides.\n"
        f"  4. Restart the central server and workers.\n\n"
        "[yellow]Warning:[/yellow] this is a self-signed CA suitable for internal clusters only. "
        "For production, use your own CA / step-ca / cert-manager."
    )
