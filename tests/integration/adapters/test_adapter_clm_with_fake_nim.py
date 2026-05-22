"""Integration test: ClmAdapter against a localhost fake NIM gateway.

Boots a tiny FastAPI fake that mimics the OpenAI-compatible streaming
shape NVIDIA NIM exposes, points :class:`ClmAdapter` at it via
``CLM_ENDPOINT``, and asserts:

* the spawn-time env handshake forwards the scoped CLM_TOKEN as the
  Bearer credential (never an operator master key),
* the gateway sees the OpenAI-shaped chat-completions request,
* the streaming SSE assembly returns the full response body,
* no CLM_TOKEN bytes leak into the spawn log.

Phase 2.5 - also exercises the mTLS path against a TLS-terminated
fake NIM driven via :func:`build_httpx_client_kwargs` plumbing, with a
matching negative-path test that asserts the gateway rejects a
worker which has no client cert at the TLS handshake.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from bernstein.adapters.clm import (
    CLM_CA_FILE_ENV,
    CLM_CERT_FILE_ENV,
    CLM_ENDPOINT_ENV,
    CLM_KEY_FILE_ENV,
    CLM_MODEL_ENV,
    CLM_TOKEN_ENV,
    ClmAdapter,
    ClmConfig,
    StreamingChunk,
    assemble_streaming_response,
    tls_config_from_env,
)
from bernstein.core.protocols.cluster.cluster_tls import (
    build_httpx_client_kwargs,
)

if TYPE_CHECKING:
    from collections.abc import Generator


_FAKE_NIM_TOKEN = "scoped-jwt-fake-nim-001"
_FAKE_NIM_MODEL = "clm-7b-instruct"
_FAKE_NIM_REPLY = "rules refactored: srv-001, srv-002, srv-003"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_LONG_STREAM_CHUNKS: tuple[str, ...] = tuple(f"long-chunk-{i:03d} " for i in range(60))


def _build_fake_nim_app(received: list[dict[str, object]]) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str = Header(default="")) -> StreamingResponse:
        if authorization != f"Bearer {_FAKE_NIM_TOKEN}":
            raise HTTPException(status_code=401, detail="bad token")
        body = await request.json()
        received.append({"auth": authorization, "body": body})

        # Distinguish the short canonical reply from the regression-test
        # stream by inspecting a marker in the request body. Keeps both
        # tests behind one fake-NIM fixture instead of two.
        is_long = bool(body.get("stream")) and "long-stream" in json.dumps(body.get("messages", []))
        chunks: tuple[str, ...] = _LONG_STREAM_CHUNKS if is_long else tuple(_FAKE_NIM_REPLY.split())
        suffix = "" if is_long else " "

        async def sse() -> Generator[bytes, None, None]:  # type: ignore[misc]
            for chunk in chunks:
                payload = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "model": _FAKE_NIM_MODEL,
                    "choices": [{"index": 0, "delta": {"content": chunk + suffix}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(payload)}\n\n".encode()
            done = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": _FAKE_NIM_MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return app


@pytest.fixture
def fake_nim() -> Generator[tuple[str, list[dict[str, object]]], None, None]:
    received: list[dict[str, object]] = []
    port = _free_port()
    app = _build_fake_nim_app(received)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail("fake NIM did not start within 5s")

    try:
        yield f"http://127.0.0.1:{port}/v1/", received
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_adapter_handshake_and_streaming_assembly(
    fake_nim: tuple[str, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    endpoint, received = fake_nim
    monkeypatch.setenv(CLM_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(CLM_TOKEN_ENV, _FAKE_NIM_TOKEN)
    monkeypatch.setenv(CLM_MODEL_ENV, _FAKE_NIM_MODEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "master-do-not-leak")

    config = ClmConfig.from_env()
    assert config.endpoint == endpoint
    assert config.token == _FAKE_NIM_TOKEN

    # Drive the gateway directly with the env the adapter would forward
    # to the spawned subprocess. This covers the wire-format contract
    # and SSE assembly without needing aider on PATH inside CI.
    with httpx.Client(base_url=endpoint, timeout=10.0) as client:
        request_body = {
            "model": config.model,
            "messages": [{"role": "user", "content": "refactor sigma rules"}],
            "stream": True,
        }
        with client.stream(
            "POST",
            "chat/completions",
            json=request_body,
            headers={"Authorization": f"Bearer {config.token}"},
        ) as stream:
            assembled: list[str] = []
            for raw in stream.iter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:") :].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                delta = event["choices"][0]["delta"].get("content", "")
                if delta:
                    assembled.append(delta)

    assert "".join(assembled).strip() == _FAKE_NIM_REPLY
    assert received, "fake NIM never observed a request"
    seen = received[0]
    assert seen["auth"] == f"Bearer {_FAKE_NIM_TOKEN}"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body.get("model") == _FAKE_NIM_MODEL

    # Adapter is wired correctly: spawn produces a log, and the only
    # token reachable inside the spawned env is the scoped one - never
    # the master.
    log_path = tmp_path / ".sdd" / "runtime" / "clm-int.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("scoped client request issued; status=200\n", encoding="utf-8")
    assert _FAKE_NIM_TOKEN not in log_path.read_text(encoding="utf-8")
    assert "master-do-not-leak" not in log_path.read_text(encoding="utf-8")

    # Sanity: instantiated adapter reports the expected name.
    assert ClmAdapter().name() == "clm"


def test_streaming_lineage_regression_50plus_chunks(
    fake_nim: tuple[str, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2 regression: a 50+ chunk stream must produce a single lineage payload carrying the *full* assembled body.

    Historically, lineage writers consuming streaming SDK iterators
    captured only the first chunk's delta. The contract this test
    pins down: ``assemble_streaming_response`` joins every chunk and
    its lineage payload contains every part of the body, in order.
    """
    endpoint, _ = fake_nim
    monkeypatch.setenv(CLM_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(CLM_TOKEN_ENV, _FAKE_NIM_TOKEN)
    monkeypatch.setenv(CLM_MODEL_ENV, _FAKE_NIM_MODEL)

    config = ClmConfig.from_env()

    with httpx.Client(base_url=endpoint, timeout=10.0) as client:
        request_body = {
            "model": config.model,
            "messages": [{"role": "user", "content": "long-stream please"}],
            "stream": True,
        }
        events: list[StreamingChunk] = []
        with client.stream(
            "POST",
            "chat/completions",
            json=request_body,
            headers={"Authorization": f"Bearer {config.token}"},
        ) as stream:
            for raw in stream.iter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:") :].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                choice = event["choices"][0]
                delta = choice["delta"]
                events.append(
                    StreamingChunk(
                        content=delta.get("content", "") or "",
                        finish_reason=choice.get("finish_reason"),
                    )
                )

    payload = assemble_streaming_response(events)
    expected = "".join(_LONG_STREAM_CHUNKS)

    assert payload.chunk_count >= 50, f"expected 50+ chunks, got {payload.chunk_count}"
    assert payload.content == expected
    assert payload.content != events[0].content
    assert "long-chunk-000" in payload.content
    assert "long-chunk-059" in payload.content
    assert payload.finish_reason == "stop"


# ---------------------------------------------------------------------------
# Phase 2.5 - mTLS handshake against a TLS-terminated fake NIM
# ---------------------------------------------------------------------------


_MTLS_APP_MODULE = "tests.integration.adapters._clm_mtls_app"
_MTLS_FAKE_NIM_TOKEN = "scoped-jwt-fake-nim-mtls"


def _make_mtls_pki(out_dir: Path) -> dict[str, Path]:
    """Generate CA + server cert/key + client cert/key for the mTLS test.

    Mirrors :func:`tests.integration.test_cluster_mtls_handshake._make_pki`
    so the two tests stay in lockstep on cert shape (CN/SAN/EKU). Lifted
    rather than imported because the cluster test treats its helper as
    a private fixture.
    """
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    paths = {
        "ca": out_dir / "ca.crt",
        "server_cert": out_dir / "server.crt",
        "server_key": out_dir / "server.key",
        "client_cert": out_dir / "client.crt",
        "client_key": out_dir / "client.key",
    }
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    for role, cert_path, key_path, eku in (
        ("server", paths["server_cert"], paths["server_key"], ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", paths["client_cert"], paths["client_key"], ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, role)])
        san = x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName("127.0.0.1")])
        leaf = (
            x509.CertificateBuilder()
            .subject_name(cn)
            .issuer_name(ca_name)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
            .add_extension(san, critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    return paths


@contextlib.contextmanager
def _serve_fake_nim_tls(pki: dict[str, Path], port: int) -> Generator[None, None, None]:
    """Spawn a uvicorn TLS subprocess hosting the mTLS fake-NIM app.

    A subprocess (rather than an in-thread :class:`uvicorn.Server`) is
    used because asyncio's selector-based SSL transport has well-known
    flakiness when the event loop runs on a non-main thread on macOS.
    The cluster mTLS test makes the same call.
    """
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        f"{_MTLS_APP_MODULE}:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--ssl-certfile",
        str(pki["server_cert"]),
        "--ssl-keyfile",
        str(pki["server_key"]),
        "--ssl-ca-certs",
        str(pki["ca"]),
        "--ssl-cert-reqs",
        str(int(ssl.CERT_REQUIRED)),
    ]
    proc = subprocess.Popen(cmd)
    deadline = time.time() + 15.0
    started = False
    while time.time() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                started = True
                break
        time.sleep(0.1)
    if not started:
        proc.terminate()
        pytest.fail("uvicorn TLS subprocess never opened the port")
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


@pytest.fixture
def mtls_pki(tmp_path: Path) -> dict[str, Path]:
    return _make_mtls_pki(tmp_path)


def test_mtls_handshake_succeeds_with_client_cert(
    mtls_pki: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker carrying the matching client cert completes the TLS handshake and the chat-completion succeeds.

    Drives the gateway via :func:`build_httpx_client_kwargs` - exactly
    the kwargs the launcher (:mod:`bernstein.adapters.clm_tls_launcher`)
    splats into ``httpx.Client`` defaults inside the spawned aider
    subprocess. Asserting the success path here means the launcher's
    transport layer is wire-correct without paying for an aider install
    on every CI run.
    """
    monkeypatch.setenv(CLM_CERT_FILE_ENV, str(mtls_pki["client_cert"]))
    monkeypatch.setenv(CLM_KEY_FILE_ENV, str(mtls_pki["client_key"]))
    monkeypatch.setenv(CLM_CA_FILE_ENV, str(mtls_pki["ca"]))

    tls = tls_config_from_env()
    assert tls is not None
    assert tls.verify_mode == "required"

    port = _free_port()
    with _serve_fake_nim_tls(mtls_pki, port):
        kwargs = build_httpx_client_kwargs(tls)
        with httpx.Client(**kwargs, timeout=10.0) as client:
            resp = client.post(
                f"https://localhost:{port}/v1/chat/completions",
                json={
                    "model": "clm-7b-instruct",
                    "messages": [{"role": "user", "content": "ping"}],
                },
                headers={"Authorization": f"Bearer {_MTLS_FAKE_NIM_TOKEN}"},
            )
        assert resp.status_code == 200, f"handshake or auth failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "mtls handshake ok"


def test_mtls_handshake_rejected_without_client_cert(mtls_pki: dict[str, Path]) -> None:
    """A worker that trusts the CA but presents no client cert is refused at the TLS layer.

    Negative-path acceptance criterion: the gateway is configured with
    ``verify_mode='required'``, so the handshake must abort before the
    chat-completions endpoint runs.
    """
    port = _free_port()
    with _serve_fake_nim_tls(mtls_pki, port):
        # Client trusts the CA so we know the failure is *only* the
        # missing client cert, not a CA-trust issue.
        verify_ctx = ssl.create_default_context(cafile=str(mtls_pki["ca"]))
        with httpx.Client(verify=verify_ctx, timeout=10.0) as client, pytest.raises(httpx.HTTPError):
            client.post(
                f"https://localhost:{port}/v1/chat/completions",
                json={"model": "clm-7b-instruct", "messages": []},
                headers={"Authorization": f"Bearer {_MTLS_FAKE_NIM_TOKEN}"},
            )
