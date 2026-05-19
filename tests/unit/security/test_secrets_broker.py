"""Unit tests for :mod:`bernstein.core.security.secrets_broker`.

Backend implementations that wrap optional cloud SDKs (boto3,
google-cloud-secret-manager) or platform CLIs (``security`` on macOS,
``keyring`` on Linux) are exercised with stubs/mocks so the suite runs
without those dependencies installed.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

from bernstein.core.security.secrets_broker import (
    AuditEvent,
    AwsSecretsManagerBackend,
    BrokerConfig,
    FileEncryptedBackend,
    GcpSecretManagerBackend,
    LinuxKeyringBackend,
    MacosKeychainBackend,
    MintedToken,
    SecretsBackend,
    SecretsBroker,
    SecretsBrokerError,
    VaultBackend,
    build_broker_from_config,
    clear_redaction_registry,
    get_redactable_values,
)

# ---------------------------------------------------------------------------
# In-memory backend used as a stand-in for the real ones in broker tests
# ---------------------------------------------------------------------------


class _MemoryBackend(SecretsBackend):
    name = "memory"

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets.copy()
        self.reads: list[str] = []

    def read(self, secret_name: str) -> str:
        self.reads.append(secret_name)
        if secret_name not in self._secrets:
            raise SecretsBrokerError(f"memory: no entry for {secret_name!r}")
        return self._secrets[secret_name]

    def list_names(self) -> list[str]:
        return sorted(self._secrets)


@pytest.fixture(autouse=True)
def _isolated_registry() -> None:
    """Ensure each test starts with an empty redaction registry."""
    clear_redaction_registry()
    yield
    clear_redaction_registry()


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestBrokerConfig:
    def test_minimal_config_uses_default_ttl(self) -> None:
        cfg = BrokerConfig.from_raw({"backend": "file_encrypted"})
        assert cfg.backend == "file_encrypted"
        assert cfg.ttl_seconds_default == 900

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(SecretsBrokerError, match="unknown backend"):
            BrokerConfig.from_raw({"backend": "no_such_backend"})

    def test_missing_block_rejected(self) -> None:
        with pytest.raises(SecretsBrokerError, match="empty or missing"):
            BrokerConfig.from_raw(None)

    def test_non_positive_ttl_rejected(self) -> None:
        with pytest.raises(SecretsBrokerError, match="positive"):
            BrokerConfig.from_raw({"backend": "file_encrypted", "mint": {"ttl_seconds_default": 0}})

    def test_per_secret_overrides_parsed(self) -> None:
        cfg = BrokerConfig.from_raw(
            {
                "backend": "file_encrypted",
                "mint": {"ttl_overrides": {"FAST": 60, "SLOW": 3600}},
            }
        )
        assert cfg.ttl_overrides == {"FAST": 60, "SLOW": 3600}

    def test_overrides_must_be_mapping(self) -> None:
        with pytest.raises(SecretsBrokerError, match="mapping"):
            BrokerConfig.from_raw({"backend": "file_encrypted", "mint": {"ttl_overrides": []}})


# ---------------------------------------------------------------------------
# Broker mint / resolve / revoke
# ---------------------------------------------------------------------------


def _build_broker(
    secrets: dict[str, str] | None = None,
    *,
    ttl_default: int = 900,
    ttl_overrides: dict[str, int] | None = None,
    clock_value: float = 1000.0,
) -> tuple[SecretsBroker, _MemoryBackend, list[AuditEvent], list[float]]:
    backend = _MemoryBackend(secrets or {"K": "raw-value-K"})
    events: list[AuditEvent] = []
    now = [clock_value]

    def clock() -> float:
        return now[0]

    cfg = BrokerConfig(
        backend="file_encrypted",
        ttl_seconds_default=ttl_default,
        ttl_overrides=dict(ttl_overrides or {}),
    )
    broker = SecretsBroker(backend, config=cfg, audit_sink=events.append, clock=clock)
    return broker, backend, events, now


class TestMintResolveRevoke:
    def test_mint_returns_short_lived_token_distinct_from_raw_value(self) -> None:
        broker, _, _, _ = _build_broker({"K": "the-raw-api-key"})
        token = broker.mint(secret_name="K", task_id="t1")
        assert isinstance(token, MintedToken)
        assert token.value != "the-raw-api-key"
        assert token.value.startswith("brn-")
        assert token.secret_name == "K"
        assert token.task_id == "t1"
        assert token.ttl_seconds == 900

    def test_resolve_returns_raw_backing_value(self) -> None:
        broker, _, _, _ = _build_broker({"K": "the-raw-api-key"})
        token = broker.mint(secret_name="K", task_id="t1")
        assert broker.resolve(token.value) == "the-raw-api-key"

    def test_resolve_after_revoke_rejects(self) -> None:
        broker, _, _, _ = _build_broker({"K": "raw"})
        token = broker.mint(secret_name="K", task_id="t1")
        assert broker.revoke(token.token_id) is True
        with pytest.raises(SecretsBrokerError, match="revoked"):
            broker.resolve(token.value)

    def test_resolve_after_ttl_expiry_rejects(self) -> None:
        broker, _, _, now = _build_broker({"K": "raw"}, ttl_default=10, clock_value=100.0)
        token = broker.mint(secret_name="K", task_id="t1")
        now[0] = 200.0
        with pytest.raises(SecretsBrokerError, match="expired"):
            broker.resolve(token.value)

    def test_resolve_unknown_token_rejects(self) -> None:
        broker, _, _, _ = _build_broker()
        with pytest.raises(SecretsBrokerError, match="unknown token"):
            broker.resolve("brn-not-a-real-token")

    def test_resolve_empty_token_rejects(self) -> None:
        broker, _, _, _ = _build_broker()
        with pytest.raises(SecretsBrokerError, match="empty token"):
            broker.resolve("")

    def test_revoke_unknown_token_returns_false(self) -> None:
        broker, _, _, _ = _build_broker()
        assert broker.revoke("does-not-exist") is False

    def test_per_secret_ttl_override_wins_over_default(self) -> None:
        broker, _, _, _ = _build_broker({"FAST": "v"}, ttl_default=900, ttl_overrides={"FAST": 30})
        token = broker.mint(secret_name="FAST", task_id="t1")
        assert token.ttl_seconds == 30

    def test_call_site_ttl_override_wins_over_config(self) -> None:
        broker, _, _, _ = _build_broker({"K": "v"}, ttl_overrides={"K": 30})
        token = broker.mint(secret_name="K", task_id="t1", ttl_seconds=120)
        assert token.ttl_seconds == 120

    def test_mint_rejects_empty_secret_name(self) -> None:
        broker, _, _, _ = _build_broker()
        with pytest.raises(SecretsBrokerError, match="secret_name"):
            broker.mint(secret_name="", task_id="t1")

    def test_mint_rejects_empty_task_id(self) -> None:
        broker, _, _, _ = _build_broker()
        with pytest.raises(SecretsBrokerError, match="task_id"):
            broker.mint(secret_name="K", task_id="")

    def test_mint_rejects_non_positive_ttl(self) -> None:
        broker, _, _, _ = _build_broker()
        with pytest.raises(SecretsBrokerError, match="ttl_seconds"):
            broker.mint(secret_name="K", task_id="t1", ttl_seconds=0)


# ---------------------------------------------------------------------------
# Auto-revocation
# ---------------------------------------------------------------------------


class TestAutoRevocation:
    def test_revoke_task_revokes_all_tokens_for_that_task(self) -> None:
        broker, _, _, _ = _build_broker({"A": "vA", "B": "vB"})
        t1 = broker.mint(secret_name="A", task_id="task-1")
        t2 = broker.mint(secret_name="B", task_id="task-1")
        t3 = broker.mint(secret_name="A", task_id="task-2")
        count = broker.revoke_task("task-1")
        assert count == 2
        with pytest.raises(SecretsBrokerError):
            broker.resolve(t1.value)
        with pytest.raises(SecretsBrokerError):
            broker.resolve(t2.value)
        # Other task is untouched.
        assert broker.resolve(t3.value) == "vA"

    def test_scoped_context_manager_auto_revokes(self) -> None:
        broker, _, _, _ = _build_broker({"K": "v"})
        with broker.mint_scoped(secret_name="K", task_id="t1") as token:
            assert broker.resolve(token.value) == "v"
        with pytest.raises(SecretsBrokerError, match="revoked"):
            broker.resolve(token.value)

    def test_scoped_context_manager_revokes_even_on_exception(self) -> None:
        broker, _, _, _ = _build_broker({"K": "v"})
        token_ref: MintedToken | None = None
        with pytest.raises(RuntimeError):
            with broker.mint_scoped(secret_name="K", task_id="t1") as token:
                token_ref = token
                raise RuntimeError("boom")
        assert token_ref is not None
        with pytest.raises(SecretsBrokerError, match="revoked"):
            broker.resolve(token_ref.value)

    def test_list_live_excludes_revoked_and_expired(self) -> None:
        broker, _, _, now = _build_broker({"A": "vA", "B": "vB"}, ttl_default=10)
        t1 = broker.mint(secret_name="A", task_id="t1")
        t2 = broker.mint(secret_name="B", task_id="t1")
        broker.revoke(t1.token_id)
        assert [t.token_id for t in broker.list_live()] == [t2.token_id]
        now[0] += 1000
        assert broker.list_live() == []


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


class TestAuditEvents:
    def test_mint_emits_mint_event(self) -> None:
        broker, _, events, _ = _build_broker({"K": "v"})
        broker.mint(secret_name="K", task_id="t1")
        assert [e.kind for e in events] == ["mint"]
        assert events[0].secret_name == "K"
        assert events[0].task_id == "t1"
        assert events[0].ttl_seconds == 900

    def test_revoke_emits_revoke_event_with_reason(self) -> None:
        broker, _, events, _ = _build_broker({"K": "v"})
        token = broker.mint(secret_name="K", task_id="t1")
        broker.revoke(token.token_id, reason="task-exit")
        kinds = [e.kind for e in events]
        assert kinds == ["mint", "revoke"]
        assert events[-1].reason == "task-exit"

    def test_resolve_emits_resolve_event(self) -> None:
        broker, _, events, _ = _build_broker({"K": "v"})
        token = broker.mint(secret_name="K", task_id="t1")
        broker.resolve(token.value)
        assert events[-1].kind == "resolve"

    def test_expired_resolve_emits_expire_event(self) -> None:
        broker, _, events, now = _build_broker({"K": "v"}, ttl_default=10)
        token = broker.mint(secret_name="K", task_id="t1")
        now[0] += 1000
        with pytest.raises(SecretsBrokerError):
            broker.resolve(token.value)
        assert events[-1].kind == "expire"
        assert events[-1].reason == "ttl"

    def test_audit_sink_exception_is_swallowed(self) -> None:
        backend = _MemoryBackend({"K": "v"})

        def sink(_event: AuditEvent) -> None:
            raise RuntimeError("audit sink down")

        cfg = BrokerConfig(backend="file_encrypted")
        broker = SecretsBroker(backend, config=cfg, audit_sink=sink)
        # Must not propagate; broker must keep running.
        token = broker.mint(secret_name="K", task_id="t1")
        assert broker.resolve(token.value) == "v"

    def test_audit_sink_runs_outside_broker_lock(self) -> None:
        """A reentrant call to the broker from inside the sink must not deadlock.

        ``revoke``, ``resolve`` and friends used to hold ``self._lock`` while
        invoking the audit sink. If the sink itself called back into the
        broker (for example to look up token metadata), the second acquire
        would block forever. Emitting after lock release fixes that; this
        regression test pins the contract.
        """
        backend = _MemoryBackend({"K": "v", "K2": "v2"})
        # Use a re-entrant scratch to avoid recursion within a single revoke.
        invoked: list[str] = []

        cfg = BrokerConfig(backend="file_encrypted")
        broker_holder: list[SecretsBroker] = []

        def sink(event: AuditEvent) -> None:
            invoked.append(event.kind)
            # Reentrant call: would deadlock if the broker lock were still
            # held while dispatching this event.
            if event.kind == "revoke" and "list_live" not in invoked:
                invoked.append("list_live")
                broker_holder[0].list_live()

        broker = SecretsBroker(backend, config=cfg, audit_sink=sink)
        broker_holder.append(broker)

        token = broker.mint(secret_name="K", task_id="t1")
        # Each of these would deadlock under the old emit-under-lock pattern.
        assert broker.resolve(token.value) == "v"
        broker.revoke(token.token_id)
        assert "list_live" in invoked


# ---------------------------------------------------------------------------
# Redactor coupling
# ---------------------------------------------------------------------------


class TestRedactorCoupling:
    def test_mint_registers_raw_value_for_redaction(self) -> None:
        broker, _, _, _ = _build_broker({"K": "the-raw-api-key-1234"})
        broker.mint(secret_name="K", task_id="t1")
        assert "the-raw-api-key-1234" in get_redactable_values()

    def test_short_values_skip_registration(self) -> None:
        broker, _, _, _ = _build_broker({"K": "abc"})
        broker.mint(secret_name="K", task_id="t1")
        assert "abc" not in get_redactable_values()

    def test_revoke_unregisters_minted_token_value(self) -> None:
        broker, _, _, _ = _build_broker({"K": "the-raw-api-key-1234"})
        token = broker.mint(secret_name="K", task_id="t1")
        assert token.value in get_redactable_values()
        broker.revoke(token.token_id)
        assert token.value not in get_redactable_values()

    def test_redact_text_scrubs_minted_values(self) -> None:
        from bernstein.core.security.redactor import redact_text

        broker, _, _, _ = _build_broker({"K": "the-raw-api-key-1234567"})
        token = broker.mint(secret_name="K", task_id="t1")
        transcript = f"agent log: bearer {token.value} payload={token.value} done"
        cleaned, count = redact_text(transcript)
        assert token.value not in cleaned
        assert count >= 1


# ---------------------------------------------------------------------------
# Backends (mocked)
# ---------------------------------------------------------------------------


class TestVaultBackendMocked:
    def test_read_pulls_value_field_from_kv_v2_response(self) -> None:
        backend = VaultBackend()
        body = json.dumps({"data": {"data": {"value": "vault-value"}}}).encode()

        class _Resp:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return self._payload

        with patch("urllib.request.urlopen", return_value=_Resp(body)):
            assert backend.read("api-key") == "vault-value"

    def test_read_rejects_multi_field_secret_without_value_field(self) -> None:
        backend = VaultBackend()
        body = json.dumps({"data": {"data": {"a": "1", "b": "2"}}}).encode()

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return body

        with patch("urllib.request.urlopen", return_value=_Resp()):  # type: ignore[call-arg]
            with pytest.raises(SecretsBrokerError, match="multiple fields"):
                backend.read("api-key")


class TestAwsBackendMocked:
    def test_read_returns_plain_string_when_secret_is_not_json(self) -> None:
        backend = AwsSecretsManagerBackend()

        class _FakeClient:
            def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
                return {"SecretString": "plain-aws-value"}

        class _FakeBoto3:
            @staticmethod
            def client(_name: str) -> _FakeClient:
                return _FakeClient()

        with patch.dict("sys.modules", {"boto3": _FakeBoto3}):
            assert backend.read("my-secret") == "plain-aws-value"

    def test_read_extracts_value_field_from_json_secret(self) -> None:
        backend = AwsSecretsManagerBackend()

        class _FakeClient:
            def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
                return {"SecretString": json.dumps({"value": "json-aws-value", "extra": "x"})}

        class _FakeBoto3:
            @staticmethod
            def client(_name: str) -> _FakeClient:
                return _FakeClient()

        with patch.dict("sys.modules", {"boto3": _FakeBoto3}):
            assert backend.read("my-secret") == "json-aws-value"

    def test_read_raises_when_boto3_missing(self) -> None:
        backend = AwsSecretsManagerBackend()
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(SecretsBrokerError, match="boto3"):
                backend.read("my-secret")


class TestGcpBackendMocked:
    def test_read_requires_project(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": ""}, clear=False):
            backend = GcpSecretManagerBackend(project="")
            with pytest.raises(SecretsBrokerError, match="GOOGLE_CLOUD_PROJECT"):
                backend.read("my-secret")

    def test_read_decodes_payload_bytes(self) -> None:
        backend = GcpSecretManagerBackend(project="proj-x")

        class _Payload:
            data = b"gcp-value"

        class _Resp:
            payload = _Payload()

        class _FakeClient:
            def access_secret_version(self, request: dict[str, Any]) -> _Resp:
                return _Resp()

        class _FakeSm:
            class SecretManagerServiceClient:
                def __new__(cls) -> _FakeClient:  # type: ignore[misc]
                    return _FakeClient()

        with patch.dict("sys.modules", {"google.cloud": type("M", (), {"secretmanager": _FakeSm})}):
            # Import path uses ``from google.cloud import secretmanager``; we
            # need a real-ish module structure for the import to succeed.
            import sys
            import types

            google_mod = types.ModuleType("google")
            cloud_mod = types.ModuleType("google.cloud")
            cloud_mod.secretmanager = _FakeSm  # type: ignore[attr-defined]
            google_mod.cloud = cloud_mod  # type: ignore[attr-defined]
            with patch.dict(sys.modules, {"google": google_mod, "google.cloud": cloud_mod}):
                assert backend.read("my-secret") == "gcp-value"


class TestMacosKeychainBackendMocked:
    def test_read_returns_stdout_minus_trailing_newline(self) -> None:
        backend = MacosKeychainBackend()

        class _Completed:
            returncode = 0
            stdout = "keychain-value\n"
            stderr = ""

        with patch("subprocess.run", return_value=_Completed()):
            assert backend.read("MY_KEY") == "keychain-value"

    def test_read_raises_on_nonzero_exit(self) -> None:
        backend = MacosKeychainBackend()

        class _Completed:
            returncode = 1
            stdout = ""
            stderr = "could not be found"

        with patch("subprocess.run", return_value=_Completed()):
            with pytest.raises(SecretsBrokerError, match="could not be found"):
                backend.read("MY_KEY")

    def test_read_raises_when_security_cli_missing(self) -> None:
        backend = MacosKeychainBackend()
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(SecretsBrokerError, match="security"):
                backend.read("MY_KEY")


class TestLinuxKeyringBackendMocked:
    def test_read_returns_value_from_keyring(self) -> None:
        backend = LinuxKeyringBackend()

        class _FakeKeyring:
            @staticmethod
            def get_password(service: str, name: str) -> str:
                return f"value-of-{name}"

        with patch.dict("sys.modules", {"keyring": _FakeKeyring}):
            assert backend.read("MY_KEY") == "value-of-MY_KEY"

    def test_read_raises_when_keyring_returns_none(self) -> None:
        backend = LinuxKeyringBackend()

        class _FakeKeyring:
            @staticmethod
            def get_password(service: str, name: str) -> str | None:
                return None

        with patch.dict("sys.modules", {"keyring": _FakeKeyring}):
            with pytest.raises(SecretsBrokerError, match="no entry"):
                backend.read("MY_KEY")


class TestFileEncryptedBackend:
    def test_read_round_trip(self, tmp_path) -> None:
        pytest.importorskip("cryptography")
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        payload = json.dumps({"API_KEY": "the-raw-value", "OTHER": "x"}).encode()
        ciphertext = Fernet(key).encrypt(payload)
        store = tmp_path / "secrets.enc"
        store.write_bytes(ciphertext)

        backend = FileEncryptedBackend(path=str(store))
        with patch.dict(os.environ, {"BERNSTEIN_BROKER_KEY": key.decode("utf-8")}):
            assert backend.read("API_KEY") == "the-raw-value"
            assert backend.list_names() == ["API_KEY", "OTHER"]

    def test_unknown_key_rejected(self, tmp_path) -> None:
        pytest.importorskip("cryptography")
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        ciphertext = Fernet(key).encrypt(b'{"K": "v"}')
        store = tmp_path / "secrets.enc"
        store.write_bytes(ciphertext)

        backend = FileEncryptedBackend(path=str(store))
        with patch.dict(os.environ, {"BERNSTEIN_BROKER_KEY": key.decode("utf-8")}):
            with pytest.raises(SecretsBrokerError, match="no entry"):
                backend.read("MISSING")

    def test_wrong_key_surfaces_decryption_error(self, tmp_path) -> None:
        pytest.importorskip("cryptography")
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        other = Fernet.generate_key()
        ciphertext = Fernet(key).encrypt(b'{"K": "v"}')
        store = tmp_path / "secrets.enc"
        store.write_bytes(ciphertext)

        backend = FileEncryptedBackend(path=str(store))
        with patch.dict(os.environ, {"BERNSTEIN_BROKER_KEY": other.decode("utf-8")}):
            with pytest.raises(SecretsBrokerError, match="decryption failed"):
                backend.read("K")

    def test_missing_key_config_surfaces_helpful_error(self, tmp_path) -> None:
        store = tmp_path / "secrets.enc"
        store.write_bytes(b"opaque")
        backend = FileEncryptedBackend(path=str(store))
        with patch.dict(os.environ, {"BERNSTEIN_BROKER_KEY": ""}, clear=False):
            with pytest.raises(SecretsBrokerError, match="key"):
                backend.read("K")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_build_broker_from_config_routes_to_backend(self, tmp_path) -> None:
        pytest.importorskip("cryptography")
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        payload = json.dumps({"K": "v-from-file-backend"}).encode()
        store = tmp_path / "secrets.enc"
        store.write_bytes(Fernet(key).encrypt(payload))

        raw = {
            "backend": "file_encrypted",
            "mint": {"ttl_seconds_default": 60},
            "backend_settings": {"path": str(store)},
        }
        with patch.dict(os.environ, {"BERNSTEIN_BROKER_KEY": key.decode("utf-8")}):
            broker = build_broker_from_config(raw)
            token = broker.mint(secret_name="K", task_id="t1")
            assert token.ttl_seconds == 60
            assert broker.resolve(token.value) == "v-from-file-backend"
