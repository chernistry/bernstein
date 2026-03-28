# Stage 1: build
FROM python:3.12-slim AS build

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir hatchling && \
    python -m hatchling build

# Stage 2: runtime
FROM python:3.12-slim

LABEL org.opencontainers.image.title="bernstein" \
      org.opencontainers.image.description="Multi-agent orchestration for CLI coding agents" \
      org.opencontainers.image.source="https://github.com/bernstein-ai/bernstein"

# Install git (required for git_ops) and curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY --from=build /app/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Create non-root user
RUN useradd -m -u 1000 bernstein && chown bernstein:bernstein /workspace
USER bernstein

# Bernstein state directory (mount a volume here for persistence)
VOLUME ["/workspace/.sdd"]

# Task server port
EXPOSE 8052

# Default: run the task server
# Override CMD to run orchestrator or worker modes
ENTRYPOINT ["bernstein"]
CMD ["start"]
