import streamlit as st
from streamlit_autorefresh import st_autorefresh

import os
import requests
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

load_dotenv()  # reads .env in the current working directory into os.environ


# ==================================
# Configuration
# ==================================

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

# Must match the API_KEY in the backend's .env file.
API_KEY = os.environ.get("API_KEY", "")

API_HEADERS = {"X-API-Key": API_KEY}

st.set_page_config(
    page_title="AI K8s Incident Analyzer",
    page_icon="🚀",
    layout="wide"
)

if not API_KEY:
    st.error(
        "⚠️ API_KEY environment variable is not set for this Streamlit app. "
        "Every backend request will be rejected with 401 Unauthorized until "
        "you set it to match the backend's API_KEY."
    )

st_autorefresh(
    interval=30000,
    key="dashboard_refresh"
)


def fetch_data(endpoint):
    """Fetch JSON data from the backend API, returning None on failure."""
    try:
        response = requests.get(
            f"{BACKEND_URL}/{endpoint}",
            headers=API_HEADERS,
            timeout=10
        )

        if response.status_code == 401:
            st.error(
                f"🔒 Unauthorized calling /{endpoint} — the API_KEY set here doesn't "
                "match the backend's API_KEY (or it's missing on one side)."
            )
            return None

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        st.error(f"API Error ({endpoint}): {e}")
        return None
    except ValueError as e:
        st.error(f"Invalid response from ({endpoint}): {e}")
        return None


def render_severity_dashboard(chart_key: str):
    """Render the incident severity metrics + pie chart. Reusable, single source of truth."""
    st.markdown("---")
    st.header("🚨 Incident Severity Dashboard")

    summary = fetch_data("severity-summary")

    if not summary:
        st.info("No severity data available.")
        return

    metric_config = [
        ("🔴 Critical", "Critical"),
        ("🟠 High", "High"),
        ("🟡 Medium", "Medium"),
        ("🟢 Low", "Low"),
        ("⚪ Unknown", "Unknown"),
    ]

    cols = st.columns(len(metric_config))
    for col, (label, key) in zip(cols, metric_config):
        with col:
            st.metric(label=label, value=summary.get(key, 0))

    severity_df = pd.DataFrame(
        {"Severity": list(summary.keys()), "Count": list(summary.values())}
    )

    fig = px.pie(
        severity_df,
        names="Severity",
        values="Count",
        title="Incident Severity Distribution",
        hole=0.4,
    )
    fig.update_layout(legend_title="Severity", margin=dict(t=50, b=20))

    st.plotly_chart(fig, use_container_width=True, key=chart_key)


# ==================================
# Sidebar
# ==================================

st.sidebar.title("🚀 AI K8s Analyzer")

page = st.sidebar.radio(
    "📌 Navigation",
    [
        "Overview",
        "Pod Analyzer",
        "Incidents",
        "Analytics",
        "MTTR / MTBF",
        "Reports",
        "Settings",
    ],
)

st.title("🚀 AI-Powered Kubernetes Incident Analyzer")
st.markdown("---")


# ==================================
# OVERVIEW PAGE
# ==================================

if page == "Overview":

    st.header("📊 Kubernetes Cluster Overview")

    pods = fetch_data("pods")
    metrics = fetch_data("cluster-metrics")

    if pods and metrics:
        df = pd.DataFrame(pods)

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Pods", metrics.get("total_pods", 0))

        with col2:
            st.metric("Running Pods", metrics.get("running_pods", 0))

        with col3:
            st.metric("Unhealthy Pods", metrics.get("unhealthy_pods", 0))

        with col4:
            st.metric("Total Restarts", metrics.get("total_restarts", 0))

    render_severity_dashboard(chart_key="overview_severity_chart")


# ==================================
# POD ANALYZER PAGE
# ==================================

elif page == "Pod Analyzer":

    st.header("🤖 AI Pod Log Analyzer")

    pods = fetch_data("pods")

    if pods:
        df = pd.DataFrame(pods)

        if "namespace" not in df.columns or "pod_name" not in df.columns:
            st.error(
                "Unexpected API response: 'namespace' or 'pod_name' field missing "
                "from /pods endpoint."
            )
        else:
            namespaces = sorted(df["namespace"].unique())
            selected_namespace = st.selectbox("Select Namespace", namespaces)

            namespace_pods = df[df["namespace"] == selected_namespace]
            selected_pod = st.selectbox(
                "Select Pod", namespace_pods["pod_name"].tolist()
            )

            if st.button("Analyze Pod 🚀", key="analyze_single_pod"):
                with st.spinner("Analyzing logs using AI..."):
                    try:
                        payload = {
                            "namespace": selected_namespace,
                            "pod_name": selected_pod,
                        }

                        response = requests.post(
                            f"{BACKEND_URL}/analyze-pod",
                            json=payload,
                            headers=API_HEADERS,
                            timeout=60,
                        )
                        response.raise_for_status()
                        result = response.json()

                        if isinstance(result, dict) and "error" in result:
                            st.error(result["error"])
                        else:
                            st.success("Analysis Completed")
                            st.subheader("📋 AI RCA Report")
                            st.text_area(
                                "Analysis",
                                value=result.get("analysis", "No analysis available"),
                                height=400,
                                key="single_pod_analysis",
                            )

                            suggested_fix = result.get("suggested_fix")
                            if suggested_fix:
                                st.subheader("💡 AI Suggested Fix")
                                st.caption(
                                    "Review before running — this is a suggestion, "
                                    "not an automatic action."
                                )
                                st.code(suggested_fix, language="bash")

                    except requests.exceptions.RequestException as e:
                        st.error(f"Unable to analyze pod: {e}")
                    except ValueError as e:
                        st.error(f"Invalid response from backend: {e}")
    else:
        st.info("No pod data available.")


# ==================================
# INCIDENTS PAGE
# ==================================

elif page == "Incidents":

    st.header("📋 Recent Incidents")

    # Backend route is singular: /incident-history
    incidents = fetch_data("incident-history")

    if incidents:
        incidents_df = pd.DataFrame(incidents)

        display_cols = [
            c for c in
            ["timestamp", "namespace", "pod_name", "severity", "jira_ticket"]
            if c in incidents_df.columns
        ]

        st.dataframe(
            incidents_df[display_cols] if display_cols else incidents_df,
            use_container_width=True,
        )

        st.markdown("**🔍 View full AI analysis per incident**")
        for i, row in incidents_df.iterrows():
            resolved = row.get("resolved", False)
            status_icon = "✅" if resolved else "🔴"
            label = f"{status_icon} {row.get('pod_name', 'unknown')} — {row.get('severity', 'Unknown')} — {row.get('timestamp', '')}"
            with st.expander(label):
                st.text(row.get("analysis", "No analysis available"))

                suggested_fix = row.get("suggested_fix")
                if suggested_fix:
                    st.markdown("**💡 AI Suggested Fix**")
                    st.caption(
                        "Review before running — this is a suggestion, not an "
                        "automatic action."
                    )
                    st.code(suggested_fix, language="bash")

                metrics_snapshot = row.get("metrics_snapshot")
                if metrics_snapshot:
                    st.markdown("**📊 Metrics at time of incident**")
                    st.text(metrics_snapshot)

                incident_id = row.get("id")
                if resolved:
                    st.success(f"Resolved at {row.get('resolved_at', 'unknown time')}")
                elif incident_id:
                    if st.button("Mark Resolved ✅", key=f"resolve_{incident_id}"):
                        try:
                            resp = requests.post(
                                f"{BACKEND_URL}/incidents/{incident_id}/resolve",
                                headers=API_HEADERS,
                                timeout=15,
                            )
                            resp.raise_for_status()
                            st.success("Marked as resolved — refresh to update the list.")
                        except requests.exceptions.RequestException as e:
                            st.error(f"Could not mark resolved: {e}")
    else:
        st.info("No incident data available.")

    render_severity_dashboard(chart_key="incidents_severity_chart")


# ==================================
# ANALYTICS PAGE
# ==================================

elif page == "Analytics":

    st.header("📈 Incident Trend Dashboard")

    trend_data = fetch_data("incident-trends")

    if trend_data:
        trend_df = pd.DataFrame(trend_data)

        if "date" in trend_df.columns and "incident_count" in trend_df.columns:
            fig = px.line(
                trend_df,
                x="date",
                y="incident_count",
                markers=True,
                title="Incident Trend Over Time",
            )
            st.plotly_chart(fig, use_container_width=True, key="incident_trend_chart")
            st.metric("Total Historical Incident Days", len(trend_df))
        else:
            st.warning("Incident trend data is missing expected fields ('date', 'incident_count').")
    else:
        st.info("No incident trend data available.")

    render_severity_dashboard(chart_key="analytics_severity_chart")


# ==================================
# MTTR / MTBF PAGE
# ==================================

elif page == "MTTR / MTBF":

    st.header("⏱️ MTTR / MTBF Dashboard")
    st.caption(
        "MTTR (Mean Time To Resolution) measures how fast incidents get "
        "resolved once marked resolved on the Incidents page. MTBF (Mean "
        "Time Between Failures) measures how often incidents happen, "
        "cluster-wide."
    )

    metrics = fetch_data("mttr-mtbf")

    if metrics:
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Incidents", metrics.get("total_incidents", 0))

        with col2:
            st.metric("Resolved Incidents", metrics.get("resolved_incidents", 0))

        with col3:
            mttr = metrics.get("mttr_minutes")
            st.metric(
                "MTTR",
                f"{mttr} min" if mttr is not None else "N/A",
                help="Average time from detection to being marked resolved.",
            )

        with col4:
            mtbf = metrics.get("mtbf_hours")
            st.metric(
                "MTBF",
                f"{mtbf} hrs" if mtbf is not None else "N/A",
                help="Average time gap between consecutive incidents, cluster-wide.",
            )

        if metrics.get("resolved_incidents", 0) == 0:
            st.info(
                "No incidents have been marked resolved yet, so MTTR can't be "
                "calculated. Go to the Incidents page and click 'Mark Resolved ✅' "
                "on any incident you've addressed."
            )
    else:
        st.info("No incident data available yet.")


# ==================================
# REPORTS PAGE
# ==================================

elif page == "Reports":

    st.header("📄 Export Reports")

    if st.button("Generate PDF Report", key="generate_pdf"):
        try:
            response = requests.get(
                f"{BACKEND_URL}/download-report",
                headers=API_HEADERS,
                timeout=30,
            )

            if response.status_code == 200:
                st.download_button(
                    label="📥 Download Incident Report",
                    data=response.content,
                    file_name="incident_report.pdf",
                    mime="application/pdf",
                )
            else:
                st.error(
                    f"Failed to generate PDF report (status {response.status_code})."
                )

        except requests.exceptions.RequestException as e:
            st.error(f"Unable to generate report: {e}")


# ==================================
# SETTINGS PAGE
# ==================================

elif page == "Settings":

    # ──────────────────────────────────────────────
    # Grafana Dashboard
    # ──────────────────────────────────────────────
    st.header("📊 Grafana Monitoring Dashboard")

    st.info(
        """
        View advanced Kubernetes monitoring dashboards,
        Prometheus metrics, alerts, and cluster health in Grafana.
        """
    )

    col1, col2 = st.columns([2, 1])

    with col1:
        st.link_button("🚀 Open Grafana Dashboard", "http://localhost:3000")

    with col2:
        st.success("Grafana URL: localhost:3000")

    # ──────────────────────────────────────────────
    # Background Scheduler Status
    # ──────────────────────────────────────────────
    st.markdown("---")
    st.header("⏰ Background Incident Scanner")

    scheduler = fetch_data("scheduler-status")

    if scheduler:
        col1, col2 = st.columns(2)

        with col1:
            status = scheduler.get("status", "Unknown")
            st.metric("Current Status", status)

        with col2:
            last_run = scheduler.get("last_run", "Never")
            st.metric("Last Run", last_run)

        if status == "Running":
            st.success("✅ Background Incident Scanner is running.")
        elif status == "Completed":
            st.info("ℹ️ Last scheduled scan completed successfully.")
        else:
            st.warning("⚠️ Scheduler is currently not running.")
    else:
        st.info("Scheduler status unavailable.")