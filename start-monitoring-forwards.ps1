<#
.SYNOPSIS
    Starts background port-forwards so the real, already-running in-cluster
    Prometheus and Grafana (installed via Helm in your Kubernetes cluster)
    are reachable from your host machine -- and therefore from Docker
    Compose containers via host.docker.internal.

.DESCRIPTION
    Docker Desktop's NodePort forwarding didn't work reliably in this setup,
    so this uses "kubectl port-forward" instead, which tunnels through
    kubectl directly and works regardless of NodePort behavior.

    Run this BEFORE "docker compose up", and leave the two PowerShell
    windows it opens running for as long as you want Prometheus/Grafana
    data to flow. Closing them stops the tunnels.

.NOTES
    Prometheus -> http://localhost:9090
    Grafana    -> http://localhost:3000
    (Same ports the project already expects -- no docker-compose.yml or
    app.py changes needed.)
#>

$ErrorActionPreference = "Stop"

Write-Host "Starting port-forward: prometheus-server -> localhost:9090" -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "kubectl port-forward -n monitoring svc/prometheus-server 9090:80"

Write-Host "Starting port-forward: grafana -> localhost:3000" -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "kubectl port-forward -n default svc/grafana 3000:80"

Write-Host ""
Write-Host "Two new PowerShell windows opened -- leave them running." -ForegroundColor Cyan
Write-Host "Now run: docker compose up --build"
