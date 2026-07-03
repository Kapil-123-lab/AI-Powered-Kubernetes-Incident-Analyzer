# ─────────────────────────────────────────────────────────
# Backend Dockerfile — AI Kubernetes Incident Analyzer API
# ─────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# System deps needed by some Python packages (e.g. reportlab fonts,
# pymongo's SSL, kubernetes client's cert handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Non-root user for security
RUN useradd -m appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f -H "X-API-Key: ${API_KEY}" http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
