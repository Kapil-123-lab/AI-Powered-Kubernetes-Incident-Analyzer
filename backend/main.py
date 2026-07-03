import os
import re
import uuid
import atexit
from datetime import datetime

from dotenv import load_dotenv

from fastapi import FastAPI, Depends, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from apscheduler.schedulers.background import BackgroundScheduler

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from pymongo import MongoClient
from prometheus_api_client import PrometheusConnect
import ollama
from kubernetes import client, config
import requests
import urllib3

load_dotenv()  # reads .env in the current working directory into os.environ

# ── Ollama host ──────────────────────────────────────────────
# The ollama Python library defaults to http://localhost:11434.
# Inside a Docker container, "localhost" refers to the container
# itself — not your Windows host where Ollama is actually running.
# Docker Desktop provides the special DNS name "host.docker.internal"
# to reach the host machine from inside a container.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
ollama_client = ollama.Client(host=OLLAMA_HOST)


# ===========================
# App Initialization
# ===========================

# ===========================
# API Key Authentication
# ===========================
# Every request must include header:  X-API-Key: <your-key>
#
# Set the expected key via environment variable before running:
#   export API_KEY="generate-a-long-random-string-here"
#
# Generate a strong key with:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"

API_KEY = os.environ.get("API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(provided_key: str = Security(api_key_header)):
    if not API_KEY:
        # Fail closed: if no key is configured server-side, refuse all
        # requests rather than silently running unauthenticated.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server misconfiguration: API_KEY environment variable is not set.",
        )

    if not provided_key or provided_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Include header: X-API-Key",
        )

    return provided_key


# Applying this dependency at the app level protects every route,
# including ones added later, without repeating `Depends(...)` on each.
app = FastAPI(
    title="AI Kubernetes Incident Analyzer",
    dependencies=[Depends(verify_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── NOTE on container health checks ──
# Every route below — including /health — now requires the X-API-Key
# header. If you later run this behind Docker/Kubernetes health probes
# (which can't send custom headers), either:
#   (a) configure the probe to send the header via a wrapper script, or
#   (b) carve out a small unauthenticated FastAPI sub-app for /health only.
# Left as-is here since this app isn't yet deployed behind orchestrator
# probes, and full lockdown was the explicit requirement.

# Load Kubernetes configuration.
#
# Locally (outside Docker): reads the default ~/.kube/config automatically.
#
# Inside a Docker container: there is no "local" kubeconfig unless you
# mount one in. docker-compose.yml mounts your Windows kubeconfig to
# /home/appuser/.kube/config and sets KUBECONFIG to point at it.
#
# Note: the mounted kubeconfig's server address gets rewritten (by
# generate-docker-kubeconfig.ps1) from 127.0.0.1 to host.docker.internal
# so it's reachable from inside the container. But Docker Desktop's
# Kubernetes API certificate is issued for a specific set of hostnames
# (which vary by Docker Desktop version, e.g. kubernetes.docker.internal)
# that don't necessarily include host.docker.internal. Rather than chase
# whichever hostname a given Docker Desktop version happens to use, we
# disable TLS hostname/cert verification for this client only — reasonable
# for a local, single-machine dev cluster reachable only via a Docker
# Desktop-managed alias.
kubeconfig_path = os.environ.get("KUBECONFIG")
if kubeconfig_path:
    config.load_kube_config(config_file=kubeconfig_path)
else:
    config.load_kube_config()

k8s_configuration = client.Configuration.get_default_copy()
k8s_configuration.verify_ssl = False
client.Configuration.set_default(k8s_configuration)

# Suppress the resulting "Unverified HTTPS request" warnings — expected
# and harmless given verify_ssl is disabled intentionally above.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

v1 = client.CoreV1Api()


# ===========================
# Secrets — loaded from environment, never hardcoded
# ===========================
# Set these before running the app, e.g.:
#   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
#   export EMAIL_SENDER="you@gmail.com"
#   export EMAIL_PASSWORD="your-gmail-app-password"
#   export EMAIL_RECEIVER="alerts@yourteam.com"

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "OPS")


def send_slack_alert(pod_name, namespace, severity, analysis):
    if not SLACK_WEBHOOK_URL:
        print("Slack alert skipped — SLACK_WEBHOOK_URL not configured.")
        return

    try:
        emoji_map = {
            "Critical": "🚨",
            "High": "🔴",
            "Medium": "🟠",
            "Low": "🟢",
        }
        emoji = emoji_map.get(severity, "⚪")

        message = f"""
{emoji} *AI Kubernetes Incident Alert*

*Namespace:* `{namespace}`
*Pod:* `{pod_name}`
*Severity:* *{severity}*

*AI RCA Summary:*
```{analysis[:700]}```
"""
        requests.post(SLACK_WEBHOOK_URL, json={"text": message}, timeout=10)
        print("Slack Alert Sent Successfully")

    except Exception as e:
        print("Slack Alert Failed:", e)


def send_email_alert(pod_name, namespace, severity, analysis):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
        print("Email alert skipped — EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVER not configured.")
        return

    try:
        subject = f"[{severity}] Kubernetes Incident Alert"

        body = f"""Kubernetes Incident Detected

Namespace : {namespace}
Pod Name  : {pod_name}
Severity  : {severity}

AI RCA:
{analysis}
"""
        message = MIMEMultipart()
        message["From"] = EMAIL_SENDER
        message["To"] = EMAIL_RECEIVER
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, message.as_string())
        server.quit()

        print(f"Email Alert Sent for {pod_name}")

    except Exception as e:
        print(f"Email Sending Failed: {e}")


def create_jira_ticket(pod_name, namespace, severity, analysis):
    """
    Creates a Jira issue for the incident. Returns the ticket key (e.g. "OPS-123")
    or None if Jira is not configured / the call fails.
    """
    if not JIRA_BASE_URL or not JIRA_EMAIL or not JIRA_API_TOKEN:
        print("Jira ticket skipped — JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN not configured.")
        return None

    try:
        url = f"{JIRA_BASE_URL}/rest/api/2/issue"

        payload = {
            "fields": {
                "project": {"key": JIRA_PROJECT_KEY},
                "summary": f"[{severity}] Incident in pod {pod_name} ({namespace})",
                "description": analysis[:2000],
                "issuetype": {"name": "Bug"},
            }
        }

        response = requests.post(
            url,
            json=payload,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()

        ticket_key = response.json().get("key")
        print(f"Jira ticket created: {ticket_key}")
        return ticket_key

    except Exception as e:
        print("Jira Ticket Creation Failed:", e)
        return None


# ===========================
# Prometheus / MongoDB
# ===========================

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

def extract_suggested_fix(analysis_text: str) -> str:
    """Pulls the 'Suggested Fix' section out of the AI's analysis text, so
    it can be shown/copied separately from the full report (e.g. as a
    ready-to-run kubectl command). Best-effort text parsing since the LLM's
    exact formatting can vary slightly -- returns "" if no such section is
    found, which callers should treat as "nothing to show", not an error."""
    match = re.search(
        r"Suggested Fix.*?:\s*(.*?)(?:\n\s*\n[A-Z][A-Za-z /]*:|\Z)",
        analysis_text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return ""


prom = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)


def get_cluster_metrics_context() -> str:
    """Cluster-wide CPU/memory snapshot, for prompts that have no specific
    pod to scope to (e.g. the raw paste-your-own-logs endpoint). Best-effort:
    if Prometheus has no data for a query, that line is simply left out
    rather than failing the whole analysis."""
    lines = []
    try:
        cpu = prom.custom_query(
            '100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        )
        if cpu:
            lines.append(f"- Node CPU usage (cluster avg): {float(cpu[0]['value'][1]):.1f}%")
    except Exception:
        pass
    try:
        mem = prom.custom_query(
            "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
        )
        if mem:
            lines.append(f"- Node memory usage (cluster avg): {float(mem[0]['value'][1]):.1f}%")
    except Exception:
        pass

    if not lines:
        return "No Prometheus metrics available at this time."
    return "\n".join(lines)


def get_pod_metrics_context(namespace: str, pod_name: str) -> str:
    """Snapshot of node + pod-level metrics at analysis time, so the AI has
    real numbers to reason with instead of guessing from logs alone.
    Best-effort: missing metrics (e.g. cAdvisor not scraped, or the pod
    already gone by the time we query) are simply omitted, not fatal."""
    lines = get_cluster_metrics_context().split("\n")
    if lines == ["No Prometheus metrics available at this time."]:
        lines = []

    try:
        pod_cpu = prom.custom_query(
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod_name}"}}[5m]))'
        )
        if pod_cpu and pod_cpu[0]["value"][1] is not None:
            lines.append(f"- This pod's CPU usage: {float(pod_cpu[0]['value'][1]):.3f} cores")
    except Exception:
        pass

    try:
        pod_mem = prom.custom_query(
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod_name}"}})'
        )
        if pod_mem and pod_mem[0]["value"][1] is not None:
            mb = float(pod_mem[0]["value"][1]) / (1024 * 1024)
            lines.append(f"- This pod's memory usage: {mb:.1f} MiB")
    except Exception:
        pass

    try:
        restarts = prom.custom_query(
            f'kube_pod_container_status_restarts_total{{namespace="{namespace}", pod="{pod_name}"}}'
        )
        if restarts:
            total_restarts = sum(float(r["value"][1]) for r in restarts)
            lines.append(f"- Total container restarts (kube-state-metrics): {int(total_restarts)}")
    except Exception:
        pass

    if not lines:
        return "No Prometheus metrics available for this pod/node at this time."
    return "\n".join(lines)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["incident_db"]
incident_collection = db["incidents"]


# ===========================
# Request Models
# ===========================

class LogRequest(BaseModel):
    logs: str


class PodRequest(BaseModel):
    namespace: str
    pod_name: str


# ===========================
# Basic Endpoints
# ===========================

@app.get("/")
def home():
    return {"message": "AI Kubernetes Incident Analyzer Running"}


@app.get("/health")
def health():
    return {"status": "UP"}


# ===========================
# Manual Log Analysis
# ===========================

@app.post("/analyze")
def analyze_logs(request: LogRequest):
    metrics_context = get_cluster_metrics_context()
    prompt = f"""You are a Senior Site Reliability Engineer.

Analyze the following Kubernetes logs. Use the cluster metrics below as
supporting evidence alongside the logs -- do not invent additional metrics
beyond what's given.

Cluster Metrics (from Prometheus, at time of analysis):
{metrics_context}

Provide:
1. Root Cause
2. Severity
3. Resolution
4. Preventive Action

Logs:
{request.logs}
"""
    response = ollama_client.chat(model="llama3", messages=[{"role": "user", "content": prompt}])
    return {"analysis": response["message"]["content"]}


# ===========================
# Analyze Selected Pod
# ===========================

@app.post("/analyze-pod")
def analyze_pod(request: PodRequest):
    try:
        logs = v1.read_namespaced_pod_log(
            name=request.pod_name,
            namespace=request.namespace,
            tail_lines=100,
        )

        metrics_context = get_pod_metrics_context(request.namespace, request.pod_name)

        prompt = f"""You are an expert Senior Site Reliability Engineer.

Analyze the following Kubernetes pod logs. Use the metrics below as
supporting evidence alongside the logs -- do not invent additional metrics
beyond what's given.

Pod Metrics (from Prometheus, at time of analysis):
{metrics_context}

Respond STRICTLY in the following format:

Root Cause:
<root cause>

Severity:
<Critical/High/Medium/Low>

Resolution:
<resolution>

Preventive Action:
<preventive action>

Suggested Fix (an exact, safe kubectl command to run, or "No safe automated fix -- manual investigation required" if none applies):
<command or explanation>

Escalation Required:
<Yes/No>

Logs:
{logs}
"""
        response = ollama_client.chat(model="llama3", messages=[{"role": "user", "content": prompt}])
        analysis = response["message"]["content"]
        analysis_lower = analysis.lower()

        if "critical" in analysis_lower:
            severity = "Critical"
        elif "high" in analysis_lower:
            severity = "High"
        elif "medium" in analysis_lower:
            severity = "Medium"
        elif "low" in analysis_lower:
            severity = "Low"
        else:
            severity = "Unknown"

        print("Detected Severity:", severity)

        suggested_fix = extract_suggested_fix(analysis)

        incident = {
            "timestamp": datetime.now(),
            "namespace": request.namespace,
            "pod_name": request.pod_name,
            "severity": severity,
            "analysis": analysis,
            "metrics_snapshot": metrics_context,
            "suggested_fix": suggested_fix,
            "resolved": False,
            "resolved_at": None,
        }
        incident_collection.insert_one(incident)

        return {
            "status": "success",
            "analysis": analysis,
            "severity": severity,
            "suggested_fix": suggested_fix,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================
# List All Pods
# ===========================

@app.get("/pods")
def get_pods():
    pods = v1.list_pod_for_all_namespaces(watch=False)
    pod_list = []

    for pod in pods.items:
        status = pod.status.phase

        if pod.status.container_statuses:
            for container in pod.status.container_statuses:
                if container.state.waiting:
                    status = container.state.waiting.reason
                elif container.state.terminated:
                    status = container.state.terminated.reason
                elif container.restart_count >= 5:
                    status = f"Restarting ({container.restart_count})"

        pod_list.append({
            "namespace": pod.metadata.namespace,
            "pod_name": pod.metadata.name,
            "status": status,
        })

    return pod_list


@app.get("/cluster-metrics")
def cluster_metrics():
    pods = v1.list_pod_for_all_namespaces(watch=False)

    total = len(pods.items)
    running = 0
    unhealthy = 0
    total_restarts = 0

    for pod in pods.items:
        if pod.status.phase == "Running":
            running += 1

        if pod.status.container_statuses:
            for container in pod.status.container_statuses:
                total_restarts += container.restart_count
                if container.restart_count >= 5:
                    unhealthy += 1

    return {
        "total_pods": total,
        "running_pods": running,
        "unhealthy_pods": unhealthy,
        "total_restarts": total_restarts,
    }


@app.get("/prometheus-test")
def prometheus_test():
    try:
        metrics = prom.all_metrics()
        return {
            "status": "success",
            "total_metrics_found": len(metrics),
            "sample_metrics": metrics[:10],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/node-metrics")
def node_metrics():
    try:
        cpu_query = '100 - (avg by(instance)(irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        memory_query = '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'

        cpu = prom.custom_query(cpu_query)
        memory = prom.custom_query(memory_query)

        return {"cpu_usage": cpu, "memory_usage": memory}
    except Exception as e:
        return {"error": str(e)}


# ===========================
# Auto Detect Failed Pods
# ===========================

@app.get("/analyze-failed-pods")
def analyze_failed_pods():
    try:
        pods = v1.list_pod_for_all_namespaces(watch=False)
        reports = []

        for pod in pods.items:
            namespace = pod.metadata.namespace
            pod_name = pod.metadata.name
            pod_phase = pod.status.phase

            if not pod.status.container_statuses:
                continue

            for container in pod.status.container_statuses:
                waiting_reason = None
                if container.state.waiting:
                    waiting_reason = container.state.waiting.reason

                print(f"Pod={pod_name}, Restarts={container.restart_count}, Waiting={waiting_reason}")

                needs_analysis = (
                    waiting_reason in [
                        "CrashLoopBackOff",
                        "ImagePullBackOff",
                        "ErrImagePull",
                        "CreateContainerConfigError",
                        "CreateContainerError",
                    ]
                    or container.restart_count >= 1
                )

                if not needs_analysis:
                    continue

                # ── Avoid duplicate-incident spam ──────────────────────
                # Without this, a pod that restarted once, days ago, gets
                # re-logged as a "new" incident on every single scan cycle
                # forever (this scanner runs every 30s by default) --
                # inflating incident counts and making MTBF meaningless.
                # Only treat this as a new incident if the restart count
                # actually increased since we last recorded this pod, or
                # enough time has passed that it's worth a fresh reminder.
                last_incident = incident_collection.find_one(
                    {"namespace": namespace, "pod_name": pod_name},
                    sort=[("timestamp", -1)],
                )

                if last_incident:
                    last_restart_count = last_incident.get("restart_count", 0)
                    seconds_since_last = (
                        datetime.now() - last_incident["timestamp"]
                    ).total_seconds()
                    cooldown_seconds = int(
                        os.environ.get("INCIDENT_COOLDOWN_SECONDS", "900")
                    )  # 15 minutes by default

                    restart_count_increased = container.restart_count > last_restart_count
                    cooldown_elapsed = seconds_since_last >= cooldown_seconds

                    if not restart_count_increased and not cooldown_elapsed:
                        continue

                try:
                    logs = v1.read_namespaced_pod_log(
                        name=pod_name, namespace=namespace, tail_lines=100, previous=True
                    )
                except Exception:
                    try:
                        logs = v1.read_namespaced_pod_log(
                            name=pod_name, namespace=namespace, tail_lines=100
                        )
                    except Exception:
                        logs = (
                            "Insufficient logs available.\n\n"
                            "Possible reasons:\n"
                            "1. Container crashed before logs could be collected.\n"
                            "2. Previous container logs have already been deleted.\n"
                            "3. Container runtime could not retrieve logs."
                        )

                print("\n========== ACTUAL LOGS ==========")
                print(logs)
                print("=================================\n")

                metrics_context = get_pod_metrics_context(namespace, pod_name)
                print("\n========== METRICS CONTEXT ==========")
                print(metrics_context)
                print("======================================\n")

                prompt = f"""You are a Senior Site Reliability Engineer.

IMPORTANT RULES:
* Analyze ONLY the logs and metrics provided below.
* DO NOT invent or assume logs or metrics beyond what's given.
* If logs are empty, explicitly say "Insufficient logs available".
* Use the metrics as supporting evidence -- e.g. if the node/pod was under
  heavy CPU or memory pressure at the time, factor that into the root cause.

Provide:
Root Cause:
Severity:
Resolution:
Preventive Action:
Suggested Fix (an exact, safe kubectl command to run, or "No safe automated fix -- manual investigation required" if none applies):

Pod Metrics (from Prometheus, at time of analysis):
{metrics_context}

Logs:
{logs}
"""
                response = ollama_client.chat(model="llama3", messages=[{"role": "user", "content": prompt}])
                analysis = response["message"]["content"]
                analysis_lower = analysis.lower()

                if "critical" in analysis_lower:
                    severity = "Critical"
                elif "high" in analysis_lower:
                    severity = "High"
                elif "medium" in analysis_lower:
                    severity = "Medium"
                elif "low" in analysis_lower:
                    severity = "Low"
                else:
                    severity = "Unknown"

                suggested_fix = extract_suggested_fix(analysis)

                incident = {
                    "timestamp": datetime.now(),
                    "namespace": namespace,
                    "pod_name": pod_name,
                    "severity": severity,
                    "analysis": analysis,
                    "metrics_snapshot": metrics_context,
                    "suggested_fix": suggested_fix,
                    "restart_count": container.restart_count,
                    "resolved": False,
                    "resolved_at": None,
                }
                result = incident_collection.insert_one(incident)

                jira_ticket = None

                if severity == "Critical":
                    send_email_alert(pod_name=pod_name, namespace=namespace, severity=severity, analysis=analysis)

                if severity in ["Critical", "High"]:
                    send_slack_alert(pod_name, namespace, severity, analysis)

                if severity == "Critical":
                    jira_ticket = create_jira_ticket(
                        pod_name=pod_name, namespace=namespace, severity=severity, analysis=analysis
                    )

                if jira_ticket:
                    incident_collection.update_one(
                        {"_id": result.inserted_id},
                        {"$set": {"jira_ticket": jira_ticket}},
                    )

                reports.append({
                    "namespace": namespace,
                    "pod_name": pod_name,
                    "status": pod_phase,
                    "restart_count": container.restart_count,
                    "waiting_reason": waiting_reason,
                    "analysis": analysis,
                    "jira_ticket": jira_ticket,
                })

        return reports

    except Exception as e:
        print("Analyze Failed Pods Error:", e)
        return {"error": str(e)}


# ===========================
# Debug Endpoint
# ===========================

@app.get("/debug-pods")
def debug_pods():
    try:
        pods = v1.list_pod_for_all_namespaces(watch=False)
        output = []

        for pod in pods.items:
            pod_info = {
                "namespace": pod.metadata.namespace,
                "pod_name": pod.metadata.name,
                "phase": pod.status.phase,
                "containers": [],
            }

            if pod.status.container_statuses:
                for container in pod.status.container_statuses:
                    waiting_reason = None
                    terminated_reason = None

                    if container.state.waiting:
                        waiting_reason = container.state.waiting.reason
                    if container.state.terminated:
                        terminated_reason = container.state.terminated.reason

                    pod_info["containers"].append({
                        "container_name": container.name,
                        "restart_count": container.restart_count,
                        "waiting_reason": waiting_reason,
                        "terminated_reason": terminated_reason,
                    })

            output.append(pod_info)

        return output

    except Exception as e:
        return {"error": str(e)}


# ===========================
# Incident History
# NOTE: route is singular "incident-history" — make sure the
# frontend calls this exact path (it previously called the
# plural "incidents-history" and got a 404).
# ===========================

@app.get("/incident-history")
def incident_history():
    incidents = []

    for incident in incident_collection.find().sort("timestamp", -1):
        incidents.append({
            "id": str(incident["_id"]),
            "timestamp": str(incident["timestamp"]),
            "namespace": incident["namespace"],
            "pod_name": incident["pod_name"],
            "severity": incident.get("severity", "Unknown"),
            "analysis": incident["analysis"],
            "jira_ticket": incident.get("jira_ticket", "N/A"),
            "metrics_snapshot": incident.get("metrics_snapshot", ""),
            "suggested_fix": incident.get("suggested_fix", ""),
            "resolved": incident.get("resolved", False),
            "resolved_at": str(incident["resolved_at"]) if incident.get("resolved_at") else None,
        })

    return incidents


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str):
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        obj_id = ObjectId(incident_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid incident id")

    incident = incident_collection.find_one({"_id": obj_id})
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    now = datetime.now()
    incident_collection.update_one(
        {"_id": obj_id},
        {"$set": {"resolved": True, "resolved_at": now}},
    )
    return {"status": "success", "resolved_at": str(now)}


@app.get("/severity-summary")
def severity_summary():
    incidents = list(incident_collection.find())

    summary = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Unknown": 0}

    for incident in incidents:
        severity = incident.get("severity", "Unknown")
        if severity in summary:
            summary[severity] += 1
        else:
            summary["Unknown"] += 1

    return summary


@app.get("/clear-incidents")
def clear_incidents():
    result = incident_collection.delete_many({})
    return {"deleted_count": result.deleted_count}


# ===========================
# PDF Report Download
# ===========================

@app.get("/download-report")
def download_report():
    incident = incident_collection.find_one(sort=[("timestamp", -1)])

    if not incident:
        return {"message": "No incidents found"}

    # Unique filename per request avoids race conditions between
    # concurrent calls overwriting the same incident_report.pdf
    filename = f"incident_report_{uuid.uuid4().hex[:8]}.pdf"

    doc = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("AI Kubernetes Incident Report", styles["Title"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"Namespace: {incident['namespace']}", styles["Normal"]))
    story.append(Paragraph(f"Pod Name: {incident['pod_name']}", styles["Normal"]))
    story.append(Paragraph(f"Severity: {incident['severity']}", styles["Normal"]))
    story.append(Paragraph(f"Timestamp: {incident['timestamp']}", styles["Normal"]))
    story.append(Spacer(1, 20))
    story.append(Paragraph(incident["analysis"].replace("\n", "<br/>"), styles["BodyText"]))

    doc.build(story)

    return FileResponse(
        filename,
        media_type="application/pdf",
        filename="incident_report.pdf",  # nice name shown to the user
    )


@app.get("/incident-trends")
def incident_trends():
    incidents = list(incident_collection.find())
    trends = {}

    for incident in incidents:
        date = incident["timestamp"].strftime("%Y-%m-%d")
        trends[date] = trends.get(date, 0) + 1

    output = [{"date": d, "incident_count": c} for d, c in trends.items()]
    return sorted(output, key=lambda x: x["date"])


@app.get("/severity-trends")
def severity_trends():
    incidents = list(incident_collection.find())
    trends = {}

    for incident in incidents:
        date = incident["timestamp"].strftime("%Y-%m-%d")
        severity = incident.get("severity", "Unknown")

        if date not in trends:
            trends[date] = {"date": date, "Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Unknown": 0}

        if severity in trends[date]:
            trends[date][severity] += 1
        else:
            trends[date]["Unknown"] += 1

    return sorted(list(trends.values()), key=lambda x: x["date"])


@app.get("/mttr-mtbf")
def mttr_mtbf():
    """
    MTTR (Mean Time To Resolution): average time between an incident being
    detected and someone marking it resolved via /incidents/{id}/resolve.
    Only incidents that have actually been marked resolved count towards
    this average -- it reflects real human response time, not an estimate.

    MTBF (Mean Time Between Failures): average time gap between consecutive
    incidents, cluster-wide. Uses every incident's detection timestamp, so
    it doesn't depend on anything being marked resolved.
    """
    all_incidents = list(incident_collection.find().sort("timestamp", 1))
    total_incidents = len(all_incidents)

    # ---- MTTR ----
    resolved = [i for i in all_incidents if i.get("resolved") and i.get("resolved_at")]
    if resolved:
        total_seconds = sum(
            (i["resolved_at"] - i["timestamp"]).total_seconds() for i in resolved
        )
        mttr_minutes = round((total_seconds / len(resolved)) / 60, 1)
    else:
        mttr_minutes = None

    # ---- MTBF ----
    if total_incidents >= 2:
        timestamps = [i["timestamp"] for i in all_incidents]
        gaps_seconds = [
            (timestamps[i + 1] - timestamps[i]).total_seconds()
            for i in range(len(timestamps) - 1)
        ]
        mtbf_hours = round((sum(gaps_seconds) / len(gaps_seconds)) / 3600, 2)
    else:
        mtbf_hours = None

    return {
        "total_incidents": total_incidents,
        "resolved_incidents": len(resolved),
        "mttr_minutes": mttr_minutes,
        "mtbf_hours": mtbf_hours,
    }


# ==================================
# Scheduled Incident Scanner
# ==================================

scheduler_status = {"status": "Not Started", "last_run": "Never"}


@app.get("/scheduler-status")
def get_scheduler_status():
    return scheduler_status


def scheduled_incident_scan():
    global scheduler_status

    print("Scheduler Function Triggered")
    scheduler_status["status"] = "Running"

    try:
        analyze_failed_pods()
        scheduler_status["status"] = "Completed"
        scheduler_status["last_run"] = str(datetime.now())
        print("Scheduled Scan Completed")

    except Exception as e:
        scheduler_status["status"] = f"Failed: {str(e)}"
        print("Scheduled Scan Failed:", e)


@app.on_event("startup")
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduled_incident_scan,
        "interval",
        seconds=int(os.environ.get("SCAN_INTERVAL_SECONDS", "30")),
    )
    scheduler.start()
    app.state.scheduler = scheduler

    atexit.register(lambda: scheduler.shutdown(wait=False))
    print("✅ Background Incident Scanner Started")


@app.on_event("shutdown")
def shutdown_scheduler():
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.shutdown()
