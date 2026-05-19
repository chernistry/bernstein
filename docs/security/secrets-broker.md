# Secrets broker

A short-lived-token broker that replaces dotfile-in-workspace and
process-env credential patterns for agent spawns. The agent process
receives only a minted token; the raw backing secret never appears in the
spawned environment.

## Lifecycle

1. Operator declares a `security.secrets` block in `bernstein.yaml` with
   a chosen backend.
2. Orchestrator builds the broker once at startup.
3. Each task asks the broker to mint a token for a named secret with a
   TTL. The broker reads the raw value from the backend, generates a new
   opaque token, and registers the mapping in-process.
4. The minted token value is plumbed into the agent's env in place of
   the real credential.
5. On task exit (success or failure) the broker revokes every token
   owned by that task id; tokens also auto-expire at their TTL.
6. The redactor scrubs both the raw backing value and the minted token
   value from any persisted transcript.

## Config

```yaml
security:
  secrets:
    backend: file_encrypted     # vault | aws_secretsmanager | gcp_secret_manager
                                # | macos_keychain | linux_keyring | file_encrypted
    mint:
      ttl_seconds_default: 900  # default token lifetime
      ttl_overrides:
        ANTHROPIC_API_KEY: 1800
        SHORT_LIVED_TOKEN: 60
    backend_settings:           # forwarded to the backend constructor
      path: /var/lib/bernstein/secrets.enc
```

## Backend setup

### vault

Reads `VAULT_ADDR` and `VAULT_TOKEN` from env. Pass `mount` in
`backend_settings` to override the default KV mount (`secret`). Secret
payloads must either contain a `value` field or a single field; the
broker reads exactly one scalar per name.

### aws_secretsmanager

Requires `boto3`. The standard AWS credential resolution chain applies
(env vars, profile, instance role). `secret_name` is the secret name or
ARN. JSON payloads with a `value` field are unwrapped automatically;
plain-string payloads pass through unchanged.

### gcp_secret_manager

Requires `google-cloud-secret-manager`. Reads `GOOGLE_CLOUD_PROJECT`
from env or `project` from `backend_settings`. Always reads the
`latest` version by default; override via `version`.

### macos_keychain

Shells out to the system `security` CLI. Pass `service` in
`backend_settings` to use a non-default service name (default:
`bernstein`). Store an item with:

```
security add-generic-password -s bernstein -a ANTHROPIC_API_KEY -w 'sk-ant-...'
```

### linux_keyring

Uses the `keyring` Python package, which brokers between freedesktop
Secret Service, KWallet, and the other supported backends. Store an
item with:

```python
import keyring
keyring.set_password("bernstein", "ANTHROPIC_API_KEY", "sk-ant-...")
```

### file_encrypted

Zero-dependency fallback: a Fernet-encrypted JSON object on disk.
Requires `cryptography`. The encryption key is supplied via
`BERNSTEIN_BROKER_KEY` (urlsafe base64) or a file path in
`backend_settings.key_path`. Build the store by encrypting a JSON
object that maps secret names to values.

## CLI

```
bernstein secrets list                            # enumerate backend secrets where supported
bernstein secrets mint --task t-42 --secret ANTHROPIC_API_KEY --ttl 900
bernstein secrets mint --task t-42 --secret K --reveal     # print the raw token value
```

`mint` prints a JSON object with the token id, secret name, task id,
TTL, expiry, and a masked token value. Pass `--reveal` to print the raw
token value when piping into an out-of-band agent invocation.

## Audit log

Every mint, resolve, revoke, and expiry event flows through:

- The module logger at INFO level. Only non-secret identifiers (token
  id, secret name, task id, TTL) are logged, never the raw value.
- An optional in-process `AuditSink` callback that the orchestrator can
  wire to the lineage subsystem or any other audit store.

## Redactor coupling

`bernstein.core.security.redactor.redact_text` consults the broker's
in-process registry and scrubs every minted token value and raw backing
value from the text it processes. The registry is updated automatically
on mint and revoke; tests can clear it via
`clear_redaction_registry()`.
