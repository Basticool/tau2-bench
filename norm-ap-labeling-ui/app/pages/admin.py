"""Admin panel — multi-user mode only.

Tabs
----
Users       : add / remove labeler accounts.
Allocate    : assign norms to users (creates a job per assignment).
Jobs        : view all jobs, inspect progress, delete jobs.
"""
from __future__ import annotations

import streamlit as st

from app.config import JOBS_DIR, USERS_FILE
from app.modules.job_manager import (
    create_job,
    delete_job,
    get_all_jobs,
    get_completed_sim_ids_job,
    get_job_units,
    update_job_status,
)
from app.modules.storage import append_jsonl, now_iso, read_jsonl, write_jsonl


# ── User helpers ───────────────────────────────────────────────────────────────

def _load_users() -> list[str]:
    return [r["username"] for r in read_jsonl(USERS_FILE)]


def _add_user(username: str) -> str | None:
    users = _load_users()
    if username in users:
        return f"User **{username}** already exists."
    append_jsonl(USERS_FILE, {"username": username, "created_at": now_iso()})
    return None


def _remove_user(username: str) -> str | None:
    if username == "admin":
        return "Cannot remove admin."
    jobs = [j for j in get_all_jobs(JOBS_DIR) if j["username"] == username and j["status"] != "completed"]
    if jobs:
        return f"User **{username}** has {len(jobs)} active job(s). Delete them first."
    records = read_jsonl(USERS_FILE)
    write_jsonl(USERS_FILE, [r for r in records if r["username"] != username])
    return None


# ── Render ─────────────────────────────────────────────────────────────────────

def render() -> None:
    if st.session_state.get("username") != "admin":
        st.error("Admin access only.")
        return

    st.title("Admin")
    tab_users, tab_alloc, tab_jobs = st.tabs(["Users", "Allocate Norms", "Jobs"])

    # ── Users tab ─────────────────────────────────────────────────────────────
    with tab_users:
        st.subheader("Manage users")
        users = _load_users()
        st.write(f"**{len(users)}** registered users: {', '.join(users)}")
        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            new_user = st.text_input("New username").strip()
            if st.button("Add user", key="add_user"):
                if not new_user:
                    st.error("Enter a username.")
                elif err := _add_user(new_user):
                    st.error(err)
                else:
                    st.success(f"Added **{new_user}**.")
                    st.rerun()

        with col2:
            del_user = st.selectbox(
                "Remove user",
                [u for u in users if u != "admin"],
                key="del_user_sel",
            )
            if del_user and st.button("Remove user", key="rem_user"):
                if err := _remove_user(del_user):
                    st.error(err)
                else:
                    st.success(f"Removed **{del_user}**.")
                    st.rerun()

    # ── Allocate tab ──────────────────────────────────────────────────────────
    with tab_alloc:
        st.subheader("Assign norms to a user")
        norm_traces: dict = st.session_state["norm_traces"]
        norms_with_obs: set = st.session_state.get("norms_with_obs", set())
        available_norms = sorted(n for n in norm_traces if n in norms_with_obs)
        non_admin_users = [u for u in _load_users() if u != "admin"]

        if not non_admin_users:
            st.info("No labeler accounts yet. Create users in the Users tab.")
        else:
            target_user = st.selectbox("Assign to user", non_admin_users, key="alloc_user")

            # Show which norms already have a job for this user
            existing_jobs = get_all_jobs(JOBS_DIR)
            already_assigned: set[str] = set()
            for j in existing_jobs:
                if j["username"] == target_user:
                    already_assigned.update(j.get("norm_ids", []))

            to_assign = st.multiselect(
                "Norms to assign",
                [n for n in available_norms if n not in already_assigned],
                key="alloc_norms",
            )

            if to_assign:
                n_traces = sum(len(norm_traces.get(n, [])) for n in to_assign)
                st.caption(f"This will create a job with {n_traces} trace(s) total.")
            if st.button("Create job", disabled=not to_assign, key="create_job"):
                job_id = create_job(target_user, to_assign, norm_traces, JOBS_DIR)
                st.success(f"Job `{job_id}` created for **{target_user}**: {', '.join(to_assign)}")
                st.rerun()

            st.caption(
                "To create annotator overlap (same traces labeled by multiple people), "
                "assign the same norm(s) to more than one user."
            )

            if already_assigned:
                st.caption(f"Already assigned to **{target_user}**: {', '.join(sorted(already_assigned))}")

    # ── Jobs tab ──────────────────────────────────────────────────────────────
    with tab_jobs:
        st.subheader("All jobs")
        all_jobs = get_all_jobs(JOBS_DIR)
        if not all_jobs:
            st.info("No jobs yet.")
        else:
            for job in all_jobs:
                job_id = job["job_id"]
                units = get_job_units(job_id, JOBS_DIR)
                total_u = len(units)
                done_u = sum(1 for u in units if u["unit_status"] == "completed")
                update_job_status(job_id, JOBS_DIR)

                with st.expander(
                    f"`{job_id}` — {job['username']} | "
                    f"{done_u}/{total_u} units | {job.get('status', '?')}",
                    expanded=False,
                ):
                    st.write(f"**Norms:** {', '.join(job.get('norm_ids', []))}")
                    st.write(f"**Created:** {job.get('created_at', '?')}")

                    # Per-norm progress
                    for norm_id in job.get("norm_ids", []):
                        norm_units = [u for u in units if u["norm_id"] == norm_id]
                        n_done = sum(1 for u in norm_units if u["unit_status"] == "completed")
                        st.write(f"  • `{norm_id}`: {n_done}/{len(norm_units)}")

                    if st.button(f"Delete job {job_id}", key=f"del_{job_id}"):
                        delete_job(job_id, JOBS_DIR)
                        st.warning(f"Deleted job `{job_id}`.")
                        st.rerun()
