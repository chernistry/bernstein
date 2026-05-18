#!/bin/bash -eu
# Build script invoked inside the ClusterFuzzLite builder container.
#
# Compiles each fuzz_*.py harness with atheris and copies the resulting
# binaries plus their seed corpora into $OUT. atheris is shipped pre-installed
# in the gcr.io/oss-fuzz-base/base-builder-python image.
#
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

pip3 install --no-cache-dir .

for fuzzer in "$SRC/bernstein/.clusterfuzzlite"/fuzz_*.py; do
  fuzzer_basename=$(basename -s .py "$fuzzer")
  compile_python_fuzzer "$fuzzer" --add-data "$SRC/bernstein:bernstein"
done
