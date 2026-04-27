"""Load all data sources once per session and cache in st.session_state."""
from __future__ import annotations

import sys

import streamlit as st


def run_startup(app_mode: str) -> None:
    if st.session_state.get("_startup_done"):
        return

    from app.config import (
        DEFAULT_NORMS_PATH,
        DEFAULT_PROPS_PATH,
        DEFAULT_TRACES_PATH,
        JOBS_DIR,
        LABELS_DIR,
    )
    from app.modules.auto_labeler import build_auto_label_sensors, compute_auto_labels
    from app.modules.data_loader import (
        group_traces_by_norm,
        load_norms,
        load_propositions,
        load_traces,
    )
    from app.modules.norm_utils import get_norm_props
    from app.modules.storage import ensure_dir, now_iso, read_jsonl, write_jsonl

    ensure_dir(LABELS_DIR)
    ensure_dir(JOBS_DIR)

    with st.spinner("Loading data…"):
        traces = load_traces(DEFAULT_TRACES_PATH)
        norms = load_norms(DEFAULT_NORMS_PATH)
        propositions = load_propositions(DEFAULT_PROPS_PATH)

    norm_traces = group_traces_by_norm(traces)

    known_props = set(propositions.keys())
    norm_props: dict[str, list[str]] = {
        norm_id: sorted(get_norm_props(norms[norm_id], known_props, all_norms=norms))
        for norm_id in norm_traces
        if norm_id in norms
    }

    # Which props are tool_call (auto-labeled)
    tool_call_props: dict[str, str] = {
        prop_id: defn["metadata"]["tool_name"]
        for prop_id, defn in propositions.items()
        if defn.get("metadata", {}).get("ap_kind") == "tool_call"
        and defn.get("metadata", {}).get("tool_name")
    }

    # Which props require manual labeling (only observation kind)
    obs_prop_ids: set[str] = {
        prop_id
        for prop_id, defn in propositions.items()
        if defn.get("metadata", {}).get("ap_kind") == "observation"
    }

    # Norms that have at least one observation prop → need labeling
    norms_with_obs: set[str] = {
        norm_id
        for norm_id, props_list in norm_props.items()
        if any(p in obs_prop_ids for p in props_list)
    }

    sensors = build_auto_label_sensors(propositions)

    # Pre-compute auto-labels: {norm_id: {sim_id: [{prop_id: bool} per message]}}
    with st.spinner("Pre-computing auto-labels…"):
        norm_auto_labels: dict[str, dict[str, list[dict[str, bool]]]] = {}
        for norm_id, trace_list in norm_traces.items():
            props_for_norm = norm_props.get(norm_id, [])
            norm_sensors = {p: sensors[p] for p in props_for_norm if p in sensors}
            sim_labels: dict[str, list[dict[str, bool]]] = {}
            for trace in trace_list:
                sim_id = trace.get("simulation", {}).get("id", "")
                messages = trace.get("simulation", {}).get("messages", [])
                sim_labels[sim_id] = compute_auto_labels(messages, norm_sensors)
            norm_auto_labels[norm_id] = sim_labels

    # Auto-save norms where every prop is tool_call (no human review needed).
    fully_auto_norms = [
        norm_id for norm_id in norm_traces
        if norm_id in norm_props and norm_id not in norms_with_obs
    ]
    if fully_auto_norms:
        with st.spinner("Auto-saving pre-labeled norms…"):
            for norm_id in fully_auto_norms:
                props_for_norm = norm_props.get(norm_id, [])
                auto_props = [p for p in props_for_norm if p in tool_call_props]
                labels_path = LABELS_DIR / f"{norm_id}.jsonl"

                existing = read_jsonl(labels_path)
                completed_sim_ids = {
                    rec["sim_id"] for rec in existing
                    if rec.get("unit_status") == "completed"
                }

                new_records = []
                for trace in norm_traces[norm_id]:
                    sim = trace.get("simulation", {})
                    sim_id = sim.get("id", "")
                    if sim_id in completed_sim_ids:
                        continue
                    messages = sim.get("messages", [])
                    auto_labels_for_sim = norm_auto_labels.get(norm_id, {}).get(sim_id, [])
                    turns = []
                    for orig_i, msg in enumerate(messages):
                        if msg.get("role") == "system":
                            continue
                        msg_auto = (
                            auto_labels_for_sim[orig_i]
                            if orig_i < len(auto_labels_for_sim)
                            else {}
                        )
                        turns.append({
                            "turn_idx": msg.get("turn_idx", orig_i),
                            "role": msg.get("role", ""),
                            "ap_labels": {p: bool(msg_auto.get(p, False)) for p in auto_props},
                            "auto_labeled_props": auto_props,
                        })
                    new_records.append({
                        "sim_id": sim_id,
                        "norm_id": norm_id,
                        "unit_status": "completed",
                        "labeled_by": "auto",
                        "labeled_at": now_iso(),
                        "turns": turns,
                    })

                if new_records:
                    write_jsonl(labels_path, existing + new_records)

    st.session_state.update({
        "traces": traces,
        "norms": norms,
        "propositions": propositions,
        "norm_traces": norm_traces,
        "norm_props": norm_props,
        "tool_call_props": tool_call_props,
        "obs_prop_ids": obs_prop_ids,
        "norms_with_obs": norms_with_obs,
        "norm_auto_labels": norm_auto_labels,
        "app_mode": app_mode,
        "_startup_done": True,
    })
