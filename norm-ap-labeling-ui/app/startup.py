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
        NORM_COMPLIANCE_REPO,
    )
    from app.modules.auto_labeler import build_auto_label_sensors, compute_auto_labels
    from app.modules.data_loader import (
        group_traces_by_norm,
        load_norms,
        load_propositions,
        load_traces,
    )
    from app.modules.norm_utils import get_norm_props
    from app.modules.storage import ensure_dir

    if NORM_COMPLIANCE_REPO not in sys.path:
        sys.path.insert(0, NORM_COMPLIANCE_REPO)

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
