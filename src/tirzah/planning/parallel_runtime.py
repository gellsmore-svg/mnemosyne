"""Concurrent PARALLEL branch execution with isolated or locked shared artifacts."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

BranchRunner = Callable[[Any], dict[str, Any]]


def run_branches_concurrently(
    branches: list[Any],
    *,
    run_branch,
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Run branch jobs; each returns (branch_id, outcome, branch_payload)."""
    if not branches:
        return []
    if len(branches) == 1:
        branch = branches[0]
        outcome, payload = run_branch(branch)
        return [(branch.id, outcome, payload)]
    results: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=len(branches)) as pool:
        futures = {pool.submit(run_branch, branch): branch for branch in branches}
        for future in as_completed(futures):
            branch = futures[future]
            outcome, payload = future.result()
            results.append((branch.id, outcome, payload))
    order = {branch.id: index for index, branch in enumerate(branches)}
    results.sort(key=lambda item: order.get(item[0], 0))
    return results


def isolated_artifacts_snapshot(artifacts: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(artifacts)
    snapshot["context_bundle"] = {"tool_results": []}
    return snapshot


def shared_branch_runner(
    base_runner: BranchRunner,
    artifacts: dict[str, Any],
    lock: threading.Lock,
) -> BranchRunner:
    def runner(step: Any) -> dict[str, Any]:
        with lock:
            return base_runner(step)

    return runner