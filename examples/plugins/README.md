# bernstein plugin examples

two flavours of example live here:

* **single-file plugins** (`adapter_plugin.py`, `slack_notifier.py`, ...) —
  quick demos meant to be copied into your own project. minimal
  scaffolding, no `pyproject.toml`, no entry-point declaration.
* **installable example packages** (`custom-guardrail/`,
  `custom-adapter/`, `custom-audit-sink/`) — full `pyproject.toml` +
  entry-point + tests, ready to `pip install -e <dir>` so bernstein
  auto-discovers them via the `bernstein.plugins` /
  `bernstein.adapters` entry-point groups.

the installable packages each demonstrate one extension point in
isolation; copy whichever one matches your use case and rename.

## installable examples

### custom-guardrail

fail-closed guardrail that blocks any prompt containing a canonical
secret-shaped token (aws keys, github tokens, openai keys, slack
tokens). plugs into `GuardrailPipeline.add(...)` via the
`configure_guardrails` hook so the orchestrator reaches the same code
path it uses for the in-tree `PromptInjectionGuardrail` /
`SecretLeakGuardrail`.

### custom-adapter

a `claude-mock` adapter that returns deterministic, byte-stable canned
responses without spawning the real claude binary. useful for offline
ci, contract / golden-file tests, and demos where you want bernstein's
full orchestration loop to run without spending budget on real model
calls. registers under the slug `claude_mock` via the
`bernstein.adapters` entry-point group.

### custom-audit-sink

mirrors bernstein audit events to an `mqtt://` topic so a downstream
siem (splunk, datadog, elastic, ...) can ingest them. implements the
`on_audit_event` hookspec which lives in
`src/bernstein/plugins/hookspecs.py` (background-execution: a slow
broker can never stall the orchestrator's tick loop). dormant by
default — set `BERNSTEIN_AUDIT_MQTT_ENABLED=1` to turn it on.

## install + run

```bash
pip install -e examples/plugins/custom-guardrail
pip install -e examples/plugins/custom-adapter
pip install -e examples/plugins/custom-audit-sink

# every plugin's hooks fire automatically once the package is
# discoverable on the python path; bernstein scans the
# bernstein.plugins / bernstein.adapters entry-point groups at
# orchestrator startup.

bernstein run --cli claude_mock plan.yaml   # exercises the mock adapter
```

per-package readmes have configuration knobs and test instructions.
