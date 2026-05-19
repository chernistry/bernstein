#!/bin/bash -eu
# Build script invoked inside the ClusterFuzzLite builder container.
#
# Compiles each fuzz_*.py harness with atheris and copies the resulting
# binaries into $OUT. atheris and PyYAML are shipped pre-installed in the
# gcr.io/oss-fuzz-base/base-builder-python image; we still re-install
# PyYAML through a hash-pinned requirements file so the supply chain
# stays auditable (Scorecard pinned-dependencies).
#
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

pip3 install \
  --no-cache-dir \
  --require-hashes \
  --requirement "$SRC/bernstein/.clusterfuzzlite/requirements.txt"

for fuzzer in "$SRC/bernstein/.clusterfuzzlite"/fuzz_*.py; do
  compile_python_fuzzer "$fuzzer"
done
