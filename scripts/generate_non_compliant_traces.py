#!/usr/bin/env python3
"""
Generate non-compliant agent traces for any tau2 domain.

For each task:
1. Find which primary norms are applicable based on the expected agent tool calls,
   using the `constrained_actions` field in the norms file.
2. Randomly select one norm to violate.
3. Modify the domain policy by replacing the norm's `policy_quote` with its
   `policy_violation` sentence.
4. Run the simulation with the modified policy.
5. Save results in the same format as standard trajectories, with a `violated_norm`
   field added to each simulation entry.

A norm is considered "primary" (eligible for selection) when its metadata contains
both `policy_quote` and `policy_violation`. Reparative norms and any norm missing
those fields are automatically excluded.

Tasks with no applicable primary norms are skipped with a warning.

Convention for norms files
--------------------------
Each norm entry must have, inside `metadata`:
  - constrained_actions : list[str]   — tool-call names the norm gates
  - policy_quote        : str         — verbatim substring of the domain policy
  - policy_violation    : str         — replacement sentence that induces non-compliance

Usage
-----
    python scripts/generate_non_compliant_traces.py \\
        --domain retail \\
        --norms  norms_and_propositions/retail_norms.json \\
        --agent-llm openai/gpt-4.1 \\
        --user-llm  openai/gpt-4.1-mini \\
        --output results/non_compliant_retail.json

    # Run on a subset of tasks
    python scripts/generate_non_compliant_traces.py \\
        --domain airline \\
        --norms  norms_and_propositions/airline_norms.json \\
        --agent-llm openai/gpt-4.1 \\
        --user-llm  openai/gpt-4.1-mini \\
        --task-ids 0 1 5 \\
        --output results/non_compliant_airline_subset.json
"""

import argparse
import json
import random
import subprocess
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent


# ---------------------------------------------------------------------------
# Norm loading and index building
# ---------------------------------------------------------------------------


def load_norms(norms_path: Path) -> dict:
    with open(norms_path) as f:
        return json.load(f)


def build_tool_to_norms(norms: dict) -> dict[str, list[str]]:
    """
    Build a mapping from tool-call name → list of applicable primary norm IDs.

    A norm is primary if its metadata contains both `policy_quote` and
    `policy_violation` (reparative norms and stubs are excluded).
    The mapping key is each action listed in the norm's `constrained_actions`.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for norm_id, norm in norms.items():
        meta = norm.get("metadata", {})
        if "policy_quote" not in meta or "policy_violation" not in meta:
            continue
        for action in meta.get("constrained_actions", []):
            index[action].append(norm_id)
    return dict(index)


# ---------------------------------------------------------------------------
# Per-task helpers
# ---------------------------------------------------------------------------


def get_applicable_norms(task, tool_to_norms: dict[str, list[str]]) -> list[str]:
    """
    Return norm IDs whose constrained action appears in the task's expected
    agent-side tool calls.
    """
    if task.evaluation_criteria is None:
        return []
    agent_tools = {
        a.name
        for a in (task.evaluation_criteria.actions or [])
        if a.requestor == "assistant"
    }
    seen: set[str] = set()
    applicable: list[str] = []
    for tool_name in agent_tools:
        for norm_id in tool_to_norms.get(tool_name, []):
            if norm_id not in seen:
                seen.add(norm_id)
                applicable.append(norm_id)
    return applicable


def build_violated_policy(base_policy: str, norm: dict) -> str:
    """
    Return a copy of base_policy with the norm's policy_quote replaced by its
    policy_violation sentence (first occurrence only).
    """
    meta = norm["metadata"]
    quote = meta["policy_quote"]
    violation = meta["policy_violation"]
    if quote not in base_policy:
        raise ValueError(
            f"policy_quote not found verbatim in domain policy.\n  Quote: {quote!r}"
        )
    return base_policy.replace(quote, violation, 1)


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def get_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=cwd, text=True
        ).strip()
    except Exception:
        return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def run_non_compliant_traces(
    domain: str,
    norms_path: Path,
    agent_llm: str,
    user_llm: str,
    output_path: Path,
    task_ids: list[str] | None = None,
    max_steps: int = 30,
    max_errors: int = 10,
    seed: int = 42,
) -> None:
    from tau2.agent.llm_agent import LLMAgent
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.runner import build_environment, build_user, get_tasks, run_simulation

    rng = random.Random(seed)

    norms = load_norms(norms_path)
    tool_to_norms = build_tool_to_norms(norms)

    primary_count = sum(
        1 for n in norms.values()
        if "policy_quote" in n.get("metadata", {}) and "policy_violation" in n.get("metadata", {})
    )
    logger.info(
        f"Loaded {len(norms)} norms ({primary_count} primary) from {norms_path.name}. "
        f"Domain: {domain}."
    )

    tasks = get_tasks(domain, task_ids=task_ids)
    logger.info(f"Loaded {len(tasks)} tasks.")

    # Read the base policy once via a probe environment
    _probe_env = build_environment(domain)
    base_policy = _probe_env.get_policy()

    run_timestamp = now_iso()
    git_commit = get_git_commit(REPO_ROOT)

    info_dict = {
        "git_commit": git_commit,
        "num_trials": 1,
        "max_steps": max_steps,
        "max_errors": max_errors,
        "user_info": {
            "implementation": "user_simulator",
            "llm": user_llm,
            "llm_args": None,
            "global_simulation_guidelines": None,
            "voice_settings": None,
            "persona_config": None,
        },
        "agent_info": {
            "implementation": "llm_agent",
            "llm": agent_llm,
            "llm_args": None,
            "voice_settings": None,
        },
        "environment_info": {
            "domain_name": domain,
            "policy": base_policy,
            "tool_defs": None,
        },
        "seed": seed,
        "text_streaming_config": None,
        "speech_complexity": None,
        "audio_native_config": None,
        "retrieval_config": None,
        "retrieval_config_kwargs": None,
    }

    serialised_tasks: list[dict] = []
    serialised_simulations: list[dict] = []
    skipped = 0

    for task in tasks:
        applicable = get_applicable_norms(task, tool_to_norms)
        if not applicable:
            agent_tool_names = [
                a.name
                for a in (task.evaluation_criteria.actions or [])
                if a.requestor == "assistant"
            ] if task.evaluation_criteria else []
            logger.warning(
                f"Task {task.id}: no applicable norms for expected agent actions "
                f"{agent_tool_names}. Skipping."
            )
            skipped += 1
            continue

        norm_id = rng.choice(applicable)
        norm = norms[norm_id]

        try:
            violated_policy = build_violated_policy(base_policy, norm)
        except ValueError as e:
            logger.error(f"Task {task.id}: could not build violated policy — {e}. Skipping.")
            skipped += 1
            continue

        logger.info(f"Task {task.id}: violating norm '{norm_id}'.")

        env = build_environment(domain)
        env.policy = violated_policy  # patched so simulation.policy records it

        agent = LLMAgent(
            tools=env.get_tools(),
            domain_policy=violated_policy,
            llm=agent_llm,
        )
        user = build_user("user_simulator", env, task, llm=user_llm)
        orchestrator = Orchestrator(
            domain=domain,
            agent=agent,
            user=user,
            environment=env,
            task=task,
            max_steps=max_steps,
            max_errors=max_errors,
            seed=rng.randint(0, 2**31),
            simulation_id=str(uuid.uuid4()),
        )

        try:
            result = run_simulation(orchestrator)
        except Exception as e:
            logger.error(f"Task {task.id}: simulation failed — {e}. Skipping.")
            skipped += 1
            continue

        sim_dict = result.model_dump(mode="json")
        sim_dict["violated_norm"] = norm_id

        serialised_simulations.append(sim_dict)
        serialised_tasks.append(task.model_dump(mode="json"))

        reward = result.reward_info.reward if result.reward_info else "N/A"
        logger.info(f"Task {task.id}: done. reward={reward}, violated_norm={norm_id}.")

    output = {
        "timestamp": run_timestamp,
        "info": info_dict,
        "tasks": serialised_tasks,
        "simulations": serialised_simulations,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(
        f"Saved {len(serialised_simulations)} simulations to {output_path}. "
        f"Skipped: {skipped}."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate non-compliant agent traces for any tau2 domain.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="tau2 domain name, e.g. retail, airline, telecom.",
    )
    parser.add_argument(
        "--norms",
        required=True,
        type=Path,
        help="Path to the domain's norms JSON file.",
    )
    parser.add_argument(
        "--agent-llm",
        required=True,
        help="LLM for the agent, e.g. openai/gpt-4.1.",
    )
    parser.add_argument(
        "--user-llm",
        required=True,
        help="LLM for the user simulator, e.g. openai/gpt-4.1-mini.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/non_compliant_traces.json"),
        help="Path to save the output JSON file.",
    )
    parser.add_argument(
        "--task-ids",
        nargs="*",
        default=None,
        help="Subset of task IDs to run (e.g. --task-ids 0 1 5). Runs all if omitted.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum steps per simulation.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=10,
        help="Maximum tool errors per simulation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for norm selection and per-simulation seeds.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_non_compliant_traces(
        domain=args.domain,
        norms_path=args.norms,
        agent_llm=args.agent_llm,
        user_llm=args.user_llm,
        output_path=args.output,
        task_ids=args.task_ids,
        max_steps=args.max_steps,
        max_errors=args.max_errors,
        seed=args.seed,
    )
