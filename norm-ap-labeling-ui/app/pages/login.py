"""Username-only login page for multi-user mode."""
from __future__ import annotations

import streamlit as st

from app.config import USERS_FILE
from app.modules.storage import append_jsonl, now_iso, read_jsonl, write_jsonl


def _load_users() -> list[str]:
    return [r["username"] for r in read_jsonl(USERS_FILE)]


def _ensure_admin() -> None:
    """Add 'admin' user on first run if not present."""
    users = _load_users()
    if "admin" not in users:
        append_jsonl(USERS_FILE, {"username": "admin", "created_at": now_iso()})


def render() -> None:
    _ensure_admin()
    st.title("Norm AP Labeling — Login")

    username = st.text_input("Username").strip()
    if st.button("Login", type="primary"):
        if not username:
            st.error("Enter a username.")
            return
        users = _load_users()
        if username not in users:
            st.error(f"Unknown user **{username}**. Ask an admin to create your account.")
            return
        st.session_state["username"] = username
        st.rerun()
