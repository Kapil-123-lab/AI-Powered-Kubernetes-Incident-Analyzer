# 🚀 AI-Powered Kubernetes Incident Analyzer

![CI](https://github.com/Kapil-123-lab/AI-Powered-Kubernetes-Incident-Analyzer/actions/workflows/ci.yml/badge.svg)

An AI-driven observability tool that watches a Kubernetes cluster, detects
failing/restarting pods, and uses a locally-run LLM (via Ollama) to generate
a root-cause analysis, severity rating, and a concrete suggested fix —
grounded in real Prometheus metrics, not just log text. Runs as a fully
Dockerized multi-service stack.

*(Screenshot/GIF of the dashboard here — Incidents page + MTTR/MTBF page work well.)*

## ✨ Features

- **Automatic incident detection** — a background scheduler polls the
  Kubernetes API for failing/restarting pods and triggers analysis
  automatically, no manual intervention needed
- **AI root-cause analysis** — pod logs are sent to a local LLM (Llama 3 via
  Ollama) which returns root cause, severity, resolution steps, and
  preventive action
- **Metrics-grounded analysis** — live Prometheus data (node/pod CPU,
  memory, restart counts) is included in every AI prompt, so conclusions
  are backed by real numbers, not log text alone
- **AI Suggested Fixes** — a distinct, copyable `kubectl` command
  recommendation per incident, separate from the general analysis
- **MTTR / MTBF dashboard** — real SRE metrics computed from incident
  history: Mean Time To Resolution (once marked resolved) and Mean Time
  Between Failures (cluster-wide)
- **Duplicate-incident suppression** — a pod isn't re-logged as a "new"
  incident on every scan cycle; only when its restart count actually
  increases, or after a cooldown window
- **PDF incident reports**, **Jira auto-ticketing**, and **Slack/email
  alerting hooks** (optional, configured via environment variables)
- **Grafana/Prometheus integration** — designed to plug into an existing
  in-cluster monitoring stack (e.g. a Helm-installed `kube-prometheus-stack`)
  rather than duplicating one

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          Host Machine                             │
│                                                                    │
│   Ollama (localhost:11434)      Kubernetes cluster (Docker Desktop)│
│   kubectl port-forward tunnels ──────┐                             │
│      Prometheus → :9090              │                             │
│      Grafana    → :3000              │                             │
│                                       ▼                             │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    Docker Compose Network                    │  │
│  │                                                                │  │
│  │   ┌──────────┐      ┌───────────┐      ┌─────────┐          │  │
│  │   │ frontend │─────▶│  backend  │─────▶│ mongodb │          │  │
│  │   │(Streamlit)│      │ (FastAPI) │      │         │          │  │
│  │   │  :8501   │      │   :8000   │      │ :27017  │          │  │
│  │   └──────────┘      └─────┬─────┘      └─────────┘          │  │
│  │                            │                                  │  │
│  │        Kubernetes API ◀────┼────▶ Ollama (host)               │  │
│  │        Prometheus (host) ◀─┘                                  │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## 🧰 Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit |
| Backend API | FastAPI |
| AI / LLM | Ollama (Llama 3), running locally |
| Database | MongoDB |
| Metrics | Prometheus |
| Dashboards | Grafana |
| Orchestration | Kubernetes (Docker Desktop) |
| Containerization | Docker, Docker Compose |
| CI | GitHub Actions |
| Alerting (optional) | Slack, Jira, Email |

## ⚙️ CI Pipeline

Every push/PR runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml),
which:

1. Lints and syntax-checks both Python services
2. Builds the `backend` and `frontend` Docker images
3. On `main`, publishes both images to GitHub Container Registry (GHCR) —
   no external registry account needed, it uses the repo's own
   `GITHUB_TOKEN`

This validates that both services actually build cleanly on every change,
without needing a live deployment target.

## Prerequisites

1. **Docker Desktop** installed and running, with Kubernetes enabled
   (Settings → Kubernetes → Enable Kubernetes).
2. **Ollama** installed and running on your host with the `llama3` model
   pulled:
   ```powershell
   ollama pull llama3
   ```
3. A `kubeconfig` at `%USERPROFILE%\.kube\config` (the default location —
   Docker Desktop's Kubernetes sets this up automatically when enabled).
4. *(Optional, for real metrics)* A Prometheus + Grafana stack already
   running in-cluster (e.g. via the `prometheus-community/kube-prometheus-stack`
   Helm chart).

## Setup

1. **Create your `.env` files** (one per service — these hold secrets and
   are never committed to git):

   ```powershell
   copy backend\.env.example backend\.env
   copy frontend\.env.example frontend\.env
   ```

2. **Generate an API key** and put the SAME value in both `.env` files:

   ```powershell
   py -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Edit `backend\.env` and `frontend\.env`, set:
   ```
   API_KEY=the-key-you-generated
   ```

3. **Generate a Docker-friendly kubeconfig:**

   ```powershell
   .\generate-docker-kubeconfig.ps1
   ```

   Run this once before first use, and again any time Docker Desktop
   restarts (it can reassign the Kubernetes API port).

4. *(Optional)* **Start monitoring tunnels**, if you have an in-cluster
   Prometheus/Grafana stack:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start-monitoring-forwards.ps1
   ```

5. **Build and start everything:**

   ```powershell
   docker compose up --build
   ```

6. **Open the dashboard:** http://localhost:8501

7. **Stop everything:**

   ```powershell
   docker compose down
   ```

   Add `-v` to also wipe the MongoDB volume (deletes all incident history).

## Troubleshooting

### Backend can't reach Kubernetes / pods endpoint fails

Regenerate the kubeconfig and restart:

```powershell
.\generate-docker-kubeconfig.ps1
docker compose restart backend
```

Verify what's actually mounted inside the container:

```powershell
docker compose exec backend cat /home/appuser/.kube/config
```

The `server:` line should read `https://host.docker.internal:NNNNN`, not
`https://127.0.0.1:NNNNN`.

### Backend can't reach Ollama

```powershell
docker compose exec backend curl http://host.docker.internal:11434
```

### 401 Unauthorized on every request

`API_KEY` in `backend/.env` and `frontend/.env` don't match. Fix both,
then:

```powershell
docker compose down
docker compose up --build
```

### Prometheus datasource / metrics not showing

Confirm the port-forward tunnels are running:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-monitoring-forwards.ps1
```

Leave the two windows it opens running for as long as you want live
metrics flowing.

## Rebuilding after code changes

```powershell
docker compose up --build
```

## Viewing logs

```powershell
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongodb
```

## 🗺️ Roadmap / Possible Next Steps

- SLO / Error Budget dashboard
- Kubernetes Events analysis (not just pod restarts)
- Alert deduplication across Slack/Jira, not just internal incident storage
- Helm chart for deploying this analyzer itself into a cluster
- Runbook integration (link known issues to documented remediation steps)
