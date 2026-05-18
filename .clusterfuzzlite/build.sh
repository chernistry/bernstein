#!/bin/bash -eu
# Build script invoked inside the ClusterFuzzLite builder container.
#
# Compiles each fuzz_*.py harness with atheris and copies the resulting
# binaries into $OUT. atheris and PyYAML are shipped pre-installed in the
# gcr.io/oss-fuzz-base/base-builder-python image, so no extra pip install
# is needed for the YAML safe_load harness.
#
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

pip3 install --no-cache-dir pyyaml

for fuzzer in "$SRC/bernstein/.clusterfuzzlite"/fuzz_*.py; do
  compile_python_fuzzer "$fuzzer"
done
