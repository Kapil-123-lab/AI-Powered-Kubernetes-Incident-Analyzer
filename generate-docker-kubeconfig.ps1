<#
.SYNOPSIS
    Generates a Docker-friendly copy of your kubeconfig for Docker Desktop's
    Kubernetes cluster.

.DESCRIPTION
    Docker Desktop's Kubernetes API server is reachable on your Windows host
    at 127.0.0.1:<port>. But "127.0.0.1" means something different inside a
    container (the container itself, not your host machine). This script
    copies your kubeconfig and swaps that address for "host.docker.internal",
    which Docker Desktop resolves automatically to your host machine from
    inside any container -- no extra Docker Compose configuration needed.

    Note: Docker Desktop's Kubernetes API certificate isn't necessarily
    issued for "host.docker.internal" (the exact set of valid hostnames
    varies by Docker Desktop version), so the backend disables TLS
    hostname/cert verification for its Kubernetes client specifically.
    That's handled in backend/main.py, not in this script.

    Run this once before first use, and again any time Docker Desktop
    restarts (it can reassign the Kubernetes API server's port, which would
    make the previously generated file stale).

.OUTPUTS
    Writes kubeconfig-docker.yaml to the current directory (the project root),
    which docker-compose.yml mounts into the backend container.
#>

$ErrorActionPreference = "Stop"

$sourcePath = Join-Path $env:USERPROFILE ".kube\config"
$destPath   = Join-Path (Get-Location) "kubeconfig-docker.yaml"

if (-not (Test-Path $sourcePath)) {
    Write-Error "Could not find kubeconfig at $sourcePath. Make sure Docker Desktop's Kubernetes is enabled (Settings -> Kubernetes -> Enable Kubernetes)."
    exit 1
}

$content = Get-Content -Path $sourcePath -Raw

# Docker Desktop's Kubernetes server line looks like:
#   server: https://127.0.0.1:56789
# Swap the loopback address for host.docker.internal, keep whatever port
# Docker Desktop assigned.
$pattern = 'https://127\.0\.0\.1:(\d+)'

if ($content -notmatch $pattern) {
    Write-Warning "Could not find a 'https://127.0.0.1:<port>' server entry in $sourcePath."
    Write-Warning "Your kubeconfig may use a different address already, or Kubernetes may not be running."
}

$updatedContent = $content -replace $pattern, 'https://host.docker.internal:$1'

Set-Content -Path $destPath -Value $updatedContent -NoNewline

Write-Host "Wrote Docker-friendly kubeconfig to: $destPath" -ForegroundColor Green

if ($updatedContent -match 'https://host\.docker\.internal:(\d+)') {
    Write-Host "Kubernetes API server will be reached at: https://host.docker.internal:$($Matches[1])" -ForegroundColor Green
} else {
    Write-Warning "No host.docker.internal entry found in the output file -- double check $sourcePath manually."
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  docker compose up --build"
Write-Host ""
Write-Host "If Docker Desktop restarts later, re-run this script, then:"
Write-Host "  docker compose restart backend"
