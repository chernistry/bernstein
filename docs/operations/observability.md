# Error telemetry and SBOM upload

Audience: SREs wiring Bernstein into an operator-managed error sink and
SBOM tracker.

## Error telemetry DSN flow

Bernstein ships a Sentry-protocol-compatible error sink wired through
`sentry-sdk`.  The DSN flows as follows:

1. Operator stands up a Sentry-protocol-compatible endpoint (any backend
   speaking the Sentry envelope protocol works).
2. Operator exports `GLITCHTIP_DSN=<dsn>` into the Bernstein process
   environment (systemd unit, container env, `direnv`, etc.).
3. `src/bernstein/cli/main.py::_init_error_telemetry` reads the env var
   at import time and calls `sentry_sdk.init(dsn=..., traces_sample_rate=0,
   profiles_sample_rate=0, send_default_pii=False, release=__version__)`.
4. When the env var is unset, missing, or empty, the helper is a no-op and
   the `sentry-sdk` package is not imported -- minimal installs pay zero
   overhead.
5. The `observability` extra (`pip install 'bernstein[observability]'`)
   pulls in `sentry-sdk[fastapi]>=2.20`; without it the helper short-circuits
   on `ImportError`.

Sample rates of zero mean events fire only on unhandled exceptions or
explicit `sentry_sdk.capture_*` calls.  No performance probes, no PII.

## SBOM upload to operator Dependency-Track

The `.github/workflows/sbom-upload.yml` workflow:

* triggers on `push` to `main` and on `release: published`,
* generates a CycloneDX SBOM via `cyclonedx-bom`,
* uploads the SBOM to a Dependency-Track instance addressed by
  `DT_API_URL` (required, no default; e.g. `https://dt.example.com`) using the
  `DT_API_KEY` secret.

The upload step is gated on both `DT_API_URL` and `DT_API_KEY` being
non-empty.  If either is unset the workflow generates the SBOM and exits
cleanly without uploading, so the pipeline is green even before the
operator has stood up Dependency-Track.
