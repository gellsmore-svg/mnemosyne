"""Process templates — versioned, human-authored process definitions.

Storage model (``process_templates`` collection): one document per *version*.
``template_id`` is stable across versions; each edit appends a new version
document (history is never overwritten). "The template" at any moment is its
highest-version document. Presets are ordinary templates seeded once with
``is_preset=True``.

A template is deliberately plain text (``body``): the process is human-readable
prose, and no rigid schema is imposed on it. The structured fields around it
(name, category, risk_level, scope) exist only for selection and querying.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pymongo.database import Database

from tirzah.db.governance import serialize_governance_row

RISK_LEVELS = ("low", "medium", "high")
SCOPES = ("product", "feature", "task", "bugfix")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_template_id() -> str:
    return f"proc_tmpl_{uuid4().hex[:12]}"


def create_template(
    db: Database,
    *,
    name: str,
    body: str,
    description: str = "",
    category: str | None = None,
    risk_level: str | None = None,
    scope: str | None = None,
    created_by: str = "operator",
    is_preset: bool = False,
    template_id: str | None = None,
    outcomes: Any = None,
    outcomes_loop: Any = None,
) -> dict[str, Any]:
    """Create version 1 of a new template. Returns the stored version document.

    ``outcomes`` (a list of ``{id, statement, check}``) and ``outcomes_loop``
    (cadence / drift_threshold / on_drift) are optional; when present they arm the
    outcomes-validation loop for instances of this template.
    """
    if not name.strip():
        raise ValueError("template name is required")
    if not body.strip():
        raise ValueError("template body is required")
    if risk_level is not None and risk_level not in RISK_LEVELS:
        raise ValueError(f"invalid risk_level: {risk_level!r} (allowed: {list(RISK_LEVELS)})")
    from tirzah.process.outcomes import normalize_outcomes, normalize_outcomes_loop

    document = {
        "template_id": template_id or _new_template_id(),
        "version": 1,
        "name": name.strip(),
        "description": description.strip(),
        "category": category,
        "risk_level": risk_level,
        "scope": scope,
        "body": body,
        "outcomes": normalize_outcomes(outcomes),
        "outcomes_loop": normalize_outcomes_loop(outcomes_loop),
        "is_preset": is_preset,
        "created_by": created_by,
        "created_at": _utcnow(),
    }
    db.process_templates.insert_one({**document})
    return serialize_governance_row(document)


def revise_template(
    db: Database,
    template_id: str,
    *,
    body: str | None = None,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    risk_level: str | None = None,
    scope: str | None = None,
    created_by: str = "operator",
    provenance: dict[str, Any] | None = None,
    outcomes: Any = None,
    outcomes_loop: Any = None,
) -> dict[str, Any]:
    """Append a new version to an existing template (history preserved).

    Unspecified fields carry forward from the current latest version, so an
    edit that only changes the body keeps the rest. Returns the new version.
    """
    current = latest_template(db, template_id)
    if current is None:
        raise ValueError(f"unknown template: {template_id}")
    if risk_level is not None and risk_level not in RISK_LEVELS:
        raise ValueError(f"invalid risk_level: {risk_level!r} (allowed: {list(RISK_LEVELS)})")
    from tirzah.process.outcomes import normalize_outcomes, normalize_outcomes_loop

    document = {
        "template_id": template_id,
        "version": int(current["version"]) + 1,
        "name": (name if name is not None else current["name"]).strip() or current["name"],
        "description": description if description is not None else current.get("description", ""),
        "category": category if category is not None else current.get("category"),
        "risk_level": risk_level if risk_level is not None else current.get("risk_level"),
        "scope": scope if scope is not None else current.get("scope"),
        "body": body if body is not None else current["body"],
        "outcomes": (
            normalize_outcomes(outcomes)
            if outcomes is not None
            else current.get("outcomes", [])
        ),
        "outcomes_loop": (
            normalize_outcomes_loop(outcomes_loop)
            if outcomes_loop is not None
            else current.get("outcomes_loop")
        ),
        "is_preset": current.get("is_preset", False),
        "created_by": created_by,
        "created_at": _utcnow(),
    }
    if provenance is not None:
        # e.g. {"kind": "evolution", "rationale": …, "based_on_instances": N}.
        document["provenance"] = dict(provenance)
    db.process_templates.insert_one({**document})
    return serialize_governance_row(document)


def latest_template(db: Database, template_id: str) -> dict[str, Any] | None:
    """The highest-version document for a template id (None if unknown)."""
    rows = list(
        db.process_templates.find({"template_id": template_id}, {"_id": 0})
    )
    if not rows:
        return None
    latest = max(rows, key=lambda row: int(row.get("version") or 0))
    return serialize_governance_row(latest)


def get_template(db: Database, template_id: str, version: int | None = None) -> dict[str, Any] | None:
    """A specific version (default: latest)."""
    if version is None:
        return latest_template(db, template_id)
    row = db.process_templates.find_one(
        {"template_id": template_id, "version": int(version)}, {"_id": 0}
    )
    return serialize_governance_row(row) if row else None


def template_versions(db: Database, template_id: str) -> list[dict[str, Any]]:
    """All versions of a template, oldest → newest."""
    rows = list(db.process_templates.find({"template_id": template_id}, {"_id": 0}))
    rows.sort(key=lambda row: int(row.get("version") or 0))
    return [serialize_governance_row(row) for row in rows]


def list_templates(
    db: Database,
    *,
    category: str | None = None,
    risk_level: str | None = None,
    scope: str | None = None,
    include_presets: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """The current (latest-version) document of every template, newest first.

    Filters apply to the latest version. Presets are included by default.
    """
    latest_by_id: dict[str, dict[str, Any]] = {}
    for row in db.process_templates.find({}, {"_id": 0}):
        tid = row.get("template_id")
        version = int(row.get("version") or 0)
        if tid not in latest_by_id or version > int(latest_by_id[tid].get("version") or 0):
            latest_by_id[tid] = row
    rows = list(latest_by_id.values())
    if not include_presets:
        rows = [r for r in rows if not r.get("is_preset")]
    if category is not None:
        rows = [r for r in rows if r.get("category") == category]
    if risk_level is not None:
        rows = [r for r in rows if r.get("risk_level") == risk_level]
    if scope is not None:
        rows = [r for r in rows if r.get("scope") == scope]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return [serialize_governance_row(row) for row in rows[: max(1, min(int(limit), 500))]]


# --- Presets ---------------------------------------------------------------
# Curated defaults spanning the velocity↔governance spectrum. The bodies are
# plain-text processes the planner reads as constraints; gate points are stated
# in prose ("pause for approval") — the enforcement layer recognises them.

PRESET_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "preset_governed",
        "name": "Governed",
        "description": "Heavy oversight for new products, high-risk features, or "
        "anything touching production. Human gates before applying or shipping.",
        "category": "governance",
        "risk_level": "high",
        "scope": "product",
        "body": (
            "GOVERNED PROCESS (heavy / high-oversight)\n"
            "\n"
            "1. Understand the request and restate the goal and constraints.\n"
            "2. Gather context and evidence before proposing any change.\n"
            "3. Propose a plan of the concrete steps. PAUSE FOR APPROVAL before\n"
            "   executing any step that applies a change or ships work.\n"
            "4. Execute one approved step at a time. On review rejection, return to\n"
            "   the relevant earlier step and iterate.\n"
            "5. PAUSE FOR APPROVAL before finalising or shipping.\n"
            "6. Flag and seek approval for any deviation from this process.\n"
            "\n"
            "Human grounding is required at every gate; velocity is secondary to\n"
            "reliability and auditability."
        ),
    },
    {
        "template_id": "preset_fluid",
        "name": "Fluid",
        "description": "Lightweight, fast-moving process for low-risk work, "
        "experiments, and iteration. Oversight is observational (log-only).",
        "category": "governance",
        "risk_level": "low",
        "scope": "feature",
        "body": (
            "FLUID PROCESS (light / high-velocity)\n"
            "\n"
            "1. Understand the request and proceed with sensible defaults.\n"
            "2. Gather just enough context, then act.\n"
            "3. Iterate freely in review/fix loops until the work is good.\n"
            "4. No mandatory human gates — oversight is observational: log the\n"
            "   steps taken and any notable decisions for later review.\n"
            "5. If risk turns out higher than expected, stop and recommend\n"
            "   switching to a governed process.\n"
            "\n"
            "Favour velocity; keep a clean trace so a retrospective can learn\n"
            "from it."
        ),
    },
    {
        "template_id": "preset_emergency",
        "name": "Emergency",
        "description": "Urgent-issue override: act first to resolve, then justify. "
        "Mandatory post-hoc review.",
        "category": "governance",
        "risk_level": "high",
        "scope": "task",
        "body": (
            "EMERGENCY PROCESS (override / act-first)\n"
            "\n"
            "1. Act immediately to contain or resolve the urgent issue; normal\n"
            "   gates are suspended.\n"
            "2. Record every action taken and the reasoning, in real time.\n"
            "3. State a clear justification for the emergency override.\n"
            "4. MANDATORY RETROSPECTIVE afterward: review what was done, whether\n"
            "   the override was warranted, and what to fold back into a normal\n"
            "   process.\n"
            "\n"
            "Speed of resolution is paramount; accountability is preserved by the\n"
            "real-time log and the mandatory review."
        ),
    },
]


def seed_presets(db: Database, *, created_by: str = "system") -> list[dict[str, Any]]:
    """Ensure the curated presets exist (idempotent — skips ones already present).

    Returns the list of presets that were newly created this call.
    """
    created = []
    for preset in PRESET_TEMPLATES:
        if latest_template(db, preset["template_id"]) is not None:
            continue
        created.append(
            create_template(
                db,
                template_id=preset["template_id"],
                name=preset["name"],
                body=preset["body"],
                description=preset["description"],
                category=preset["category"],
                risk_level=preset["risk_level"],
                scope=preset["scope"],
                created_by=created_by,
                is_preset=True,
            )
        )
    return created
