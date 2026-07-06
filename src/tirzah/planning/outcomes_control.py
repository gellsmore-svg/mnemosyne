"""Live control for the outcomes-validation loop (family #5, phase 2).

Wires ``process.outcomes`` into the recursive planner. When the active process
instance has an **armed** outcomes loop (declared outcomes + an ``outcomes_loop``
config), after each execution we validate the accumulated work against the
frozen outcomes and, on drift:

* **re-anchor** — name the drifted outcomes back to the planner on the next
  revision (``on_drift`` ∈ {reanchor, reanchor_then_gate});
* **gate** — when a revision proposes to *complete/stabilise* while the work is
  still drifting, raise a human gate and block completion (``on_drift`` ∈
  {gate, reanchor_then_gate}). The operator resolves it with the ordinary
  ``resolve_gate`` — approve = accept despite drift (the override), reject =
  keep going.

The gate only fires when the **deterministic** floor also says an outcome is
unmet, honouring the family rule: never auto-fail on a model judgement alone.

Everything here is inert unless a template author armed a loop, so the default
planner path is unchanged.
"""

from __future__ import annotations

from typing import Any, Callable

from tirzah.process import enforcement as _enf
from tirzah.process import instances as _inst
from tirzah.process import outcomes as _oc

AskFn = Callable[[str], str]

_DRIFT_GATE_STEP = "outcomes:drift"


class OutcomesController:
    """Per-request live control over an instance's armed outcomes loop."""

    def __init__(
        self, db: Any, instance: dict[str, Any], *, ask: AskFn | None = None
    ) -> None:
        self.db = db
        self.instance = instance
        self.instance_id = str(instance.get("instance_id"))
        self.loop = instance.get("outcomes_loop") or {}
        self.on_drift = str(self.loop.get("on_drift") or _oc.DEFAULT_ON_DRIFT)
        self.ask = ask
        self.last: dict[str, Any] | None = None
        self.gated = False

    # --- assessment --------------------------------------------------------

    def assess(self, result: dict[str, Any]) -> dict[str, Any]:
        """Validate the work so far; record + emit; cache and return it."""
        work = {
            "answer": result.get("answer") or result.get("message"),
            "objective": result.get("objective"),
            "artifacts": result.get("artifacts"),
            "trace": result.get("process_trace"),
        }
        validation = _oc.validate_outcomes(self.instance, work, ask=self.ask)
        self.last = validation
        detail = {
            "drift_score": validation.get("drift_score"),
            "drifting": validation.get("drifting"),
            "statuses": {o["id"]: o["status"] for o in validation.get("per_outcome", [])},
        }
        _record(self.db, self.instance_id, _oc.OUTCOMES_VALIDATED, detail)
        if validation.get("ready") and validation.get("drift_score") == 0.0:
            _record(self.db, self.instance_id, _oc.OUTCOMES_MET, {})
        elif validation.get("drifting"):
            _record(self.db, self.instance_id, _oc.OUTCOMES_DRIFT, detail)
        return validation

    # --- re-anchor ---------------------------------------------------------

    def wants_reanchor(self, validation: dict[str, Any] | None) -> bool:
        return bool(
            validation
            and validation.get("drifting")
            and self.on_drift in ("reanchor", "reanchor_then_gate")
        )

    def reanchor_information(
        self, information: dict[str, Any], validation: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Return ``information`` with an ``outcome_reanchor`` note when drifting."""
        if not self.wants_reanchor(validation):
            return information
        constraint = _oc.render_reanchor_constraint(validation or {})
        if not constraint:
            return information
        _record(
            self.db, self.instance_id, _oc.OUTCOMES_REANCHORED,
            {"outcomes": [o["id"] for o in (validation or {}).get("per_outcome", []) if o["status"] != "met"]},
        )
        return {**information, "outcome_reanchor": constraint}

    # --- completion gate ---------------------------------------------------

    def should_gate_completion(self, validation: dict[str, Any] | None) -> bool:
        """Block a proposed completion when drifting — but only if the
        deterministic floor agrees an outcome is unmet (never on a model alone)."""
        if not validation or not validation.get("drifting"):
            return False
        if self.on_drift not in ("gate", "reanchor_then_gate"):
            return False
        floor_unmet = any(
            o.get("deterministic_status") == "unmet"
            for o in validation.get("per_outcome", [])
        )
        return floor_unmet

    def raise_drift_gate(self, validation: dict[str, Any]) -> dict[str, Any]:
        """Pause the instance at a drift gate; return a summary for the result."""
        unmet = [o for o in validation.get("per_outcome", []) if o["status"] != "met"]
        reason = "outcome drift: " + ", ".join(
            f"{o['id']}={o['status']}" for o in unmet
        )
        _enf.reach_gate(self.db, self.instance_id, step_id=_DRIFT_GATE_STEP, reason=reason)
        self.gated = True
        return {
            "gate": "outcomes_drift",
            "step_id": _DRIFT_GATE_STEP,
            "reason": reason,
            "drift_score": validation.get("drift_score"),
            "unmet_outcomes": [{"id": o["id"], "statement": o["statement"]} for o in unmet],
            "resolve_with": "process gate approve/reject (approve accepts despite drift)",
        }


def active_outcomes_controller(
    db: Any, session_id: str | None, *, ask: AskFn | None = None, config: Any = None
) -> OutcomesController | None:
    """A controller iff an active instance for the session has an armed loop.

    When the loop's ``judge`` is ``llm`` and ``config`` is provided, the model
    judgement tier is wired from the process refinement ``default_ask`` (best
    effort; the deterministic floor always stands if it is unavailable).
    """
    if db is None or not session_id:
        return None
    try:
        instance = _inst.active_instance_for_session(db, session_id)
    except Exception:  # noqa: BLE001 - never break the planner on a lookup
        return None
    if not instance:
        return None
    if not (instance.get("process_outcomes") and instance.get("outcomes_loop")):
        return None
    if ask is None and config is not None:
        judge = str((instance.get("outcomes_loop") or {}).get("judge") or "deterministic")
        if judge == "llm":
            try:
                from tirzah.process.refinement import default_ask

                ask = default_ask(config)
            except Exception:  # noqa: BLE001 - fall back to the deterministic floor
                ask = None
    return OutcomesController(db, instance, ask=ask)


def _record(db: Any, instance_id: str, event: str, detail: dict[str, Any]) -> None:
    """Append to the instance trace + best-effort Galeed emit; never raises."""
    try:
        _inst.record_event(db, instance_id, event, detail)
    except Exception:
        pass
    try:
        _enf._emit(instance_id, event, detail)  # noqa: SLF001 - shared emit helper
    except Exception:
        pass
