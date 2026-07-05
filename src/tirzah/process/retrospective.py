"""Process retrospectives + audit queries.

Turns an instance's append-only trace into a reviewable retrospective (paths
taken, gates, deviations, override, outcome) and rolls instances up into usage
metrics (adherence, deviation rate, outcomes, velocity). Also answers the
historical question the spec calls for — *"how were similar tasks handled
previously?"* — by matching prior instances on template and task text.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from tirzah.process import instances as inst

# Trace events that count as deviations / gates for the metrics.
_DEVIATION_EVENTS = {"process.deviation.flagged"}
_GATE_EVENTS = {"process.gate.reached"}
_OVERRIDE_EVENTS = {"process.override.invoked"}


def build_retrospective(db: Database, instance_id: str) -> dict[str, Any] | None:
    """A structured review of one instance: its path, gates, deviations,
    override, outcome, and a plain-language summary."""
    instance = inst.get_instance(db, instance_id)
    if instance is None:
        return None
    trace = instance.get("trace") or []
    gates = [e for e in trace if e["event"] in _GATE_EVENTS]
    deviations = [e for e in trace if e["event"] in _DEVIATION_EVENTS]
    overrides = [e for e in trace if e["event"] in _OVERRIDE_EVENTS]
    approvals = [e for e in trace if e["event"] == "process.gate.approved"]
    rejections = [e for e in trace if e["event"] == "process.gate.rejected"]

    summary = _summarise(instance, gates, deviations, overrides, rejections)
    return {
        "instance_id": instance_id,
        "template_id": instance.get("template_id"),
        "template_name": instance.get("template_name"),
        "template_version": instance.get("template_version"),
        "task": instance.get("task"),
        "status": instance.get("status"),
        "outcome": instance.get("outcome"),
        "started_at": instance.get("started_at"),
        "completed_at": instance.get("completed_at"),
        "counts": {
            "trace_events": len(trace),
            "gates": len(gates),
            "gate_approvals": len(approvals),
            "gate_rejections": len(rejections),
            "deviations": len(deviations),
            "overrides": len(overrides),
        },
        "gates": gates,
        "deviations": deviations,
        "overrides": overrides,
        "summary": summary,
        "trace": trace,
    }


def usage_metrics(
    db: Database, *, template_id: str | None = None
) -> dict[str, Any]:
    """Aggregate metrics across instances (optionally one template) to inform
    template evolution: adherence, deviation rate, outcomes, velocity."""
    instances = inst.list_instances(db, template_id=template_id, limit=500)
    total = len(instances)
    completed = [i for i in instances if i.get("status") == "completed"]
    abandoned = [i for i in instances if i.get("status") == "abandoned"]

    deviating = 0
    overriding = 0
    total_gates = 0
    for instance in instances:
        trace = instance.get("trace") or []
        if any(e["event"] in _DEVIATION_EVENTS for e in trace):
            deviating += 1
        if any(e["event"] in _OVERRIDE_EVENTS for e in trace):
            overriding += 1
        total_gates += sum(1 for e in trace if e["event"] in _GATE_EVENTS)

    outcomes: dict[str, int] = {}
    for instance in completed:
        outcome = instance.get("outcome") or "completed"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    def _rate(n: int) -> float | None:
        return round(n / total, 3) if total else None

    by_template: dict[str, int] = {}
    for instance in instances:
        key = instance.get("template_name") or instance.get("template_id") or "?"
        by_template[key] = by_template.get(key, 0) + 1

    return {
        "template_id": template_id,
        "total_instances": total,
        "completed": len(completed),
        "abandoned": len(abandoned),
        "completion_rate": _rate(len(completed)),
        "instances_with_deviations": deviating,
        "deviation_rate": _rate(deviating),
        "instances_with_override": overriding,
        "override_rate": _rate(overriding),
        "total_gates": total_gates,
        "avg_gates_per_instance": round(total_gates / total, 2) if total else None,
        "outcomes": outcomes,
        "by_template": by_template,
    }


def similar_task_history(
    db: Database,
    *,
    task: str,
    template_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """"How were similar tasks handled previously?" — prior instances whose task
    text shares words with ``task`` (optionally within one template), ranked by
    overlap, newest-first as the tiebreak. Lean rows (no full trace)."""
    query_words = _words(task)
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for instance in inst.list_instances(db, template_id=template_id, limit=500):
        overlap = len(query_words & _words(instance.get("task") or ""))
        if overlap == 0 and query_words:
            continue
        trace = instance.get("trace") or []
        scored.append((
            overlap,
            str(instance.get("started_at") or ""),
            {
                "instance_id": instance.get("instance_id"),
                "task": instance.get("task"),
                "template_name": instance.get("template_name"),
                "template_version": instance.get("template_version"),
                "status": instance.get("status"),
                "outcome": instance.get("outcome"),
                "deviations": sum(1 for e in trace if e["event"] in _DEVIATION_EVENTS),
                "gates": sum(1 for e in trace if e["event"] in _GATE_EVENTS),
                "started_at": instance.get("started_at"),
            },
        ))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[: max(1, min(int(limit), 100))]]


def _summarise(
    instance: dict[str, Any],
    gates: list[dict[str, Any]],
    deviations: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
) -> str:
    parts = [
        f"Task '{instance.get('task')}' ran under '{instance.get('template_name')}' "
        f"v{instance.get('template_version')} and ended {instance.get('status')}"
        + (f" ({instance.get('outcome')})" if instance.get("outcome") else "")
        + "."
    ]
    if gates:
        parts.append(
            f"{len(gates)} human gate(s); {len(rejections)} rejection(s) that returned to earlier steps."
        )
    if deviations:
        parts.append(f"{len(deviations)} flagged deviation(s) from the process.")
    if overrides:
        parts.append(f"{len(overrides)} emergency override(s) — review the justification(s).")
    if not gates and not deviations and not overrides:
        parts.append("No gates, deviations, or overrides recorded.")
    return " ".join(parts)


def _words(text: str) -> set[str]:
    return {
        token
        for token in "".join(c.lower() if c.isalnum() else " " for c in (text or "")).split()
        if len(token) > 2
    }
