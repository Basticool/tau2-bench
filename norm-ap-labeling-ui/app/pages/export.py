"""Export labeled data.

Downloads a JSONL where each line is a completed work unit with all fields:
sim_id, norm_id, labeled_by, labeled_at, turns (with ap_labels per turn).
"""
from __future__ import annotations

import json

import streamlit as st

from app.config import JOBS_DIR, LABELS_DIR
from app.modules.job_manager import get_all_jobs, get_job_units
from app.modules.storage import read_jsonl


def _collect_simple() -> list[dict]:
    records = []
    norm_traces: dict = st.session_state.get("norm_traces", {})
    for norm_id in norm_traces:
        for rec in read_jsonl(LABELS_DIR / f"{norm_id}.jsonl"):
            if rec.get("unit_status") == "completed":
                records.append(rec)
    return records


def _collect_multi_user() -> list[dict]:
    records = []
    for job in get_all_jobs(JOBS_DIR):
        for unit in get_job_units(job["job_id"], JOBS_DIR):
            if unit.get("unit_status") == "completed":
                records.append(unit)
    return records


def render() -> None:
    app_mode = st.session_state.get("app_mode", "simple")
    st.title("Export labels")

    records = _collect_simple() if app_mode == "simple" else _collect_multi_user()

    if not records:
        st.info("No completed labels to export yet.")
        return

    st.write(f"**{len(records)}** completed label records across all norms.")

    # Summary table
    from collections import defaultdict
    by_norm: dict[str, int] = defaultdict(int)
    for r in records:
        by_norm[r.get("norm_id", "?")] += 1
    import pandas as pd
    st.dataframe(
        pd.DataFrame(
            [{"norm_id": k, "labeled_traces": v} for k, v in sorted(by_norm.items())]
        ),
        hide_index=True,
        use_container_width=True,
    )

    jsonl_bytes = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode()
    st.download_button(
        label="Download all labels (.jsonl)",
        data=jsonl_bytes,
        file_name="norm_ap_labels.jsonl",
        mime="application/x-ndjson",
    )
