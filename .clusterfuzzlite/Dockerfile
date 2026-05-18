# ClusterFuzzLite builder image for bernstein.
#
# Digest-pinned base image so the build is reproducible and Scorecard's
# Pinned-Dependencies check is satisfied.
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:e24e8e50612617101dd10038502f68268ab45f31d79a3722c230464277a951b3

COPY . /src/bernstein
WORKDIR /src/bernstein
COPY .clusterfuzzlite/build.sh /
