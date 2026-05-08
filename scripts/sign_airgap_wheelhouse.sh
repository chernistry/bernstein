#!/usr/bin/env bash
# Sign every wheel + the manifest in an air-gap wheelhouse with cosign.
#
# Usage:
#   COSIGN_KEY=cosign.key ./scripts/sign_airgap_wheelhouse.sh dist/airgap-wheelhouse/1.9.4
#   COSIGN_KEY=cosign.key COSIGN_PASSWORD=... ./scripts/sign_airgap_wheelhouse.sh <path>
#
# CI signs with a CI-only key; production releases sign with the offline key.
# Output: <wheel>.sig and MANIFEST.sig alongside each input.

set -euo pipefail

usage() {
  echo "Usage: $0 <wheelhouse-dir>" >&2
  exit 2
}

if [[ $# -lt 1 ]]; then
  usage
fi

WHEELHOUSE_DIR="$1"

if [[ ! -d "$WHEELHOUSE_DIR" ]]; then
  echo "error: wheelhouse directory not found: $WHEELHOUSE_DIR" >&2
  exit 1
fi

if [[ ! -f "$WHEELHOUSE_DIR/MANIFEST.json" ]]; then
  echo "error: missing MANIFEST.json in $WHEELHOUSE_DIR" >&2
  exit 1
fi

if ! command -v cosign >/dev/null 2>&1; then
  echo "error: cosign is required (https://docs.sigstore.dev/cosign/installation)" >&2
  exit 1
fi

if [[ -z "${COSIGN_KEY:-}" ]]; then
  echo "error: COSIGN_KEY env var must point at a cosign private key" >&2
  exit 1
fi

# --tlog-upload=false: do not push to the public Rekor transparency log.
# Air-gap signing happens on disconnected build hosts; the default Rekor
# upload would dial rekor.sigstore.dev and fail (or silently leak the
# fact a release was cut). Operators running a private Rekor opt back in
# by exporting COSIGN_TLOG_UPLOAD=true.
TLOG_FLAG="--tlog-upload=false"
if [[ "${COSIGN_TLOG_UPLOAD:-}" == "true" ]]; then
  TLOG_FLAG="--tlog-upload=true"
fi

sign_blob() {
  local target="$1"
  local sig="${target}.sig"
  cosign sign-blob --yes "$TLOG_FLAG" --key "$COSIGN_KEY" --output-signature "$sig" "$target" >/dev/null
  echo "signed: ${target##*/}"
}

shopt -s nullglob
for wheel in "$WHEELHOUSE_DIR"/*.whl; do
  sign_blob "$wheel"
done

manifest_target="$WHEELHOUSE_DIR/MANIFEST.json"
manifest_sig="$WHEELHOUSE_DIR/MANIFEST.sig"
cosign sign-blob --yes "$TLOG_FLAG" --key "$COSIGN_KEY" --output-signature "$manifest_sig" "$manifest_target" >/dev/null
echo "signed: MANIFEST.json -> MANIFEST.sig"

echo
echo "wheelhouse signed: $WHEELHOUSE_DIR"
