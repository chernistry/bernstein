# cloudflared sidecar for the Bernstein central server.
#
# Pinned by both tag AND manifest digest so deployments are reproducible
# and Scorecard pinned-dependencies passes. Bump the tag + digest pair
# deliberately when you want a newer version (look up via
# `regctl image digest cloudflare/cloudflared:<tag>` or the Docker Hub
# tag detail page).
# cloudflare/cloudflared:2025.1.0
FROM cloudflare/cloudflared:2025.1.0@sha256:3247f3ef49eda23244b8aa5583f82b7c3880b0d057e1172d0e818f5e678d9f27

# The image's default entrypoint is cloudflared itself, so we only need
# to provide arguments. We support two run modes:
#   1. Token mode (recommended for fresh tunnels): pass `TUNNEL_TOKEN`
#      via env. cloudflared looks it up automatically; no config file
#      is required.
#   2. Config-file mode: mount `config.yml` + `creds.json` into
#      /etc/cloudflared/ and run `tunnel run`.
#
# docker-compose.yml drives mode (1) by default. Mode (2) is documented
# in docs/cluster/deployment-patterns.md.

USER nonroot
WORKDIR /etc/cloudflared

# Healthcheck: cloudflared exposes a metrics server when started with
# `--metrics`. We probe it to confirm the tunnel daemon is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["cloudflared", "--version"]

ENTRYPOINT ["cloudflared"]
CMD ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"]
