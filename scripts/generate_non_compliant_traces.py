#!/usr/bin/env python3
"""
Generate non-compliant traces for any tau2 domain.

For each task, ALL applicable norms are discovered. One simulation is run per
applicable norm with ALL of its violation types applied simultaneously:

  policy_violation      — replaces a sentence in the *agent* policy so a
                          policy-following agent will misbehave.
  user_policy_violation — appends adversarial instructions to the *user*
                          simulator so the user provokes the violation.
  env_modification      — mutates the environment database so the world state
                          makes the norm impossible to satisfy.

Any combination of the three types present on a norm is applied in the same
simulation run, maximising the chance of observing the violation.

Norm applicability
------------------
A norm contributes a simulation to a task when:
- its metadata has ``"always_applicable": true``  (e.g. single-user norm), OR
- any of its ``constrained_actions`` appears in the task's expected agent calls.

Convention for norm files
-------------------------
Each primary norm entry must have, inside ``metadata``:

  constrained_actions    : list[str]  — tool-call names the norm gates
  always_applicable      : bool       — (optional) include for every task

Plus one or more of:

  policy_quote           : str        — verbatim substring of the domain policy
  policy_violation       : str        — replacement text for agent non-compliance
  user_policy_violation  : str        — text appended to user simulator instructions
  env_modification       : dict       — structured DB mutation spec (see below)

env_modification spec
---------------------
  collection        : str   — top-level DB attribute (e.g. "orders", "users")
  set               : dict  — {field: value} pairs to assign on matching items
  nested_collection : str   — (optional) sub-attribute to descend into per item
  filter_by         : dict  — (optional) {field: value} filter for nested items

Important: env_modification is applied AFTER the orchestrator re-initialises
the environment from task initialization data (which would otherwise wipe the
mutation).  This is done by patching the orchestrator's
``_initialize_environment`` method.

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
        --domain retail \\
        --norms  norms_and_propositions/retail_norms.json \\
        --agent-llm openai/gpt-4.1 \\
        --user-llm  openai/gpt-4.1-mini \\
        --task-ids 0 1 5 \\
        --output results/non_compliant_retail_subset.json
"""

import argparse
import json
import random
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent

VIOLATION_TYPES = ("policy_violation", "user_policy_violation", "env_modification")


# ---------------------------------------------------------------------------
# Norm loading
# ---------------------------------------------------------------------------


def load_norms(norms_path: Path) -> dict:
    with open(norms_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Norm applicability
# ---------------------------------------------------------------------------


def get_applicable_norms(task, norms: dict) -> list[str]:
    """
    Return norm IDs applicable to this task.

    A norm is applicable when:
    - its metadata has ``'always_applicable': True``, OR
    - any of its ``constrained_actions`` intersects the task's expected agent tools.

    Only norms with at least one violation type in their metadata are returned.
    """
    agent_tools: set[str] = set()
    if task.evaluation_criteria is not None:
        agent_tools = {
            a.name
            for a in (task.evaluation_criteria.actions or [])
            if a.requestor == "assistant"
        }

    result: list[str] = []
    seen: set[str] = set()

    for norm_id, norm in norms.items():
        meta = norm.get("metadata", {})

        if not any(vt in meta for vt in VIOLATION_TYPES):
            continue

        always = meta.get("always_applicable", False)
        if not always:
            constrained = set(meta.get("constrained_actions", []))
            if not constrained & agent_tools:
                continue

        if norm_id not in seen:
            seen.add(norm_id)
            result.append(norm_id)

    return result


# ---------------------------------------------------------------------------
# Violation application helpers
# ---------------------------------------------------------------------------


def build_violated_agent_policy(base_policy: str, norm: dict) -> str:
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


def apply_env_modification(env, spec: dict) -> None:
    """
    Apply a structured mutation spec to env.tools.db.

    Spec keys:
      collection        : top-level DB attribute name
      set               : {field: value} to assign on each matching item
      nested_collection : (optional) attribute to descend into on each item
      filter_by         : (optional) {field: value} filter for nested items
    """
    db = env.tools.db
    collection = getattr(db, spec["collection"])
    nested_key = spec.get("nested_collection")
    filter_by = spec.get("filter_by", {})
    set_fields = spec["set"]

    for item in collection.values():
        if nested_key:
            nested = getattr(item, nested_key)
            for nested_item in nested.values():
                if all(
                    getattr(nested_item, k, None) == v for k, v in filter_by.items()
                ):
                    for field, value in set_fields.items():
                        setattr(nested_item, field, value)
        else:
            for field, value in set_fields.items():
                setattr(item, field, value)


def patch_orchestrator_env_modification(orchestrator, env, spec: dict) -> None:
    """
    Monkey-patch orchestrator._initialize_environment so that the env
    modification is applied AFTER the original set_state call.

    This is necessary because set_state -> update_db replaces the entire DB
    from task initialization data, wiping any pre-applied modifications.
    """
    original = orchestrator._initialize_environment

    def patched(initialization_data, initialization_actions, message_history):
        original(initialization_data, initialization_actions, message_history)
        apply_env_modification(env, spec)

    orchestrator._initialize_environment = patched


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

    primary_count = sum(
        1 for n in norms.values()
        if any(vt in n.get("metadata", {}) for vt in VIOLATION_TYPES)
    )
    logger.info(
        f"Loaded {len(norms)} norms ({primary_count} with violation specs) "
        f"from {norms_path.name}. Domain: {domain}."
    )

    tasks = get_tasks(domain, task_ids=task_ids)
    logger.info(f"Loaded {len(tasks)} tasks.")

    _probe_env = build_environment(domain)
    base_policy = _probe_env.get_policy()

    run_timestamp = now_iso()
    git_commit = get_git_commit(REPO_ROOT)

    info_dict = {
        "git_commit": git_commit,
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
        applicable = get_applicable_norms(task, norms)

        if not applicable:
            agent_tool_names = (
                [
                    a.name
                    for a in (task.evaluation_criteria.actions or [])
                    if a.requestor == "assistant"
                ]
                if task.evaluation_criteria
                else []
            )
            logger.warning(
                f"Task {task.id}: no applicable norms for expected agent actions "
                f"{agent_tool_names}. Skipping."
            )
            skipped += 1
            continue

        logger.info(
            f"Task {task.id}: {len(applicable)} norm(s) to violate: {applicable}"
        )

        for norm_id in applicable:
            norm = norms[norm_id]
            meta = norm["metadata"]
            applied_violations: list[str] = []

            env = build_environment(domain)

            # --- policy_violation: patch agent policy ---
            if "policy_violation" in meta:
                try:
                    agent_policy = build_violated_agent_policy(base_policy, norm)
                    env.policy = agent_policy
                    applied_violations.append("policy_violation")
                except ValueError as e:
                    logger.error(
                        f"Task {task.id} / {norm_id}: could not build violated policy "
                        f"— {e}. Skipping policy_violation for this norm."
                    )
                    agent_policy = base_policy
            else:
                agent_policy = base_policy

            # --- build participants ---
            agent = LLMAgent(
                tools=env.get_tools(),
                domain_policy=agent_policy,
                llm=agent_llm,
            )
            user = build_user("user_simulator", env, task, llm=user_llm)

            # --- user_policy_violation: append to user instructions ---
            if "user_policy_violation" in meta:
                user.instructions = (
                    (user.instructions or "")
                    + f"\n\nIMPORTANT INSTRUCTION: {meta['user_policy_violation']}"
                )
                applied_violations.append("user_policy_violation")

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

            # --- env_modification: patch orchestrator to apply AFTER set_state ---
            if "env_modification" in meta:
                patch_orchestrator_env_modification(
                    orchestrator, env, meta["env_modification"]
                )
                applied_violations.append("env_modification")

            logger.info(
                f"Task {task.id}: running norm='{norm_id}' "
                f"with violations={applied_violations}."
            )

            try:
                result = run_simulation(orchestrator)
            except Exception as e:
                logger.error(
                    f"Task {task.id} / {norm_id}: simulation failed — {e}. Skipping."
                )
                skipped += 1
                continue

            sim_dict = result.model_dump(mode="json")
            sim_dict["violated_norm"] = norm_id
            sim_dict["applied_violations"] = applied_violations

            serialised_simulations.append(sim_dict)
            serialised_tasks.append(task.model_dump(mode="json"))

            reward = result.reward_info.reward if result.reward_info else "N/A"
            logger.info(
                f"Task {task.id}: done. reward={reward}, "
                f"violated_norm={norm_id}, applied_violations={applied_violations}."
            )

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
        description="Generate non-compliant traces for any tau2 domain.",
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
        help="Random seed for per-simulation seeds.",
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
