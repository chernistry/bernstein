# Strict structured-output schemas

Adapters parse model-emitted structured output and merge it into stored
artefacts. A lenient parse opens two failure modes:

1. Hallucinated keys land in storage and break downstream consumers that did
   not expect them.
2. AI updates overwrite operator-owned fields (notes, tags, `id`).

The strict contract closes both. The shared primitives live in
`bernstein.adapters.strict_schema`.

## The strict contract

Every schema that parses AI output forbids extra keys.

- JSON Schema artefacts (the per-phase schemas in
  `bernstein.core.orchestration.phase_schemas`) declare
  `additionalProperties: false` on every object node. Use `seal_schema` to
  produce a sealed copy and `assert_schema_sealed` to prove a schema cannot
  accept undeclared keys.
- Dataclass-backed payloads (the refinement `Critique` in
  `bernstein.core.orchestration.refinement_schemas`) expose a
  `from_dict_strict` parse path. A hallucinated top-level key, or a
  hallucinated key inside any nested issue, raises `SchemaViolation` rather
  than being silently dropped.

A `SchemaViolation` is deterministic, so the retry path treats it as
non-transient and bounds its attempts.

## User-owned field registry

Some fields are managed by operators or by the store, never by AI output.
`USER_OWNED_FIELDS` is the global floor:

| Field | Owner |
| --- | --- |
| `user_notes` | operator |
| `operator_overrides` | operator |
| `id` | store |
| `created_at` | store |

A schema may add to this floor through `UserOwnedFieldRegistry.register`.
Per-schema entries are unioned with the floor, so a registration can never
weaken the global protection.

### Stripping AI writes

Before an AI update reaches the merge layer, pass it through
`strip_user_owned_fields`:

```python
from bernstein.adapters.strict_schema import strip_user_owned_fields

safe_update, rejected = strip_user_owned_fields(schema_id, ai_update)
if rejected is not None:
    # rejected names the schema and the stripped operator-owned fields.
    audit_log.record(rejected)
merge(safe_update)
```

Any blacklisted key is removed from a copy of the update. When at least one
field is stripped, an `AIWriteRejected{schema, fields}` event is logged and
returned so the audit surface can attribute the attempt.

## Provider error classification

A provider may reject a structured-output request because the response
carried undeclared keys. `classify_schema_violation` maps those messages to a
`SchemaViolation` carrying any field names lifted from the message:

```python
from bernstein.adapters.strict_schema import classify_schema_violation

violation = classify_schema_violation(provider_error)
if violation is not None:
    # Deterministic schema fault: do not retry without changing the payload.
    raise violation
```

Unrelated errors return `None` so the caller falls back to its general error
path.

## Fixtures

`tests/fixtures/adapter_outputs/` holds strict-clean fixtures that must parse
under the strict contract. Payloads that carried previously-tolerated extras
live under `tests/fixtures/adapter_outputs/legacy/`; the migration test
asserts they are rejected under strict mode.
