"""Auto-labeler for tool_call atomic propositions.

Uses ApRegexSensor (from norm_compliance.sensors) to deterministically ground
propositions whose ap_kind == "tool_call". These are true at turn t iff the
assistant message at t contains a tool call whose name exactly matches the
proposition's tool_name.

All other ap_kinds (tool_result, state, utterance) require human labeling.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

_APP_ROOT = str(Path(__file__).resolve().parents[2])
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from norm_compliance.sensors import ApRegexSensor  # noqa: E402
from norm_compliance.models import Turn  # noqa: E402


def build_auto_label_sensors(propositions: dict) -> dict[str, ApRegexSensor]:
    """Build one ApRegexSensor per tool_call proposition."""
    sensors: dict[str, ApRegexSensor] = {}
    for prop_id, defn in propositions.items():
        meta = defn.get("metadata", {})
        if meta.get("ap_kind") == "tool_call":
            tool_name = meta.get("tool_name", "")
            if tool_name:
                sensors[prop_id] = ApRegexSensor(
                    prop_id, rf"^{re.escape(tool_name)}$", field="tool_name"
                )
    return sensors


def _run_sensor(sensor: ApRegexSensor, message: dict) -> bool:
    """Run one stateless ApRegexSensor on a single message synchronously."""
    tool_calls = [
        {"name": tc.get("name", "")}
        for tc in (message.get("tool_calls") or [])
        if isinstance(tc, dict)
    ]
    turn = Turn(
        role=message.get("role", ""),
        content=message.get("content") or "",
        metadata={"tool_calls": tool_calls},
    )
    sensor.initialize()

    async def _step() -> bool:
        return await sensor.step(turn)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_step())
    finally:
        loop.close()


def compute_auto_labels(
    messages: list[dict],
    sensors: dict[str, ApRegexSensor],
) -> list[dict[str, bool]]:
    """Return one {prop_id: bool} dict per message for all tool_call props."""
    if not sensors:
        return [{} for _ in messages]
    return [
        {prop_id: _run_sensor(sensor, msg) for prop_id, sensor in sensors.items()}
        for msg in messages
    ]
