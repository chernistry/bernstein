# custom-guardrail

example bernstein plugin: a fail-closed guardrail that blocks any prompt
containing canonical secret-shaped tokens (aws keys, github tokens, etc).

intended as a worked example of the `GuardrailPipeline` extension point
in `bernstein.core.security.guardrail_pipeline`. real deployments should
prefer the in-tree `SecretLeakGuardrail` for output checking; this
plugin demonstrates how to add a custom **input** check via the plugin
sdk so the same patterns can ride into vendor-specific scanners.

## install (editable, for development)

```bash
pip install -e examples/plugins/custom-guardrail
```

bernstein discovers the plugin via the `bernstein.plugins` entry-point
group declared in `pyproject.toml`. nothing else to wire up.

## what it blocks

regex-matches before the agent ever spawns. fail-closed = an empty or
near-empty list of violations still rejects the prompt when any pattern
fires. patterns shipped:

| token shape                     | example fragment                |
|---------------------------------|---------------------------------|
| `AWS_SECRET_ACCESS_KEY`         | env-var leak in pasted shell    |
| `AKIA[0-9A-Z]{16}`              | aws access key id               |
| `ghp_[a-zA-Z0-9]{36}`           | github personal token (classic) |
| `github_pat_[A-Za-z0-9_]{20,}`  | github fine-grained pat         |
| `sk-[a-zA-Z0-9]{20,}`           | openai-shaped api key           |
| `xox[baprs]-[a-zA-Z0-9-]{10,}`  | slack token                     |

extend the list by passing `extra_patterns=[...]` to the plugin
constructor or by subclassing `NoSecretsGuardrail`.

## test

```bash
cd examples/plugins/custom-guardrail
pytest tests/ -q
```
