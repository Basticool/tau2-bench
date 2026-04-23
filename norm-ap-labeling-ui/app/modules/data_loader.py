from __future__ import annotations

import json
from pathlib import Path


def load_traces(path: str | Path) -> list[dict]:
    """Load JSONL traces file; skip header line (type=header)."""
    traces = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "header":
                continue
            traces.append(obj)
    return traces


def group_traces_by_norm(traces: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for trace in traces:
        norm_id = trace.get("simulation", {}).get("violated_norm")
        if norm_id:
            groups.setdefault(norm_id, []).append(trace)
    return groups


def load_norms(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_propositions(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_sim_id(trace: dict) -> str:
    return trace.get("simulation", {}).get("id", "")


def get_messages(trace: dict) -> list[dict]:
    return trace.get("simulation", {}).get("messages", [])
