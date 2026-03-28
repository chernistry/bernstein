# Bernstein — Deployment Guide

This guide covers deploying Bernstein in cluster mode: Docker Compose for local/dev clusters and Kubernetes (via Helm) for production.

---

## Prerequisites

- Docker 24+ with Compose v2
- (For K8s) kubectl + Helm 3.12+
- At least one LLM provider API key (e.g. `ANTHROPIC_API_KEY`)

---

## Docker Compose

### Quick start

```bash
# 1. Copy and fill in your API keys
cp .env.example .env
$EDITOR .env

# 2. Build the image and start the cluster
docker compose up --build -d

# 3. Check status
curl http://localhost:8052/health
docker compose ps
```

### Scale workers

```bash
# Run 4 parallel workers
docker compose up --scale bernstein-worker=4 -d
```

### Services

| Service | Description | Port |
|---|---|---|
| `bernstein-server` | Task server — shared state coordinator | 8052 |
| `bernstein-orchestrator` | Reads backlog, decomposes goals into tasks | — |
| `bernstein-worker` | Claims and executes tasks via CLI agents | — |
| `postgres` | Persistent relational store (future use) | 5432 |
| `redis` | Distributed locks + bulletin board (future use) | 6379 |

### Environment variables

Create `.env` from the table below:

| Variable | Required | Description |
|---|---|---|
| `BERNSTEIN_AUTH_TOKEN` | Yes | Shared secret for inter-node auth (pick any random string) |
| `ANTHROPIC_API_KEY` | If using Claude | Claude API key |
| `OPENAI_API_KEY` | If using Codex | OpenAI API key |
| `GOOGLE_API_KEY` | If using Gemini | Google AI API key |
| `OPENROUTER_API_KEY` | Optional | OpenRouter aggregator key |
| `TAVILY_API_KEY` | Optional | Web search tool key |

### Persistent state

`.sdd/` is mounted as a named volume (`sdd-data`). To back it up:

```bash
docker run --rm -v bernstein_sdd-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/sdd-backup.tar.gz /data
```

---

## Kubernetes (Helm)

### Add Bitnami repo (required for PostgreSQL + Redis sub-charts)

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
```

### Create provider keys secret

```bash
kubectl create secret generic bernstein-provider-keys \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..." \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=GOOGLE_API_KEY="AIza..."
```

### Install

```bash
helm dependency update ./deploy/helm/bernstein

helm install bernstein ./deploy/helm/bernstein \
  --namespace bernstein \
  --create-namespace \
  --set providerKeys.existingSecret=bernstein-provider-keys
```

### Upgrade

```bash
helm upgrade bernstein ./deploy/helm/bernstein \
  --namespace bernstein \
  --set providerKeys.existingSecret=bernstein-provider-keys
```

### Uninstall

```bash
helm uninstall bernstein --namespace bernstein
```

### Common overrides

**Scale workers:**
```bash
helm upgrade bernstein ./deploy/helm/bernstein \
  --namespace bernstein \
  --set worker.replicaCount=8
```

**Disable HPA (fixed worker count):**
```bash
--set worker.autoscaling.enabled=false
```

**Expose the task server via ingress:**
```bash
--set ingress.enabled=true \
--set ingress.className=nginx \
--set "ingress.hosts[0].host=bernstein.example.com" \
--set "ingress.hosts[0].paths[0].path=/" \
--set "ingress.hosts[0].paths[0].pathType=Prefix"
```

**Use external PostgreSQL/Redis (e.g. managed cloud services):**
```bash
--set postgresql.enabled=false \
--set redis.enabled=false \
--set externalDatabase.url="postgresql://user:pass@host:5432/bernstein" \
--set externalRedis.url="redis://host:6379/0"
```

### Architecture

```
                          ┌─────────────────┐
                          │  Ingress (opt.)  │
                          └────────┬────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      bernstein-server        │
                    │   Deployment + Service       │
                    │   (ClusterIP :8052)          │
                    └──┬──────────────────────┬───┘
                       │                      │
         ┌─────────────▼─────────┐   ┌────────▼────────────┐
         │  bernstein-orchestrat │   │  bernstein-worker    │
         │  Deployment (1 pod)   │   │  StatefulSet (N pods)│
         │  conduct --remote     │   │  conduct --worker    │
         └───────────────────────┘   └─────────────────────┘
                       │                      │
              ┌────────▼────────┐   ┌─────────▼──────────┐
              │   PostgreSQL    │   │       Redis          │
              │  (bitnami chart)│   │  (bitnami chart)    │
              └─────────────────┘   └────────────────────┘
```

### Resource sizing guide

| Role | Replicas | CPU req | Mem req | Notes |
|---|---|---|---|---|
| server | 1 | 100m | 256Mi | Stateful — single replica |
| orchestrator | 1 | 100m | 128Mi | Reads backlog, no heavy compute |
| worker | 2–20 | 500m | 512Mi | Scale based on task throughput |

Workers make outbound calls to LLM APIs and run `claude`/`codex`/`gemini` CLI binaries. They do **not** need GPUs.

### Secrets management

Never put API keys in `values.yaml`. Use one of:

- **Kubernetes Secrets** (`kubectl create secret`) — simplest
- **External Secrets Operator** — sync from AWS Secrets Manager, Vault, GCP Secret Manager
- **Sealed Secrets** — encrypted secrets committed to git

### Health checks

```bash
# Task server health
kubectl exec -n bernstein deploy/bernstein-server -- \
  curl -s http://localhost:8052/health

# Live task queue
kubectl exec -n bernstein deploy/bernstein-server -- \
  curl -s http://localhost:8052/status
```

---

## CI/CD integration

To build and push the image in CI:

```bash
docker build -t your-registry/bernstein:$GIT_SHA .
docker push your-registry/bernstein:$GIT_SHA

helm upgrade bernstein ./deploy/helm/bernstein \
  --set image.repository=your-registry/bernstein \
  --set image.tag=$GIT_SHA
```

---

## Troubleshooting

**Server health check fails on startup**
The server waits for PostgreSQL to be ready. Check postgres logs:
```bash
docker compose logs postgres
# or
kubectl logs -n bernstein -l app.kubernetes.io/component=postgresql
```

**Workers not claiming tasks**
Verify `BERNSTEIN_AUTH_TOKEN` matches across all nodes:
```bash
docker compose exec bernstein-worker env | grep AUTH
```

**Task server unreachable from workers**
In K8s, check the Service is up:
```bash
kubectl get svc -n bernstein
kubectl describe svc bernstein-server -n bernstein
```
