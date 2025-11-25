import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from scripts.aggregate_data_quality import load_reports  # noqa


def main():
    st.title("Data Quality Dashboard")
    report_dir = ROOT / "results" / "data_quality"
    rows = load_reports(report_dir)
    if not rows:
        st.warning(f"No reports found in {report_dir}")
        return
    df = pd.DataFrame(rows)
    df["generated_at"] = pd.to_datetime(df["generated_at"])
    df = df.sort_values("generated_at")

    severity_filter = st.multiselect("Severity", sorted(df["severity"].dropna().unique()), default=["warn", "error", "pass"])
    symbol_filter = st.multiselect("Symbol", sorted(df["symbol"].dropna().unique()), default=list(df["symbol"].dropna().unique()))

    filtered = df[df["severity"].isin(severity_filter) & df["symbol"].isin(symbol_filter)]
    st.dataframe(filtered[["generated_at", "symbol", "severity", "gap_ratio", "duplicate_timestamps", "hash"]].reset_index(drop=True))

    st.subheader("Gap Ratio Over Time")
    chart_data = filtered.set_index("generated_at")[["gap_ratio"]]
    st.line_chart(chart_data)

    st.subheader("Severity Counts")
    severity_counts = filtered.groupby(["severity"]).size()
    st.bar_chart(severity_counts)


if __name__ == "__main__":
    main()
