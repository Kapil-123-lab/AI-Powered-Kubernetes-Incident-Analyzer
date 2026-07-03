# AI-Powered Kubernetes Incident Analyzer — Docker Setup

This runs the full stack — FastAPI backend, Streamlit dashboard, and MongoDB —
with a single command. Ollama (the AI model) stays running on your host
machine, since it benefits from your machine's full GPU/CPU rather than
running inside a constrained container.

## Architecture

```
┌─────────────────────────────────────────────┐
│              Your Windows Host                │
│                                               │
│   Ollama (localhost:11434)                   │
│   Docker Desktop Kubernetes cluster          │
│   ~/.kube/config                             │
│                                               │
│  ┌─────────────────────────────────────┐    │
│  │         Docker Compose Network        │    │
│  │                                       │    │
│  │  ┌──────────┐  ┌──────────┐         │    │
│  │  │ frontend │─▶│ backend  │         │    │
│  │  │ :8501    │  │ :8000    │         │    │
│  │  └──────────┘  └────┬─────┘         │    │
│  │                     │                │    │
│  │              ┌──────▼─────┐         │    │
│  │              │  mongodb   │         │    │
│  │              │  :27017    │         │    │
│  │              └────────────┘         │    │
│  └───────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

## Prerequisites

1. **Docker Desktop** installed and running, with Kubernetes enabled
   (Settings → Kubernetes → Enable Kubernetes).
2. **Ollama** installed and running on your Windows host with the
   `llama3` model pulled:
   ```powershell
   ollama pull llama3
   ```
3. A `kubeconfig` at `%USERPROFILE%\.kube\config` (the default location —
   Docker Desktop's Kubernetes sets this up automatically when enabled).

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

3. **Generate a Docker-friendly kubeconfig.**

   Docker Desktop's Kubernetes API server is reachable on your Windows
   host at `127.0.0.1:<some-port>` — but `127.0.0.1` means something
   different from inside a container (the container itself, not your
   host). This script makes a copy of your kubeconfig with that address
   swapped for `host.docker.internal`, which correctly resolves to your
   host machine from inside any container:

   ```powershell
   .\generate-docker-kubeconfig.ps1
   ```

   Run this once before first use, and again any time Docker Desktop
   restarts (it can reassign the Kubernetes API port, which would make
   the generated file stale).

4. **Build and start everything:**

   ```powershell
   docker compose up --build
   ```

   First run takes a few minutes (downloading base images, installing
   dependencies). Subsequent runs are much faster.

4. **Open the dashboard:** http://localhost:8501

5. **Stop everything:**

   ```powershell
   docker compose down
   ```

   Add `-v` to also wipe the MongoDB volume (deletes all incident history):
   ```powershell
   docker compose down -v
   ```

## Troubleshooting

### Backend can't reach Kubernetes / pods endpoint fails / "Connection refused" to 127.0.0.1:NNNNN

This means the generated kubeconfig is missing, stale, or wasn't
regenerated after a Docker Desktop restart (which can reassign the
Kubernetes API server's port). Fix:

```powershell
.\generate-docker-kubeconfig.ps1
docker compose restart backend
```

Verify the fix worked by checking what's actually mounted inside the
container:

```powershell
docker compose exec backend cat /home/appuser/.kube/config
```

The `server:` line should read `https://host.docker.internal:NNNNN`,
**not** `https://127.0.0.1:NNNNN`. If it still shows `127.0.0.1`, the
script didn't run successfully, or `kubeconfig-docker.yaml` wasn't
picked up — re-run the script and restart the backend again.

### Backend can't reach Ollama

Check the backend can resolve the host:

```powershell
docker compose exec backend curl http://host.docker.internal:11434
```

If this fails, your Docker version may not support `host.docker.internal`
automatically — the `extra_hosts` entry in `docker-compose.yml` should
cover this, but on older Docker Desktop versions you may need to upgrade.

### 401 Unauthorized on every request

`API_KEY` in `backend/.env` and `frontend/.env` don't match. Double-check
both files contain the exact same value, then:

```powershell
docker compose down
docker compose up --build
```

(Environment variable changes require a restart, not just a code reload.)

### 503 Service Unavailable

The backend's `API_KEY` is empty — `backend/.env` likely wasn't created
from the template, or Docker Compose isn't finding it. Confirm the file
exists at `backend\.env` (not `backend\.env.example`).

## Rebuilding after code changes

```powershell
docker compose up --build
```

Docker Compose only rebuilds the image layers that changed, so this is
fast after the first build.

## Viewing logs

```powershell
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongodb
```
