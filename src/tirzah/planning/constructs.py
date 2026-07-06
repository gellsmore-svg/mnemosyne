"""ITERATE, DECISION, PARALLEL/MERGE, RETRY, and ERROR expansion for interpretive PLAN execution (SPEC §4.6)."""
from __future__ import annotations

import copy
import re
import time
from typing import Any, Callable

from tirzah.planning.parallel_runtime import isolated_artifacts_snapshot, run_branches_concurrently

_retry_sleeper = time.sleep

from tirzah.planning.context_bundle import ensure_bundle, latest_tool_matches
from tirzah.planning.recursive import PlanStep

StepRunner = Callable[[PlanStep], dict[str, Any]]
BranchRunner = Callable[[PlanStep], dict[str, Any]]


def direct_body_steps(parent_id: str, steps: list[PlanStep]) -> list[PlanStep]:
    return [step for step in steps if parent_id in step.depends_on]


def transitive_dependents(root_id: str, steps: list[PlanStep]) -> set[str]:
    """All steps that depend on *root_id* directly or through other dependents."""
    dependents: set[str] = set()
    changed = True
    while changed:
        changed = False
        for row in steps:
            if row.id in dependents or row.id == root_id:
                continue
            deps = set(row.depends_on)
            if root_id in deps or deps.intersection(dependents):
                if root_id in deps or dependents.intersection(deps):
                    dependents.add(row.id)
                    changed = True
    return dependents


def selected_branch_subtree(decision_id: str, selected_ids: list[str], steps: list[PlanStep]) -> list[PlanStep]:
    """Steps reachable from selected branch heads without crossing skipped heads."""
    heads = set(selected_ids)
    skipped_heads = {row.id for row in direct_body_steps(decision_id, steps) if row.id not in heads}
    subtree_ids: set[str] = set(heads)
    changed = True
    while changed:
        changed = False
        for row in steps:
            if row.id in subtree_ids or row.id in skipped_heads:
                continue
            deps = set(row.depends_on)
            if decision_id not in deps and not deps.intersection(subtree_ids):
                continue
            if deps.intersection(skipped_heads):
                continue
            if deps - {decision_id} - subtree_ids:
                continue
            subtree_ids.add(row.id)
            changed = True
    order = {row.id: index for index, row in enumerate(steps)}
    return sorted((row for row in steps if row.id in subtree_ids), key=lambda row: order[row.id])


def reset_decision_subtree(
    decision_id: str,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
) -> None:
    for step_id in transitive_dependents(decision_id, steps):
        row = next((item for item in steps if item.id == step_id), None)
        if row is None:
            continue
        row.status = "pending"
        completed.discard(step_id)
        artifacts.pop(step_id, None)
    artifacts.pop(f"decision:{decision_id}", None)


def is_owned_by_pending_parent(step: PlanStep, steps: list[PlanStep], completed: set[str]) -> bool:
    for parent in steps:
        if parent.id not in step.depends_on:
            continue
        if parent.construct in {
            "ITERATE",
            "DECISION",
            "PARALLEL",
            "RETRY",
            "ERROR",
            "AWAIT",
            "SERVICE",
            "CONCURRENT",
            "QUEUE",
        } and parent.id not in completed:
            return True
    return False


def parse_retry_backoff(step: PlanStep) -> tuple[str, int]:
    text = " ".join([step.action, *step.success_criteria])
    mode = "none"
    match = re.search(r"BACKOFF:\s*(none|linear|exponential)", text, re.I)
    if match:
        mode = match.group(1).lower()
    for item in step.success_criteria:
        if item.lower().startswith("backoff:"):
            mode = item.split(":", 1)[1].strip().lower()
    base_ms = 10
    ms_match = re.search(r"BACKOFF_MS:\s*(\d+)", text, re.I)
    if ms_match:
        base_ms = max(0, int(ms_match.group(1)))
    return mode, base_ms


def retry_backoff_seconds(attempt: int, mode: str, base_ms: int) -> float:
    if mode == "none" or base_ms <= 0 or attempt <= 1:
        return 0.0
    if mode == "linear":
        return (attempt * base_ms) / 1000.0
    if mode == "exponential":
        return (2 ** (attempt - 2) * base_ms) / 1000.0
    return 0.0


def parse_parallel_mode(step: PlanStep) -> str:
    text = " ".join([step.action, *step.success_criteria])
    if re.search(r"\bCONCURRENT\b|MODE:\s*concurrent", text, re.I):
        return "concurrent"
    for item in step.success_criteria:
        if item.lower() in {"concurrent", "mode:concurrent"}:
            return "concurrent"
    return "sequential"


def _trim_await_event(value: str) -> str:
    raw = str(value or "").strip()
    raw = re.split(r"\s+TIMEOUT:", raw, maxsplit=1, flags=re.I)[0].strip()
    return _normalize_error_signal(raw)


def parse_await_event(step: PlanStep) -> str:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"EVENT:\s*([^;\]]+)", text, re.I)
    if match:
        return _trim_await_event(match.group(1))
    for item in step.success_criteria:
        if item.lower().startswith("event:"):
            return _trim_await_event(item.split(":", 1)[1])
    return "default"


def parse_await_timeout_seconds(step: PlanStep) -> float | None:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"TIMEOUT:\s*(\d+(?:\.\d+)?)(s|ms)?", text, re.I)
    if match:
        value = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        return value / 1000.0 if unit == "ms" else value
    for item in step.success_criteria:
        if item.lower().startswith("timeout:"):
            raw = item.split(":", 1)[1].strip().lower()
            if raw.endswith("ms"):
                return float(raw[:-2]) / 1000.0
            if raw.endswith("s"):
                return float(raw[:-1])
            return float(raw)
    return None


def await_signals(answer_kwargs: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merged.update(artifacts.get("await_signals") or {})
    merged.update(answer_kwargs.get("await_signals") or {})
    return merged


def await_event_satisfied(
    step: PlanStep,
    *,
    answer_kwargs: dict[str, Any],
    artifacts: dict[str, Any],
) -> bool:
    event = parse_await_event(step)
    signals = await_signals(answer_kwargs, artifacts)
    if signals.get("any") or signals.get("*"):
        return True
    if signals.get(step.id):
        return True
    if signals.get(event):
        return True
    return False


def parse_retry_max(step: PlanStep, *, default: int = 3, ceiling: int = 5) -> int:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"MAX:\s*(\d+)", text, re.I)
    if match:
        return max(1, min(int(match.group(1)), ceiling))
    return max(1, min(default, ceiling))


def parse_max_rounds(step: PlanStep, *, default: int = 3, ceiling: int = 10) -> int:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"MAX:\s*(\d+)", text, re.I)
    if match:
        return max(1, min(int(match.group(1)), ceiling))
    return max(1, min(default, ceiling))


def branch_label(step: PlanStep) -> str | None:
    for item in step.success_criteria:
        lowered = item.lower()
        if lowered.startswith("branch:"):
            return item.split(":", 1)[1].strip().lower()
    return None


def parse_decision_signal(step: PlanStep) -> str:
    match = re.search(r"ON:\s*([^→|]+)", step.action, re.I)
    if match:
        return _normalize_signal(match.group(1))
    for item in step.success_criteria:
        if item.lower().startswith("on:"):
            return _normalize_signal(item.split(":", 1)[1])
    return "default"


def _normalize_signal(value: str) -> str:
    return " ".join(str(value or "").split()).lower().replace(" ", "_")


def cascade_skip_dependents(
    steps: list[PlanStep],
    completed: set[str],
    trace: list[dict[str, Any]],
    *,
    reason: str = "skipped_parent",
) -> None:
    """Skip pending steps that depend on a skipped ancestor (nested branch trees)."""
    changed = True
    while changed:
        changed = False
        for step in steps:
            if step.status != "pending":
                continue
            parents = [next((row for row in steps if row.id == dep_id), None) for dep_id in step.depends_on]
            if not parents or not all(parent is not None and parent.status == "skipped" for parent in parents):
                continue
            step.status = "skipped"
            completed.add(step.id)
            trace.append(
                {
                    "step": "plan.step.skipped",
                    "step_id": step.id,
                    "metadata": {
                        "construct": step.construct,
                        "reason": reason,
                        "skipped_parents": [parent.id for parent in parents if parent is not None],
                    },
                }
            )
            changed = True


def parse_loop_control(step: PlanStep) -> tuple[str | None, str | None]:
    construct = (step.construct or "").upper()
    if construct not in {"BREAK", "CONTINUE"}:
        return None, None
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"IF:\s*([^;\]]+)", text, re.I)
    condition = match.group(1).strip().lower() if match else None
    return construct.lower(), condition


def evaluate_loop_control_condition(
    condition: str | None,
    *,
    artifacts: dict[str, Any],
    round_num: int,
    body_blocked: bool,
) -> bool:
    if condition is None:
        return True
    normalized = " ".join(condition.split()).lower().replace(" ", "_")
    if normalized in {"blocked", "body_blocked"}:
        return body_blocked
    if normalized == "has_matches":
        bundle = ensure_bundle(artifacts)
        return bool(latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")))
    if normalized == "context_ready":
        bundle = ensure_bundle(artifacts)
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        return "compile_context" in tools or bool(artifacts.get("retrieval_package"))
    if normalized.startswith("round>="):
        try:
            return round_num >= int(normalized.split(">=", 1)[1])
        except ValueError:
            return False
    return False


def iterate_until_satisfied(step: PlanStep, artifacts: dict[str, Any], *, round_num: int) -> bool:
    criteria = [
        item.split(":", 1)[1].strip().lower()
        for item in step.success_criteria
        if item.lower().startswith("until:")
    ]
    bundle = ensure_bundle(artifacts)
    if "has_matches" in criteria and latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")):
        return True
    if "context_ready" in criteria:
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        if "compile_context" in tools or artifacts.get("retrieval_package"):
            return True
    if "single_round" in criteria:
        return round_num >= 1
    return False


def evaluate_decision_branch(
    signal: str,
    *,
    artifacts: dict[str, Any],
    answer_kwargs: dict[str, Any],
    config: Any = None,
) -> str:
    runtime = getattr(config, "runtime", None) if config is not None else None
    if signal in {"web_research", "web_research_enabled", "external_evidence"}:
        if answer_kwargs.get("web_research") or getattr(runtime, "web_research_enabled", False):
            return "web"
        return "memory"
    if signal == "retrieval_mode":
        mode = answer_kwargs.get("retrieval_mode") or getattr(runtime, "retrieval_mode", "direct")
        return str(mode).lower()
    if signal in {"has_matches", "focus_selection"}:
        bundle = ensure_bundle(artifacts)
        if latest_tool_matches(bundle, ("search_nodes", "expand_proximity", "semantic_candidates")):
            return "expand"
        return "search"
    if signal == "context_depth":
        bundle = ensure_bundle(artifacts)
        tools = {str(row.get("tool")) for row in bundle.get("tool_results") or []}
        if "compile_context" not in tools:
            return "compile"
        return "expand"
    return "default"


def execute_iterate_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    body = direct_body_steps(step.id, steps)
    if not body:
        return {"status": "completed", "artifact": {"rounds": 0, "body": []}}
    max_rounds = parse_max_rounds(step)
    rounds_run = 0
    loop_break = False
    for round_num in range(1, max_rounds + 1):
        rounds_run = round_num
        trace.append(
            {
                "step": "plan.iterate.round",
                "step_id": step.id,
                "metadata": {"round": round_num, "max_rounds": max_rounds, "body": [item.id for item in body]},
            }
        )
        body_blocked = False
        for body_step in body:
            if round_num > 1 and body_step.status in {"completed", "skipped"}:
                if (body_step.construct or "").upper() not in {"BREAK", "CONTINUE"}:
                    body_step.status = "pending"
                    completed.discard(body_step.id)
                    if (body_step.construct or "").upper() == "DECISION":
                        reset_decision_subtree(body_step.id, steps, completed, artifacts)
            if body_step.status != "pending":
                continue
            if not all(dep in completed or dep == step.id for dep in body_step.depends_on):
                continue
            control, condition = parse_loop_control(body_step)
            if control == "break":
                if evaluate_loop_control_condition(
                    condition,
                    artifacts=artifacts,
                    round_num=round_num,
                    body_blocked=body_blocked,
                ):
                    body_step.status = "completed"
                    completed.add(body_step.id)
                    trace.append(
                        {
                            "step": "plan.step.completed",
                            "step_id": body_step.id,
                            "metadata": {
                                "construct": "BREAK",
                                "reason": "loop_break",
                                "round": round_num,
                                "condition": condition,
                            },
                        }
                    )
                    loop_break = True
                    break
                body_step.status = "skipped"
                completed.add(body_step.id)
                trace.append(
                    {
                        "step": "plan.step.skipped",
                        "step_id": body_step.id,
                        "metadata": {"construct": "BREAK", "reason": "condition_not_met", "round": round_num},
                    }
                )
                continue
            if control == "continue":
                if evaluate_loop_control_condition(
                    condition,
                    artifacts=artifacts,
                    round_num=round_num,
                    body_blocked=body_blocked,
                ):
                    body_step.status = "completed"
                    completed.add(body_step.id)
                    trace.append(
                        {
                            "step": "plan.step.completed",
                            "step_id": body_step.id,
                            "metadata": {
                                "construct": "CONTINUE",
                                "reason": "loop_continue",
                                "round": round_num,
                                "condition": condition,
                            },
                        }
                    )
                    break
                body_step.status = "skipped"
                completed.add(body_step.id)
                trace.append(
                    {
                        "step": "plan.step.skipped",
                        "step_id": body_step.id,
                        "metadata": {"construct": "CONTINUE", "reason": "condition_not_met", "round": round_num},
                    }
                )
                continue
            try:
                outcome = run_step(body_step, round_num=round_num)
            except TypeError:
                outcome = run_step(body_step)
            body_step.status = outcome["status"]
            trace.append(
                {
                    "step": f"plan.step.{outcome['status']}",
                    "step_id": body_step.id,
                    "metadata": {"construct": body_step.construct, "reason": outcome.get("reason"), "round": round_num},
                }
            )
            if outcome["status"] == "completed":
                completed.add(body_step.id)
                if outcome.get("artifact") is not None:
                    artifacts[body_step.id] = outcome["artifact"]
            if outcome["status"] == "blocked":
                body_blocked = True
        if loop_break:
            break
        if iterate_until_satisfied(step, artifacts, round_num=round_num):
            break
        if any(body_step.status == "blocked" for body_step in body):
            break
    return {
        "status": "completed",
        "artifact": {
            "rounds": rounds_run,
            "body": [item.id for item in body],
            "max_rounds": max_rounds,
            "loop_break": loop_break,
        },
    }


def execute_branch_subtree(
    parent_step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    selected_ids: list[str],
    branch_runner: BranchRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
    inline_kind: str = "decision",
) -> dict[str, Any]:
    """Run a branch subtree under a DECISION or PARALLEL parent."""
    subtree = selected_branch_subtree(parent_step.id, selected_ids, steps)
    metadata_base = {"round": round_num} if round_num is not None else {}
    parent_key = "decision_id" if inline_kind == "decision" else "parallel_id"
    inline_flag = "inline_decision_branch" if inline_kind == "decision" else "inline_parallel_branch"
    while True:
        progressed = False
        for branch_step in subtree:
            if branch_step.status != "pending":
                continue
            if not all(dep in completed or dep == parent_step.id for dep in branch_step.depends_on):
                continue
            outcome = branch_runner(branch_step)
            branch_step.status = outcome["status"]
            trace.append(
                {
                    "step": f"plan.step.{outcome['status']}",
                    "step_id": branch_step.id,
                    "metadata": {
                        "construct": branch_step.construct,
                        "reason": outcome.get("reason"),
                        inline_flag: True,
                        parent_key: parent_step.id,
                        **metadata_base,
                    },
                }
            )
            progressed = True
            if outcome["status"] == "completed":
                completed.add(branch_step.id)
                if outcome.get("artifact") is not None:
                    artifacts[branch_step.id] = outcome["artifact"]
            elif outcome["status"] == "blocked":
                return {
                    "status": "blocked",
                    "reason": outcome.get("reason", "inline_branch_blocked"),
                    "artifact": outcome.get("artifact"),
                }
        if not progressed:
            break
    return {"status": "completed"}


def execute_selected_branch_steps(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    selected_ids: list[str],
    branch_runner: BranchRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Run selected DECISION branch steps immediately (inside an active ITERATE round)."""
    return execute_branch_subtree(
        step,
        steps=steps,
        completed=completed,
        artifacts=artifacts,
        selected_ids=selected_ids,
        branch_runner=branch_runner,
        trace=trace,
        round_num=round_num,
        inline_kind="decision",
    )


def parse_parallel_state(step: PlanStep) -> str:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"STATE:\s*(isolated|shared)", text, re.I)
    return match.group(1).lower() if match else "isolated"


def parse_merge_rule(step: PlanStep) -> str:
    for item in step.success_criteria:
        lowered = item.lower()
        if lowered.startswith("merge:"):
            return item.split(":", 1)[1].strip().lower()
    return "collect"


def infer_parallel_parent(merge_step: PlanStep, steps: list[PlanStep]) -> str | None:
    for dep_id in merge_step.depends_on:
        parent = next((row for row in steps if row.id == dep_id), None)
        if parent is not None and (parent.construct or "").upper() == "PARALLEL":
            return parent.id
    for dep_id in merge_step.depends_on:
        branch = next((row for row in steps if row.id == dep_id), None)
        if branch is None:
            continue
        for parent_id in branch.depends_on:
            parent = next((row for row in steps if row.id == parent_id), None)
            if parent is not None and (parent.construct or "").upper() == "PARALLEL":
                return parent.id
    return None


def _run_single_parallel_branch(
    step: PlanStep,
    branch: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    branch_runner: BranchRunner,
    trace: list[dict[str, Any]],
    round_num: int | None,
    state: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    trace.append(
        {
            "step": "plan.parallel.branch",
            "step_id": step.id,
            "metadata": {"branch": branch.id, "state": state, "round": round_num},
        }
    )
    saved_bundle = artifacts.get("context_bundle")
    if state == "isolated":
        artifacts["context_bundle"] = {"tool_results": []}
    try:
        outcome = execute_branch_subtree(
            step,
            steps=steps,
            completed=completed,
            artifacts=artifacts,
            selected_ids=[branch.id],
            branch_runner=branch_runner,
            trace=trace,
            round_num=round_num,
            inline_kind="parallel",
        )
    finally:
        if state == "isolated":
            payload = {
                **dict(artifacts.get(branch.id) or {}),
                "context_bundle": copy.deepcopy(artifacts.get("context_bundle") or {"tool_results": []}),
            }
            if saved_bundle is not None:
                artifacts["context_bundle"] = saved_bundle
            else:
                artifacts.pop("context_bundle", None)
            return outcome, payload
    return outcome, dict(artifacts.get(branch.id) or {})


def execute_parallel_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    branch_runner: BranchRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
    isolated_branch_runner: Callable[[dict[str, Any], set[str]], BranchRunner] | None = None,
    shared_lock: Any | None = None,
) -> dict[str, Any]:
    """Run PARALLEL branches sequentially or concurrently; isolated or shared state."""
    branches = direct_body_steps(step.id, steps)
    state = parse_parallel_state(step)
    execution_mode = parse_parallel_mode(step)
    if not branches:
        return {
            "status": "completed",
            "artifact": {"branches": [], "state": state, "execution_mode": execution_mode},
        }
    branch_payload: dict[str, Any] = {}

    def run_branch(branch: PlanStep) -> tuple[dict[str, Any], dict[str, Any]]:
        if execution_mode == "concurrent" and state == "isolated" and isolated_branch_runner is not None:
            local_artifacts = isolated_artifacts_snapshot(artifacts)
            local_completed = set(completed)
            local_trace: list[dict[str, Any]] = []
            trace.append(
                {
                    "step": "plan.parallel.branch",
                    "step_id": step.id,
                    "metadata": {"branch": branch.id, "state": state, "round": round_num, "execution_mode": execution_mode},
                }
            )
            outcome = execute_branch_subtree(
                step,
                steps=steps,
                completed=local_completed,
                artifacts=local_artifacts,
                selected_ids=[branch.id],
                branch_runner=isolated_branch_runner(local_artifacts, local_completed),
                trace=local_trace,
                round_num=round_num,
                inline_kind="parallel",
            )
            trace.extend(local_trace)
            completed.update(local_completed)
            payload = {
                **dict(local_artifacts.get(branch.id) or {}),
                "context_bundle": copy.deepcopy(local_artifacts.get("context_bundle") or {"tool_results": []}),
            }
            return outcome, payload
        runner = branch_runner
        if execution_mode == "concurrent" and state == "shared" and shared_lock is not None:
            from tirzah.planning.parallel_runtime import shared_branch_runner

            runner = shared_branch_runner(branch_runner, artifacts, shared_lock)
        return _run_single_parallel_branch(
            step,
            branch,
            steps=steps,
            completed=completed,
            artifacts=artifacts,
            branch_runner=runner,
            trace=trace,
            round_num=round_num,
            state=state,
        )

    if execution_mode == "concurrent":
        results = run_branches_concurrently(branches, run_branch=run_branch)
        for branch_id, outcome, payload in results:
            if outcome["status"] == "blocked":
                return outcome
            branch_payload[branch_id] = payload
    else:
        for branch in branches:
            outcome, payload = run_branch(branch)
            if outcome["status"] == "blocked":
                return outcome
            branch_payload[branch.id] = payload

    parallel_artifact = {
        "state": state,
        "execution_mode": execution_mode,
        "branch_ids": [branch.id for branch in branches],
        "branches": branch_payload,
    }
    artifacts[f"parallel:{step.id}"] = parallel_artifact
    trace.append(
        {
            "step": "plan.parallel.completed",
            "step_id": step.id,
            "metadata": {
                "state": state,
                "execution_mode": execution_mode,
                "branch_ids": parallel_artifact["branch_ids"],
                "round": round_num,
            },
        }
    )
    return {"status": "completed", "artifact": parallel_artifact}


def execute_merge_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    artifacts: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """Join PARALLEL branch artifacts at a MERGE step."""
    from tirzah.planning.context_bundle import append_tool_result

    parallel_id = infer_parallel_parent(step, steps)
    rule = parse_merge_rule(step)
    parallel_data = artifacts.get(f"parallel:{parallel_id}") if parallel_id else {}
    parallel_state = str(parallel_data.get("state") or "shared")
    branch_ids = list(parallel_data.get("branch_ids") or step.depends_on)
    stored_branches = parallel_data.get("branches") or {}
    branch_payload = {
        branch_id: stored_branches.get(branch_id) or artifacts.get(branch_id) for branch_id in branch_ids
    }
    if rule == "context_bundle":
        bundle = ensure_bundle(artifacts)
        for artifact in branch_payload.values():
            if not isinstance(artifact, dict):
                continue
            if parallel_state == "isolated":
                for row in (artifact.get("context_bundle") or {}).get("tool_results") or []:
                    if not isinstance(row, dict):
                        continue
                    append_tool_result(
                        bundle,
                        tool=str(row.get("tool") or "unknown"),
                        output=row.get("output") or {},
                        arguments=dict(row.get("arguments") or {}),
                        details=dict(row.get("details") or {}),
                        ok=bool(row.get("ok", True)),
                    )
                continue
            tool_result = artifact.get("tool_result")
            if isinstance(tool_result, dict):
                append_tool_result(
                    bundle,
                    tool=str(tool_result.get("tool") or artifact.get("tool") or "unknown"),
                    output=tool_result.get("output") or {},
                    arguments=dict(tool_result.get("arguments") or {}),
                    details=dict(tool_result.get("details") or {}),
                    ok=bool(tool_result.get("ok", artifact.get("ok", True))),
                )
    merged = {
        "rule": rule,
        "parallel_id": parallel_id,
        "branches": branch_payload,
    }
    artifacts[f"merge:{step.id}"] = merged
    trace.append(
        {
            "step": "plan.parallel.merged",
            "step_id": step.id,
            "metadata": {
                "rule": rule,
                "parallel_id": parallel_id,
                "branch_ids": branch_ids,
                "tool_count": len((ensure_bundle(artifacts).get("tool_results") or [])),
                "parallel_state": parallel_state,
            },
        }
    )
    return {"status": "completed", "artifact": merged}


def _normalize_error_signal(value: str) -> str:
    return " ".join(str(value or "").split()).lower().replace(" ", "_")


def parse_error_handler(step: PlanStep) -> dict[str, Any]:
    text = " ".join([step.action, *step.success_criteria])
    on_signal = "any"
    then_mode = "propagate"
    fallback_step_id: str | None = None
    match = re.search(r"ON:\s*([^;]+?)(?:\s+THEN:|\s*;|$)", text, re.I)
    if match:
        on_signal = _normalize_error_signal(match.group(1))
    then_match = re.search(r"THEN:\s*([A-Za-z_]+)", text, re.I)
    if then_match:
        then_mode = then_match.group(1).lower()
    for item in step.success_criteria:
        lowered = item.lower()
        if lowered.startswith("on:"):
            on_signal = _normalize_error_signal(item.split(":", 1)[1])
        elif lowered.startswith("then:"):
            then_mode = item.split(":", 1)[1].strip().lower()
        elif lowered.startswith("fallback:"):
            fallback_step_id = item.split(":", 1)[1].strip()
    arrow = re.search(r"fallback\s*→\s*([^\];]+)", text, re.I)
    if arrow and not fallback_step_id:
        fallback_step_id = arrow.group(1).strip()
    return {
        "on": on_signal,
        "then": then_mode,
        "fallback_step_id": fallback_step_id,
    }


def error_signal_matches(on_signal: str, reason: str | None) -> bool:
    if on_signal in {"any", "default", "*"}:
        return True
    normalized = _normalize_error_signal(reason or "")
    if not normalized:
        return False
    return on_signal in normalized or normalized.startswith(on_signal) or normalized.endswith(on_signal)


def execute_error_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Run guarded body steps; on blocked + matching ON, apply THEN handle/fallback/propagate."""
    body = direct_body_steps(step.id, steps)
    handler = parse_error_handler(step)
    if not body:
        return {"status": "completed", "artifact": {"guarded": True, **handler}}
    failure: dict[str, Any] | None = None
    for body_step in body:
        if body_step.status != "pending":
            continue
        if not all(dep in completed or dep == step.id for dep in body_step.depends_on):
            continue
        outcome = run_step(body_step)
        body_step.status = outcome["status"]
        trace.append(
            {
                "step": f"plan.step.{outcome['status']}",
                "step_id": body_step.id,
                "metadata": {
                    "construct": body_step.construct,
                    "reason": outcome.get("reason"),
                    "guarded_by": step.id,
                    "round": round_num,
                },
            }
        )
        if outcome["status"] == "completed":
            completed.add(body_step.id)
            if outcome.get("artifact") is not None:
                artifacts[body_step.id] = outcome["artifact"]
            continue
        if outcome["status"] == "blocked":
            failure = outcome
            if not error_signal_matches(handler["on"], outcome.get("reason")):
                return outcome
            break
        return outcome
    if failure is None:
        return {"status": "completed", "artifact": {"guarded": True, **handler}}
    then_mode = handler["then"]
    trace.append(
        {
            "step": "plan.error.triggered",
            "step_id": step.id,
            "metadata": {
                "on": handler["on"],
                "then": then_mode,
                "reason": failure.get("reason"),
                "round": round_num,
            },
        }
    )
    if then_mode == "propagate":
        return failure
    for body_step in body:
        if body_step.status == "blocked":
            body_step.status = "skipped"
            completed.add(body_step.id)
            trace.append(
                {
                    "step": "plan.step.skipped",
                    "step_id": body_step.id,
                    "metadata": {"construct": body_step.construct, "reason": "error_guard_failed", "round": round_num},
                }
            )
    if then_mode == "handle":
        artifacts[f"error:{step.id}"] = {
            "reason": failure.get("reason"),
            "handled": True,
            "on": handler["on"],
        }
        return {
            "status": "completed",
            "artifact": {"handled_error": failure.get("reason"), "on": handler["on"]},
        }
    if then_mode == "fallback":
        fallback_id = handler.get("fallback_step_id")
        if not fallback_id:
            return {"status": "blocked", "reason": "error_fallback_unspecified"}
        fallback = next((row for row in steps if row.id == fallback_id), None)
        if fallback is None:
            return {"status": "blocked", "reason": "error_fallback_missing", "fallback_step_id": fallback_id}
        fallback_outcome = run_step(fallback)
        fallback.status = fallback_outcome["status"]
        trace.append(
            {
                "step": f"plan.step.{fallback_outcome['status']}",
                "step_id": fallback.id,
                "metadata": {
                    "construct": fallback.construct,
                    "reason": fallback_outcome.get("reason"),
                    "error_fallback": True,
                    "recovered_from": failure.get("reason"),
                    "round": round_num,
                },
            }
        )
        if fallback_outcome["status"] == "completed":
            completed.add(fallback.id)
            if fallback_outcome.get("artifact") is not None:
                artifacts[fallback.id] = fallback_outcome["artifact"]
            artifacts[f"error:{step.id}"] = {
                "reason": failure.get("reason"),
                "fallback_step_id": fallback_id,
                "recovered": True,
            }
            return {
                "status": "completed",
                "artifact": {
                    "fallback_step_id": fallback_id,
                    "recovered_from": failure.get("reason"),
                },
            }
        return fallback_outcome
    return {"status": "blocked", "reason": f"unknown_error_then:{then_mode}"}


def execute_retry_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Re-run direct body steps until they complete or MAX attempts are exhausted.

    Intended semantics: a retry attempt re-runs the WHOLE body — including steps
    that succeeded on an earlier attempt — so each attempt is a coherent pass
    rather than a patchwork of stale and fresh results. Callers should keep
    side-effectful steps (e.g. web_fetch) idempotent or wrap only the fragile
    tail of a flow in RETRY.
    """
    body = direct_body_steps(step.id, steps)
    if not body:
        return {"status": "completed", "artifact": {"attempts": 0, "body": []}}
    max_attempts = parse_retry_max(step)
    backoff_mode, backoff_ms = parse_retry_backoff(step)
    attempts_run = 0
    for attempt in range(1, max_attempts + 1):
        attempts_run = attempt
        if attempt > 1:
            delay = retry_backoff_seconds(attempt, backoff_mode, backoff_ms)
            if delay > 0:
                trace.append(
                    {
                        "step": "plan.retry.backoff",
                        "step_id": step.id,
                        "metadata": {
                            "attempt": attempt,
                            "backoff_mode": backoff_mode,
                            "delay_seconds": delay,
                            "round": round_num,
                        },
                    }
                )
                _retry_sleeper(delay)
        trace.append(
            {
                "step": "plan.retry.attempt",
                "step_id": step.id,
                "metadata": {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "backoff_mode": backoff_mode,
                    "round": round_num,
                },
            }
        )
        body_blocked = False
        for body_step in body:
            if attempt > 1 and body_step.status in {"completed", "blocked"}:
                body_step.status = "pending"
                completed.discard(body_step.id)
            if body_step.status != "pending":
                continue
            if not all(dep in completed or dep == step.id for dep in body_step.depends_on):
                continue
            outcome = run_step(body_step)
            body_step.status = outcome["status"]
            trace.append(
                {
                    "step": f"plan.step.{outcome['status']}",
                    "step_id": body_step.id,
                    "metadata": {
                        "construct": body_step.construct,
                        "reason": outcome.get("reason"),
                        "retry_attempt": attempt,
                        "round": round_num,
                    },
                }
            )
            if outcome["status"] == "completed":
                completed.add(body_step.id)
                if outcome.get("artifact") is not None:
                    artifacts[body_step.id] = outcome["artifact"]
            if outcome["status"] == "blocked":
                body_blocked = True
        if not body_blocked and all(item.status == "completed" for item in body):
            return {
                "status": "completed",
                "artifact": {"attempts": attempts_run, "body": [item.id for item in body], "max_attempts": max_attempts},
            }
    return {
        "status": "blocked",
        "reason": "retry_exhausted",
        "artifact": {"attempts": attempts_run, "body": [item.id for item in body], "max_attempts": max_attempts},
    }


def execute_decision_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    answer_kwargs: dict[str, Any],
    config: Any,
    trace: list[dict[str, Any]],
    branch_runner: BranchRunner | None = None,
    round_num: int | None = None,
) -> dict[str, Any]:
    signal = parse_decision_signal(step)
    chosen = evaluate_decision_branch(signal, artifacts=artifacts, answer_kwargs=answer_kwargs, config=config)
    artifacts[f"decision:{step.id}"] = chosen
    branches = direct_body_steps(step.id, steps)
    selected = []
    fallback_id = None
    for branch in branches:
        label = branch_label(branch) or "default"
        if label == chosen:
            selected.append(branch.id)
        elif label == "default":
            fallback_id = branch.id
    if not selected and fallback_id:
        selected = [fallback_id]
    elif not selected and branches:
        selected = [branches[0].id]
    for branch in branches:
        if branch.id in selected:
            continue
        branch.status = "skipped"
        completed.add(branch.id)
        trace.append(
            {
                "step": "plan.step.skipped",
                "step_id": branch.id,
                "metadata": {"construct": branch.construct, "reason": "decision_not_selected", "branch": chosen},
            }
        )
    cascade_skip_dependents(steps, completed, trace, reason="decision_branch_skipped")
    trace.append(
        {
            "step": "plan.decision.selected",
            "step_id": step.id,
            "metadata": {"signal": signal, "branch": chosen, "selected_steps": selected, "round": round_num},
        }
    )
    artifact = {"signal": signal, "branch": chosen, "selected_steps": selected}
    if branch_runner is not None and selected:
        branch_outcome = execute_selected_branch_steps(
            step,
            steps=steps,
            completed=completed,
            artifacts=artifacts,
            selected_ids=selected,
            branch_runner=branch_runner,
            trace=trace,
            round_num=round_num,
        )
        if branch_outcome["status"] == "blocked":
            return branch_outcome
        artifact["inline_branch_status"] = branch_outcome["status"]
    return {"status": "completed", "artifact": artifact}


def parse_await_then(step: PlanStep) -> str:
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"THEN:\s*([A-Za-z_]+)", text, re.I)
    if match:
        return match.group(1).lower()
    for item in step.success_criteria:
        if item.lower().startswith("then:"):
            return item.split(":", 1)[1].strip().lower()
    return "blocked"


def await_started_at(artifacts: dict[str, Any], step_id: str) -> float | None:
    payload = artifacts.get(f"await:{step_id}")
    if isinstance(payload, dict):
        started = payload.get("started_at")
        if isinstance(started, (int, float)):
            return float(started)
    return None


def await_timeout_elapsed(step: PlanStep, artifacts: dict[str, Any], *, now: float | None = None) -> bool:
    timeout = parse_await_timeout_seconds(step)
    if timeout is None:
        return False
    started = await_started_at(artifacts, step.id)
    if started is None:
        return False
    current = time.time() if now is None else now
    return (current - started) >= timeout


def resume_awaiting_steps(
    steps: list[PlanStep],
    completed: set[str],
    *,
    answer_kwargs: dict[str, Any],
    artifacts: dict[str, Any],
    trace: list[dict[str, Any]],
) -> None:
    """Promote awaiting steps when external signals arrive or a timeout fires."""
    for step in steps:
        if step.status != "awaiting":
            continue
        event = parse_await_event(step)
        if await_event_satisfied(step, answer_kwargs=answer_kwargs, artifacts=artifacts):
            step.status = "completed"
            completed.add(step.id)
            artifacts[f"await:{step.id}"] = {
                "event": event,
                "status": "satisfied",
                "signals": await_signals(answer_kwargs, artifacts),
            }
            trace.append(
                {
                    "step": "plan.await.satisfied",
                    "step_id": step.id,
                    "metadata": {"event": event, "signals": sorted(await_signals(answer_kwargs, artifacts).keys())},
                }
            )
            continue
        if await_timeout_elapsed(step, artifacts):
            then_mode = parse_await_then(step)
            step.status = "blocked" if then_mode == "blocked" else "completed"
            if step.status == "completed":
                completed.add(step.id)
            artifacts[f"await:{step.id}"] = {
                "event": event,
                "status": "timeout",
                "then": then_mode,
            }
            trace.append(
                {
                    "step": "plan.await.timeout",
                    "step_id": step.id,
                    "metadata": {"event": event, "then": then_mode, "timeout_seconds": parse_await_timeout_seconds(step)},
                }
            )


def execute_await_step(
    step: PlanStep,
    *,
    answer_kwargs: dict[str, Any],
    artifacts: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """Suspend until await_signals satisfy EVENT or TIMEOUT elapses."""
    event = parse_await_event(step)
    if await_event_satisfied(step, answer_kwargs=answer_kwargs, artifacts=artifacts):
        payload = {
            "event": event,
            "status": "satisfied",
            "signals": await_signals(answer_kwargs, artifacts),
        }
        artifacts[f"await:{step.id}"] = payload
        trace.append(
            {
                "step": "plan.await.satisfied",
                "step_id": step.id,
                "metadata": {"event": event, "signals": sorted(payload["signals"].keys())},
            }
        )
        return {"status": "completed", "artifact": payload}
    if await_timeout_elapsed(step, artifacts):
        then_mode = parse_await_then(step)
        payload = {"event": event, "status": "timeout", "then": then_mode}
        artifacts[f"await:{step.id}"] = payload
        trace.append(
            {
                "step": "plan.await.timeout",
                "step_id": step.id,
                "metadata": {"event": event, "then": then_mode, "timeout_seconds": parse_await_timeout_seconds(step)},
            }
        )
        if then_mode == "blocked":
            return {"status": "blocked", "reason": "await_timeout", "artifact": payload}
        return {"status": "completed", "artifact": payload}
    artifacts[f"await:{step.id}"] = {
        "event": event,
        "status": "pending",
        "started_at": time.time(),
        "timeout_seconds": parse_await_timeout_seconds(step),
    }
    trace.append(
        {
            "step": "plan.await.pending",
            "step_id": step.id,
            "metadata": {"event": event, "timeout_seconds": parse_await_timeout_seconds(step)},
        }
    )
    return {"status": "awaiting", "artifact": artifacts[f"await:{step.id}"]}


def service_should_continue(step: PlanStep, answer_kwargs: dict[str, Any]) -> bool:
    if answer_kwargs.get("service_continue"):
        return True
    target = answer_kwargs.get("service_step_id")
    return bool(target and str(target) == step.id)


def execute_service_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
    answer_kwargs: dict[str, Any],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Run one SERVICE tick over direct body steps (resume with service_continue)."""
    body = direct_body_steps(step.id, steps)
    if not body:
        return {"status": "completed", "artifact": {"ticks": 0, "body": []}}
    state = artifacts.get(f"service:{step.id}") or {}
    ticks = int(state.get("ticks") or 0)
    if ticks > 0 and not service_should_continue(step, answer_kwargs):
        return {"status": "completed", "artifact": {**state, "resumed": False}}
    ticks += 1
    trace.append(
        {
            "step": "plan.service.tick",
            "step_id": step.id,
            "metadata": {"tick": ticks, "body": [item.id for item in body], "round": round_num},
        }
    )
    body_blocked = False
    for body_step in body:
        if body_step.status in {"completed", "skipped"} and ticks > 1:
            body_step.status = "pending"
            completed.discard(body_step.id)
        if body_step.status != "pending":
            continue
        if not all(dep in completed or dep == step.id for dep in body_step.depends_on):
            continue
        outcome = run_step(body_step)
        body_step.status = outcome["status"]
        trace.append(
            {
                "step": f"plan.step.{outcome['status']}",
                "step_id": body_step.id,
                "metadata": {
                    "construct": body_step.construct,
                    "reason": outcome.get("reason"),
                    "service_tick": ticks,
                    "round": round_num,
                },
            }
        )
        if outcome["status"] == "completed":
            completed.add(body_step.id)
            if outcome.get("artifact") is not None:
                artifacts[body_step.id] = outcome["artifact"]
        if outcome["status"] == "blocked":
            body_blocked = True
    payload = {
        "ticks": ticks,
        "body": [item.id for item in body],
        "blocked": body_blocked,
        "continue": service_should_continue(step, answer_kwargs),
    }
    artifacts[f"service:{step.id}"] = payload
    if body_blocked:
        return {"status": "blocked", "reason": "service_tick_blocked", "artifact": payload}
    return {"status": "completed", "artifact": payload}


def execute_concurrent_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    branch_runner: BranchRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
    isolated_branch_runner: Callable[[dict[str, Any], set[str]], BranchRunner] | None = None,
    shared_lock: Any | None = None,
) -> dict[str, Any]:
    """Run CONCURRENT branches without a MERGE join (non-joining fan-out)."""
    branches = direct_body_steps(step.id, steps)
    state = parse_parallel_state(step)
    execution_mode = parse_parallel_mode(step)
    if not branches:
        return {
            "status": "completed",
            "artifact": {"branches": [], "state": state, "execution_mode": execution_mode},
        }
    branch_payload: dict[str, Any] = {}

    def run_branch(branch: PlanStep) -> tuple[dict[str, Any], dict[str, Any]]:
        if execution_mode == "concurrent" and state == "isolated" and isolated_branch_runner is not None:
            local_artifacts = isolated_artifacts_snapshot(artifacts)
            local_completed = set(completed)
            local_trace: list[dict[str, Any]] = []
            trace.append(
                {
                    "step": "plan.concurrent.branch",
                    "step_id": step.id,
                    "metadata": {"branch": branch.id, "state": state, "round": round_num, "execution_mode": execution_mode},
                }
            )
            outcome = execute_branch_subtree(
                step,
                steps=steps,
                completed=local_completed,
                artifacts=local_artifacts,
                selected_ids=[branch.id],
                branch_runner=isolated_branch_runner(local_artifacts, local_completed),
                trace=local_trace,
                round_num=round_num,
                inline_kind="parallel",
            )
            trace.extend(local_trace)
            completed.update(local_completed)
            payload = {
                **dict(local_artifacts.get(branch.id) or {}),
                "context_bundle": copy.deepcopy(local_artifacts.get("context_bundle") or {"tool_results": []}),
            }
            return outcome, payload
        runner = branch_runner
        if execution_mode == "concurrent" and state == "shared" and shared_lock is not None:
            from tirzah.planning.parallel_runtime import shared_branch_runner

            runner = shared_branch_runner(branch_runner, artifacts, shared_lock)
        trace.append(
            {
                "step": "plan.concurrent.branch",
                "step_id": step.id,
                "metadata": {"branch": branch.id, "state": state, "round": round_num, "execution_mode": execution_mode},
            }
        )
        outcome = execute_branch_subtree(
            step,
            steps=steps,
            completed=completed,
            artifacts=artifacts,
            selected_ids=[branch.id],
            branch_runner=runner,
            trace=trace,
            round_num=round_num,
            inline_kind="parallel",
        )
        return outcome, dict(artifacts.get(branch.id) or {})

    if execution_mode == "concurrent":
        results = run_branches_concurrently(branches, run_branch=run_branch)
        for branch_id, outcome, payload in results:
            if outcome["status"] == "blocked":
                return outcome
            branch_payload[branch_id] = payload
    else:
        for branch in branches:
            outcome, payload = run_branch(branch)
            if outcome["status"] == "blocked":
                return outcome
            branch_payload[branch.id] = payload

    concurrent_artifact = {
        "state": state,
        "execution_mode": execution_mode,
        "branch_ids": [branch.id for branch in branches],
        "branches": branch_payload,
    }
    artifacts[f"concurrent:{step.id}"] = concurrent_artifact
    trace.append(
        {
            "step": "plan.concurrent.completed",
            "step_id": step.id,
            "metadata": {
                "state": state,
                "execution_mode": execution_mode,
                "branch_ids": concurrent_artifact["branch_ids"],
                "round": round_num,
            },
        }
    )
    return {"status": "completed", "artifact": concurrent_artifact}


def suggest_plan_profile_hint(query: str, answer_kwargs: dict[str, Any], config: Any) -> str:
    runtime = getattr(config, "runtime", None)
    web = bool(answer_kwargs.get("web_research") or getattr(runtime, "web_research_enabled", False))
    mode = str(answer_kwargs.get("retrieval_mode") or getattr(runtime, "retrieval_mode", "direct")).lower()
    word_count = len(str(query or "").split())
    if web:
        return "Use granular context profile: search_nodes → compile_context → web_search → web_fetch → answer_adapter."
    if mode in {"agentic", "deep"}:
        return "Prefer granular context with graph expansion (search_nodes, compile_context, expand_proximity) unless a monolithic backbone is clearly sufficient."
    if word_count >= 14:
        return "Long request: consider granular gather steps with explicit ITERATE bounds instead of one monolithic retrieval."
    return "Prefer monolithic tirzah_retrieval → answer_adapter unless the request needs scoped DB/web steps or revision-friendly gather."

# --- QUEUE (turn-based / round-robin multi-agent discussion) ---------------


def parse_queue_order(step: PlanStep) -> str:
    """FIFO (default) | PRIORITY | ROUND_ROBIN, read from the step text."""
    text = " ".join([step.action, *step.success_criteria]).upper()
    match = re.search(r"ORDER:\s*(ROUND[_ ]?ROBIN|PRIORITY|FIFO)", text)
    if match:
        return match.group(1).replace(" ", "_")
    if "ROUND_ROBIN" in text or "ROUND ROBIN" in text or "ROUND-ROBIN" in text:
        return "ROUND_ROBIN"
    if "PRIORITY" in text:
        return "PRIORITY"
    return "FIFO"


def parse_queue_rounds(step: PlanStep, *, default: int = 1, ceiling: int = 10) -> int:
    """Number of full round-robin cycles (ROUNDS: n, else MAX: n, else default)."""
    text = " ".join([step.action, *step.success_criteria])
    match = re.search(r"(?:ROUNDS|MAX):\s*(\d+)", text, re.I)
    if match:
        return max(1, min(int(match.group(1)), ceiling))
    return max(1, min(default, ceiling))


def participant_priority(step: PlanStep) -> int:
    """Higher runs earlier under ORDER: PRIORITY (priority:N in success_criteria)."""
    for item in step.success_criteria:
        match = re.match(r"\s*priority:\s*(-?\d+)", item, re.I)
        if match:
            return int(match.group(1))
    return 0


def _ordered_participants(step: PlanStep, participants: list[PlanStep]) -> list[PlanStep]:
    order = parse_queue_order(step)
    if order == "PRIORITY":
        return sorted(
            participants,
            key=lambda p: (-participant_priority(p), participants.index(p)),
        )
    return list(participants)  # FIFO / ROUND_ROBIN keep document order


def _queue_converged(step: PlanStep, artifacts: dict[str, Any]) -> bool:
    """Early-stop for a bounded discussion: honoured only when the process asks
    for it (UNTIL: consensus/converged/done). A round converges when a turn's
    artifact carries a truthy converged/consensus/done/stop flag, OR its output
    text contains a convergence marker."""
    until = [
        item.split(":", 1)[1].strip().lower()
        for item in step.success_criteria
        if item.lower().startswith("until:")
    ]
    wants_convergence = any(
        token in {"consensus", "converged", "convergence", "done", "agreement"}
        for token in until
    )
    if not wants_convergence:
        return False
    state = artifacts.get(f"queue:{step.id}") or {}
    for turn in state.get("last_round_turns") or []:
        artifact = turn.get("artifact") or {}
        if any(bool(artifact.get(flag)) for flag in ("converged", "consensus", "done", "stop")):
            return True
        text = str(artifact.get("output") or artifact.get("answer") or "").lower()
        if any(marker in text for marker in ("consensus reached", "we agree", "converged", "no further")):
            return True
    return False


def execute_queue_step(
    step: PlanStep,
    *,
    steps: list[PlanStep],
    completed: set[str],
    artifacts: dict[str, Any],
    run_step: StepRunner,
    trace: list[dict[str, Any]],
    round_num: int | None = None,
) -> dict[str, Any]:
    """Run the QUEUE's participants turn-by-turn (Cairn §5 QUEUE).

    Semantics for round-robin multi-agent discussion:
    - Direct body steps are the *participants* (each turn is a CALL to an agent).
    - ONE_AT_A_TIME: turns run serially, so each turn sees prior turns' outputs
      (state is SHARED across the queue — this is what makes it a discussion, in
      contrast to PARALLEL's isolated branches).
    - ORDER: FIFO (document order, one pass), PRIORITY (by priority:N), or
      ROUND_ROBIN (cycle every participant for up to ROUNDS/MAX rounds).
    - UNTIL: consensus/converged/done stops the discussion early once a turn
      signals it (bounded convergence); otherwise it runs the full round count.
    A running transcript of every turn is accumulated in the queue artifact so
    the outcome is auditable and downstream steps can read the discussion.
    """
    participants = direct_body_steps(step.id, steps)
    if not participants:
        return {"status": "completed", "artifact": {"order": parse_queue_order(step), "participants": [], "transcript": []}}

    order = parse_queue_order(step)
    ordered = _ordered_participants(step, participants)
    rounds = parse_queue_rounds(step) if order == "ROUND_ROBIN" else 1

    transcript: list[dict[str, Any]] = []
    blocked = False
    rounds_run = 0
    converged = False

    for round_index in range(1, rounds + 1):
        rounds_run = round_index
        last_round_turns: list[dict[str, Any]] = []
        trace.append({
            "step": "plan.queue.round",
            "step_id": step.id,
            "metadata": {"round": round_index, "order": order, "of": rounds},
        })
        for turn_index, participant in enumerate(ordered, start=1):
            # Re-run participants each round (round-robin turns); state carried in
            # `artifacts` means each turn reads the accumulating discussion.
            if participant.status in {"completed", "skipped", "blocked"} and round_index > 1:
                participant.status = "pending"
                completed.discard(participant.id)
            if participant.status != "pending":
                continue
            if not all(dep in completed or dep == step.id for dep in participant.depends_on):
                continue
            trace.append({
                "step": "plan.queue.turn",
                "step_id": step.id,
                "metadata": {"participant": participant.id, "round": round_index, "turn": turn_index},
            })
            outcome = run_step(participant)
            participant.status = outcome["status"]
            entry = {
                "participant": participant.id,
                "round": round_index,
                "turn": turn_index,
                "status": outcome["status"],
                "artifact": outcome.get("artifact"),
            }
            transcript.append(entry)
            last_round_turns.append(entry)
            trace.append({
                "step": f"plan.step.{outcome['status']}",
                "step_id": participant.id,
                "metadata": {"construct": participant.construct, "reason": outcome.get("reason"),
                             "queue_round": round_index},
            })
            if outcome["status"] == "completed":
                completed.add(participant.id)
                if outcome.get("artifact") is not None:
                    artifacts[participant.id] = outcome["artifact"]
            if outcome["status"] == "blocked":
                blocked = True

        # Persist the round so _queue_converged can read it, then check.
        artifacts[f"queue:{step.id}"] = {
            "order": order, "rounds_run": rounds_run, "transcript": transcript,
            "last_round_turns": last_round_turns, "participant_ids": [p.id for p in ordered],
        }
        if blocked:
            break
        if order == "ROUND_ROBIN" and _queue_converged(step, artifacts):
            converged = True
            trace.append({
                "step": "plan.queue.converged",
                "step_id": step.id,
                "metadata": {"round": round_index},
            })
            break

    payload = {
        "order": order,
        "participant_ids": [p.id for p in ordered],
        "rounds_run": rounds_run,
        "converged": converged,
        "transcript": transcript,
    }
    artifacts[f"queue:{step.id}"] = payload
    if blocked:
        return {"status": "blocked", "reason": "queue_turn_blocked", "artifact": payload}
    return {"status": "completed", "artifact": payload}
