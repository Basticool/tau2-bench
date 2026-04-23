"""Main labeling interface.

Layout
------
Sidebar : norm selector with per-norm progress badges.
Main    : proposition legend (collapsible) → one bordered container per
          message, showing full content on the left and prop checkboxes on
          the right → Save & Next button.

After all traces for a norm are labeled, the user is prompted to review
and optionally update proposition descriptions + examples in-place.
"""
from __future__ import annotations

import json

import streamlit as st

from app.config import JOBS_DIR, LABELS_DIR
from app.modules.job_manager import (
    get_completed_sim_ids_job,
    get_completed_sim_ids_simple,
    get_user_jobs,
    is_norm_complete_job,
    is_norm_complete_simple,
    save_simple_label,
    save_unit_labels,
)
from app.modules.storage import now_iso, write_json


# ── Display helpers ────────────────────────────────────────────────────────────

def _role_badge(role: str) -> str:
    return {"assistant": "🤖 assistant", "user": "👤 user", "tool": "🔧 tool"}.get(
        role, role
    )


def _short_prop(prop_id: str) -> str:
    """Abbreviate a proposition ID for use as a compact checkbox label."""
    for prefix in ("agent_called_", "agent_", "auth_tool_", "user_", "order_"):
        if prop_id.startswith(prefix):
            return prop_id[len(prefix):]
    return prop_id


def _render_message_content(msg: dict) -> None:
    """Render the full content of a single message."""
    role = msg.get("role", "")
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or []

    if role == "assistant" and tool_calls:
        for tc in tool_calls:
            name = tc.get("name", "?")
            args = tc.get("arguments") or {}
            st.code(
                f"{name}(\n"
                + ",\n".join(f"  {k} = {json.dumps(v)}" for k, v in args.items())
                + "\n)",
                language="python",
            )
        if content:
            st.write(content)
    elif role == "tool":
        # Try to pretty-print JSON tool results
        try:
            parsed = json.loads(content)
            st.json(parsed, expanded=False)
        except (json.JSONDecodeError, TypeError):
            st.text(content)
    else:
        st.write(content or "*(empty)*")


# ── State helpers ──────────────────────────────────────────────────────────────

def _chk_key(norm_id: str, sim_id: str, orig_i: int, prop_id: str) -> str:
    return f"chk_{norm_id}_{sim_id}_{orig_i}_{prop_id}"


def _get_completed_ids(norm_id: str, app_mode: str, ss: dict) -> set[str]:
    if app_mode == "multi_user":
        jobs = get_user_jobs(ss.get("username", ""), JOBS_DIR)
        for job in jobs:
            if norm_id in job.get("norm_ids", []):
                return get_completed_sim_ids_job(job["job_id"], norm_id, JOBS_DIR)
        return set()
    return get_completed_sim_ids_simple(LABELS_DIR, norm_id)


def _save_labels(
    norm_id: str, sim_id: str, turns: list[dict], app_mode: str, ss: dict
) -> None:
    if app_mode == "multi_user":
        jobs = get_user_jobs(ss.get("username", ""), JOBS_DIR)
        for job in jobs:
            if norm_id in job.get("norm_ids", []):
                save_unit_labels(
                    job["job_id"], sim_id, norm_id, turns,
                    ss.get("username", ""), JOBS_DIR,
                )
                return
    else:
        save_simple_label(LABELS_DIR, norm_id, sim_id, turns)


# ── Post-norm proposition editor ───────────────────────────────────────────────

def _render_post_norm_editor(norm_id: str) -> None:
    ss = st.session_state
    propositions: dict = ss["propositions"]
    norm_props: list[str] = ss["norm_props"].get(norm_id, [])
    from app.config import DEFAULT_PROPS_PATH

    st.success(f"All traces for **{norm_id}** are labeled!")
    st.markdown("### Review proposition descriptions")
    st.caption(
        "For each proposition below, review its description and examples. "
        "Edit them if you want to improve them, then click **Save updates**. "
        "Leave as-is and click **Done** to skip."
    )

    updated: dict[str, dict] = {}
    for prop_id in norm_props:
        defn = propositions.get(prop_id, {})
        with st.expander(f"**{prop_id}**", expanded=True):
            ap_kind = defn.get("metadata", {}).get("ap_kind", "")
            st.caption(f"ap_kind: `{ap_kind}`")

            new_desc = st.text_area(
                "Description",
                value=defn.get("description", ""),
                height=100,
                key=f"desc_{norm_id}_{prop_id}",
            )

            examples_raw = json.dumps(defn.get("examples", []), indent=2)
            new_examples_raw = st.text_area(
                "Examples (JSON list)",
                value=examples_raw,
                height=150,
                key=f"ex_{norm_id}_{prop_id}",
            )
            updated[prop_id] = {"desc": new_desc, "examples_raw": new_examples_raw}

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("Save updates", type="primary"):
            all_ok = True
            for prop_id, vals in updated.items():
                try:
                    new_examples = json.loads(vals["examples_raw"])
                except json.JSONDecodeError:
                    st.error(f"Invalid JSON in examples for **{prop_id}**.")
                    all_ok = False
                    continue
                if prop_id in propositions:
                    propositions[prop_id]["description"] = vals["desc"]
                    propositions[prop_id]["examples"] = new_examples
            if all_ok:
                write_json(DEFAULT_PROPS_PATH, propositions)
                ss["propositions"] = propositions
                st.toast("Proposition descriptions saved.", icon="✅")
                ss[f"props_edited_{norm_id}"] = True
                ss.pop("post_norm_editing", None)
                st.rerun()
    with col2:
        if st.button("Done (no changes)"):
            ss[f"props_edited_{norm_id}"] = True
            ss.pop("post_norm_editing", None)
            st.rerun()


# ── Main render ────────────────────────────────────────────────────────────────

def render() -> None:
    ss = st.session_state
    norm_traces: dict = ss["norm_traces"]
    norms: dict = ss["norms"]
    propositions: dict = ss["propositions"]
    norm_props: dict = ss["norm_props"]
    tool_call_props: dict = ss["tool_call_props"]
    obs_prop_ids: set = ss["obs_prop_ids"]
    norms_with_obs: set = ss["norms_with_obs"]
    norm_auto_labels: dict = ss["norm_auto_labels"]
    app_mode: str = ss.get("app_mode", "simple")

    # Only norms that contain at least one observation prop need labeling
    available_norms = [n for n in norm_traces if n in norms_with_obs]
    if not available_norms:
        st.warning("No traces found in the dataset.")
        return

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("Norm AP Labeler")

        if app_mode == "multi_user":
            st.caption(f"Logged in as **{ss.get('username')}**")
            if st.button("Logout", key="logout_btn"):
                del ss["username"]
                st.rerun()
            st.divider()

            jobs = get_user_jobs(ss.get("username", ""), JOBS_DIR)
            assigned_norms: list[str] = []
            for job in jobs:
                for nid in job.get("norm_ids", []):
                    if nid not in assigned_norms and nid in norm_traces:
                        assigned_norms.append(nid)
            if not assigned_norms:
                st.info("No norms assigned to you yet. Ask an admin.")
                return
            available_norms = assigned_norms

        def _norm_label(nid: str) -> str:
            n_total = len(norm_traces.get(nid, []))
            completed = _get_completed_ids(nid, app_mode, ss)
            n_done = len(completed)
            icon = "✓" if n_done >= n_total else ("◑" if n_done > 0 else "○")
            return f"{icon} {nid}  ({n_done}/{n_total})"

        selected_norm = st.radio(
            "Norms:",
            available_norms,
            format_func=_norm_label,
            key="norm_selector",
        )

        if ss.get("_active_norm") != selected_norm:
            ss["_active_norm"] = selected_norm
            ss.pop("post_norm_editing", None)

    norm_id: str = selected_norm  # type: ignore[assignment]
    traces = norm_traces.get(norm_id, [])
    n_total = len(traces)
    props: list[str] = norm_props.get(norm_id, [])

    # ── Post-norm editing mode ─────────────────────────────────────────────────
    if ss.get("post_norm_editing") == norm_id:
        _render_post_norm_editor(norm_id)
        return

    # ── Norm header ────────────────────────────────────────────────────────────
    norm_meta = norms.get(norm_id, {}).get("metadata", {})
    st.subheader(f"Norm: `{norm_id}`")
    if norm_meta.get("description"):
        st.caption(norm_meta["description"])

    # ── Proposition legend ─────────────────────────────────────────────────────
    if props:
        with st.expander("Proposition descriptions (click to expand)", expanded=False):
            for prop_id in props:
                defn = propositions.get(prop_id, {})
                meta = defn.get("metadata", {})
                ap_kind = meta.get("ap_kind", "?")
                auto = prop_id in tool_call_props
                badge = " *(auto-labeled)*" if auto else ""
                st.markdown(f"**`{prop_id}`**{badge} — `{ap_kind}`")
                st.write(defn.get("description", "—"))
                rule = meta.get("grounding_rule", "")
                if rule:
                    st.caption(f"Rule: {rule}")
                st.divider()

    # ── Find next pending trace ────────────────────────────────────────────────
    completed_ids = _get_completed_ids(norm_id, app_mode, ss)
    pending = [
        (i, t) for i, t in enumerate(traces)
        if t.get("simulation", {}).get("id", "") not in completed_ids
    ]

    n_done = n_total - len(pending)
    st.progress(n_done / n_total if n_total else 1.0, text=f"{n_done}/{n_total} traces labeled")

    if not pending:
        if not ss.get(f"props_edited_{norm_id}"):
            st.success(f"All {n_total} traces labeled! Proceeding to proposition review…")
            ss["post_norm_editing"] = norm_id
            st.rerun()
        else:
            st.success(f"All {n_total} traces for **{norm_id}** are labeled and reviewed.")
        return

    trace_pos, trace = pending[0]
    sim = trace.get("simulation", {})
    sim_id = sim.get("id", "")
    messages = sim.get("messages", [])
    task_info = trace.get("task", {})

    # ── Trace header ───────────────────────────────────────────────────────────
    st.markdown(
        f"**Trace {trace_pos + 1} of {n_total}**"
        f"&nbsp;|&nbsp; task: `{task_info.get('task_id', sim_id)}`"
    )
    if task_info.get("instruction"):
        st.caption(f"Goal: {task_info['instruction']}")

    # ── Build display message list (skip system) ───────────────────────────────
    auto_labels_for_sim = norm_auto_labels.get(norm_id, {}).get(sim_id, [])
    display_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") != "system"]

    if not display_msgs:
        st.warning("No displayable messages in this trace.")
        if st.button("Skip trace"):
            _save_labels(norm_id, sim_id, [], app_mode, ss)
            st.rerun()
        return

    indexed_auto: list[dict[str, bool]] = [
        auto_labels_for_sim[orig_i] if orig_i < len(auto_labels_for_sim) else {}
        for orig_i, _ in display_msgs
    ]

    # ── Column header legend ───────────────────────────────────────────────────
    st.caption(
        "Left: full message content. Right: check **observation** propositions that hold "
        "at this turn. 🔒 props are auto-labeled from tool calls (shown for reference)."
    )

    # observation props → manual checkboxes
    # tool_call props   → shown locked (auto-labeled, for reference)
    # all other kinds   → not shown, not saved
    manual_props = [p for p in props if p in obs_prop_ids]
    auto_props   = [p for p in props if p in tool_call_props]

    # ── Per-message labeling rows ──────────────────────────────────────────────
    for i, (orig_i, msg) in enumerate(display_msgs):
        role = msg.get("role", "")
        turn_idx = msg.get("turn_idx", orig_i)
        msg_auto = indexed_auto[i]

        with st.container(border=True):
            col_content, col_checks = st.columns([3, 2])

            with col_content:
                st.caption(f"Turn {turn_idx} · {_role_badge(role)}")
                _render_message_content(msg)

            with col_checks:
                # Auto-labeled props first (locked)
                if auto_props:
                    st.caption("🔒 auto-labeled")
                    for prop_id in auto_props:
                        val = bool(msg_auto.get(prop_id, False))
                        st.checkbox(
                            _short_prop(prop_id),
                            value=val,
                            disabled=True,
                            help=prop_id,
                            key=_chk_key(norm_id, sim_id, orig_i, prop_id),
                        )

                # Manual props
                if manual_props:
                    if auto_props:
                        st.divider()
                    st.caption("Label:")
                    for prop_id in manual_props:
                        st.checkbox(
                            _short_prop(prop_id),
                            value=False,
                            help=prop_id + " — " + propositions.get(prop_id, {}).get("description", ""),
                            key=_chk_key(norm_id, sim_id, orig_i, prop_id),
                        )

    # ── Action buttons ─────────────────────────────────────────────────────────
    st.divider()
    col_save, col_skip, _ = st.columns([1, 1, 6])
    with col_save:
        if st.button("Save & Next ▶", type="primary", key=f"save_{norm_id}_{sim_id}"):
            turns = []
            for i, (orig_i, msg) in enumerate(display_msgs):
                ap_labels: dict[str, bool] = {}
                # Auto props: read from precomputed auto_labels
                for prop_id in auto_props:
                    ap_labels[prop_id] = bool(indexed_auto[i].get(prop_id, False))
                # Manual props: read from session state (checkbox widget value)
                for prop_id in manual_props:
                    ap_labels[prop_id] = bool(
                        ss.get(_chk_key(norm_id, sim_id, orig_i, prop_id), False)
                    )
                turns.append({
                    "turn_idx": msg.get("turn_idx", orig_i),
                    "role": msg.get("role", ""),
                    "ap_labels": ap_labels,
                    "auto_labeled_props": auto_props,
                })
            _save_labels(norm_id, sim_id, turns, app_mode, ss)

            new_completed = _get_completed_ids(norm_id, app_mode, ss)
            if len(new_completed) >= n_total and not ss.get(f"props_edited_{norm_id}"):
                ss["post_norm_editing"] = norm_id
            st.rerun()

    with col_skip:
        if st.button("Skip ▷", key=f"skip_{norm_id}_{sim_id}"):
            _save_labels(norm_id, sim_id, [], app_mode, ss)
            st.rerun()
