"""Process enforcement — bind the active process to plan execution.

Three responsibilities:

1. **Constraint injection** — render the active instance's process text as a
   planning-context block the interpretive planner must plan within, and
   instruct it to place explicit gate steps where the process demands human
   approval (``render_process_constraint``).

2. **Gate + deviation semantics** — recognise, from the process prose, where a
   human gate is required (``process_requires_gate``); a gate pauses the
   instance (``awaiting_gate``) until an operator approves (resumable), and a
   material departure from the process is flagged for approval
   (``flag_deviation``) rather than silently taken.

3. **Emergency override** — record an override with mandatory justification
   (``record_override``); the instance still carries the full trace, and its
   process (Emergency preset) mandates a retrospective.

The enforcement layer writes only through ``instances.record_event`` and emits
best-effort Galeed events, so a running request is never broken by process
bookkeeping.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database

from tirzah.process import instances as inst

# Phrases in a process body that mark a required human gate. Matched
# case-insensitively; deliberately prose-level so operators author gates in
# plain English ("pause for approval") without a rigid syntax.
_GATE_MARKERS = (
    "pause for approval",
    "pause and seek approval",
    "human approval",
    "human gate",
    "require approval",
    "requires approval",
    "await approval",
    "gate:",
)

_OVERRIDE_MARKERS = (
    "gates are suspended",
    "normal gates are suspended",
    "override",
    "act immediately",
    "act first",
)


def process_requires_gate(process_body: str) -> bool:
    """True when the process text calls for at least one human gate."""
    lowered = (process_body or "").lower()
    return any(marker in lowered for marker in _GATE_MARKERS)


def process_is_override(process_body: str) -> bool:
    """True when the process is an emergency/override style (gates suspended)."""
    lowered = (process_body or "").lower()
    return any(marker in lowered for marker in _OVERRIDE_MARKERS)


def render_process_constraint(instance: dict[str, Any]) -> str:
    """The planning-context block that makes the planner plan *within* the
    active process. Prepended to the planner's context so it is the top-level
    guide for the plan."""
    if not instance:
        return ""
    body = instance.get("process_body") or ""
    name = instance.get("template_name") or "the active process"
    lines = [
        "ACTIVE PROCESS — you MUST plan within this human-defined process. It is",
        "the top-level guide for how this work proceeds; honour its gates, loops,",
        f"and deviation rules. Process name: {name}.",
        "",
        body.strip(),
        "",
    ]
    if process_requires_gate(body):
        lines += [
            "This process requires human approval gates. Where it says to pause for",
            "approval, emit an explicit AWAIT step (construct: AWAIT) that waits for",
            "an operator signal before the step that applies a change or ships work —",
            "do not merge a gated action into an ungated step.",
            "",
        ]
    if process_is_override(body):
        lines += [
            "This is an emergency/override process: normal gates are suspended for",
            "speed, but every action must be recorded and a retrospective is",
            "mandatory afterward.",
            "",
        ]
    lines += [
        "Flag any material deviation from this process rather than silently taking",
        "it. Deviations require operator approval.",
    ]
    return "\n".join(lines)


def note_plan_shaped(
    db: Database, instance_id: str, *, plan_id: str, has_gate_steps: bool
) -> None:
    """Record that a plan was created under the process (adherence signal)."""
    inst.record_event(
        db,
        instance_id,
        "process.plan.shaped",
        {"plan_id": plan_id, "has_gate_steps": has_gate_steps},
    )
    _emit(instance_id, "process.plan.shaped", {"plan_id": plan_id, "has_gate_steps": has_gate_steps})


def reach_gate(
    db: Database, instance_id: str, *, step_id: str, reason: str = ""
) -> dict[str, Any] | None:
    """The instance hits a human gate: pause (awaiting_gate), resumable on
    approval."""
    updated = inst.record_event(
        db,
        instance_id,
        "process.gate.reached",
        {"step_id": step_id, "reason": reason},
        status="awaiting_gate",
    )
    _emit(instance_id, "process.gate.reached", {"step_id": step_id})
    return updated


def resolve_gate(
    db: Database,
    instance_id: str,
    *,
    step_id: str,
    approved: bool,
    approver: str = "operator",
    note: str | None = None,
) -> dict[str, Any] | None:
    """Operator decision at a gate. Approval resumes the instance (active);
    rejection sends it back for iteration (recorded, stays active so the flow
    can return to an earlier step)."""
    event = "process.gate.approved" if approved else "process.gate.rejected"
    updated = inst.record_event(
        db,
        instance_id,
        event,
        {"step_id": step_id, "approver": approver, "note": note},
        status="active",
    )
    _emit(instance_id, event, {"step_id": step_id, "approver": approver})
    return updated


def flag_deviation(
    db: Database,
    instance_id: str,
    *,
    description: str,
    step_id: str | None = None,
    proposed_by: str = "agent",
) -> dict[str, Any] | None:
    """An agent flags a material departure from the process and seeks approval
    (the instance pauses at a gate until the operator resolves it)."""
    updated = inst.record_event(
        db,
        instance_id,
        "process.deviation.flagged",
        {"description": description, "step_id": step_id, "proposed_by": proposed_by},
        status="awaiting_gate",
    )
    _emit(instance_id, "process.deviation.flagged", {"description": description[:120]})
    return updated


def resolve_deviation(
    db: Database,
    instance_id: str,
    *,
    approved: bool,
    approver: str = "operator",
    note: str | None = None,
) -> dict[str, Any] | None:
    event = "process.deviation.approved" if approved else "process.deviation.rejected"
    updated = inst.record_event(
        db,
        instance_id,
        event,
        {"approver": approver, "note": note},
        status="active",
    )
    _emit(instance_id, event, {"approver": approver})
    return updated


def record_override(
    db: Database,
    instance_id: str,
    *,
    justification: str,
    actor: str = "operator",
) -> dict[str, Any] | None:
    """Emergency override: skip the normal gates with a required justification.
    The override is logged and (per the Emergency process) subject to a
    mandatory retrospective."""
    if not justification.strip():
        raise ValueError("an emergency override requires a justification")
    updated = inst.record_event(
        db,
        instance_id,
        "process.override.invoked",
        {"justification": justification.strip(), "actor": actor},
        status="active",
    )
    _emit(instance_id, "process.override.invoked", {"actor": actor})
    return updated


def _emit(instance_id: str, event: str, detail: dict[str, Any]) -> None:
    """Best-effort Galeed spine event so process actions are watchable in Mizpah
    alongside the rest of the request. Never raises."""
    try:
        from galeed import Tracer

        tracer = Tracer(trace_id=instance_id, session_id="process", source="tirzah")
        tracer.emit(event, summary=f"{event} on {instance_id}", **detail)
    except Exception:
        pass
