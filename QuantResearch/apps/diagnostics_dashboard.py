#!/usr/bin/env python3
"""Streamlit app to browse diagnostics charts/metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st

DEFAULT_BASE = Path("charts")


def find_runs(base: Path) -> List[Path]:
    if not base.exists():
        return []
    return sorted(
        [p for p in base.glob("**/diagnostics_*") if p.is_dir()],
        key=lambda p: p.name,
        reverse=True,
    )


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - visualization only
        st.warning(f"Failed to parse {path}: {exc}")
        return {}


def display_artifacts(run_dir: Path, meta: Dict) -> None:
    st.subheader("Artifacts")
    artifacts = meta.get("artifacts", {})
    for label, rel_path in artifacts.items():
        if not rel_path:
            continue
        img_path = Path(rel_path)
        if not img_path.is_absolute():
            img_path = run_dir / Path(rel_path).name
        if not img_path.exists():
            st.write(f"{label}: missing ({img_path})")
            continue
        if img_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            st.image(str(img_path), caption=f"{label}: {img_path.name}")
        else:
            st.write(f"{label}: {img_path}")


def display_metrics(data_dump: Dict) -> None:
    if not data_dump:
        return
    st.subheader("Metrics Snapshot")
    if batch := data_dump.get("batch_heatmap"):
        st.write("Batch heatmap pivot (sample):")
        for key, pivot in batch.items():
            st.write(f"Scenario: {key}")
            df = pd.DataFrame(pivot)
            st.dataframe(df)
    if mc := data_dump.get("monte_carlo"):
        st.metric("Monte Carlo mean Sharpe", f"{mc.get('mean', 0):.3f}")
    if wf := data_dump.get("walkforward"):
        st.write("Walk-forward KPIs:")
        st.dataframe(pd.DataFrame(wf))


def render_risk_tab(base_dir: Path) -> None:
    st.subheader("Risk Metrics")
    csv_path = Path("results/risk/metrics.csv").expanduser()
    if not csv_path.exists():
        st.warning("results/risk/metrics.csv not found.")
        return
    df = pd.read_csv(csv_path)
    if df.empty:
        st.info("metrics.csv is empty.")
        return
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.date
    status_filter = st.multiselect("Status filter", options=sorted(df["status"].unique()), default=list(df["status"].unique()))
    start_date = st.date_input("Start date", value=df["date"].min())
    end_date = st.date_input("End date", value=df["date"].max())
    run_query = st.text_input("Run ID contains", "")
    filtered = df[df["status"].isin(status_filter)]
    if start_date:
        filtered = filtered[filtered["date"] >= start_date]
    if end_date:
        filtered = filtered[filtered["date"] <= end_date]
    if run_query:
        filtered = filtered[filtered["run_id"].astype(str).str.contains(run_query)]
    st.line_chart(filtered.set_index("timestamp")["rejects"])
    col1, col2, col3 = st.columns(3)
    col1.metric("Latest latency (avg)", f"{filtered['latency_ms_avg'].tail(1).iloc[0]:.1f} ms")
    col2.metric("Latest latency (p95)", f"{filtered['latency_ms_p95'].tail(1).iloc[0]:.1f} ms")
    col3.metric("Latest total PnL", f"{filtered['total_pnl'].tail(1).iloc[0]:.2f}")
    if not filtered.empty:
        st.plotly_chart(px.box(filtered, y="latency_ms_avg", title="Latency distribution"), use_container_width=True)
        st.plotly_chart(px.box(filtered, y="total_pnl", title="Total PnL distribution"), use_container_width=True)
    fail_df = df[df["status"].str.lower() == "fail"].tail(20)
    st.write("Slack Alert History (fail runs)")
    if fail_df.empty:
        st.info("No fail records.")
    else:
        st.dataframe(fail_df[["timestamp", "run_id", "rejects", "kills", "latency_ms_avg", "total_pnl", "max_drawdown_pct"]])
    st.write("Recent entries")
    st.dataframe(df.tail(20))
    st.download_button("Download metrics CSV", csv_path.read_bytes(), file_name="risk_metrics.csv")
    png = base_dir / "ci" / "risk_metrics.png"
    if png.exists():
        st.image(str(png), caption="Risk metrics chart")


def main() -> None:
    st.title("Diagnostics Dashboard")
    base_input = st.sidebar.text_input("Diagnostics base dir", str(DEFAULT_BASE))
    base_dir = Path(base_input).expanduser()
    tab = st.sidebar.radio("Panel", ["Diagnostics", "Risk"])

    if tab == "Risk":
        render_risk_tab(base_dir)
        return

    runs = find_runs(base_dir)
    if not runs:
        st.info(f"No diagnostics directories found under {base_dir}")
        return
    run_labels = [str(r.relative_to(base_dir)) for r in runs]
    selected_label = st.sidebar.selectbox("Select run", options=run_labels)
    run_dir = runs[run_labels.index(selected_label)]
    meta = load_json(run_dir / "diagnostics_metadata.json")
    data_dump = load_json(run_dir / "diagnostics_data.json")

    st.write(f"**Run directory:** {run_dir}")
    st.json(meta)
    display_metrics(data_dump)
    display_artifacts(run_dir, meta)


if __name__ == "__main__":
    main()
