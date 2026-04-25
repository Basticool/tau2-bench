"""Export labeled data.

Downloads a JSONL where each line is one (annotator, turn, AP) record:
  annotator_id, sample_id, target_ap, decision ∈ {yes, no, unsure}, timestamp_utc.

Auto-labeled (tool_call) props are excluded; only human-labeled observation props
are included.  sample_id encodes both the simulation and turn index:
  "<sim_id>:t<turn_idx>"
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd
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


def _flatten_to_schema(units: list[dict]) -> list[dict]:
    flat = []
    for unit in units:
        annotator_id = unit.get("labeled_by", "unknown")
        sim_id = unit.get("sim_id", "?")
        timestamp_utc = unit.get("labeled_at", "")
        for turn in unit.get("turns", []):
            turn_idx = turn.get("turn_idx")
            auto_props = set(turn.get("auto_labeled_props", []))
            for prop_id, decision in turn.get("ap_labels", {}).items():
                if prop_id in auto_props:
                    continue
                if isinstance(decision, bool):
                    decision = "yes" if decision else "no"
                flat.append({
                    "annotator_id": annotator_id,
                    "sample_id": f"{sim_id}:t{turn_idx}",
                    "target_ap": prop_id,
                    "decision": decision,
                    "timestamp_utc": timestamp_utc,
                })
    return flat


def render() -> None:
    app_mode = st.session_state.get("app_mode", "simple")
    st.title("Export labels")

    units = _collect_simple() if app_mode == "simple" else _collect_multi_user()

    if not units:
        st.info("No completed labels to export yet.")
        return

    flat = _flatten_to_schema(units)
    st.write(f"**{len(flat)}** label records across **{len(units)}** completed traces.")

    by_norm: dict[str, int] = defaultdict(int)
    for r in units:
        by_norm[r.get("norm_id", "?")] += 1
    st.dataframe(
        pd.DataFrame(
            [{"norm_id": k, "labeled_traces": v} for k, v in sorted(by_norm.items())]
        ),
        hide_index=True,
        use_container_width=True,
    )

    jsonl_bytes = "\n".join(json.dumps(r, ensure_ascii=False) for r in flat).encode()
    st.download_button(
        label="Download labels (.jsonl)",
        data=jsonl_bytes,
        file_name="norm_ap_labels.jsonl",
        mime="application/x-ndjson",
    )
