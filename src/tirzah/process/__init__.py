"""Human-defined Processes — configurable, selectable templates that ground
agentic workflows with the right amount of oversight.

A **template** is a versioned, human-authored plain-text description of how work
should proceed (its gates, loops, and deviation rules stated in prose). An
**instance** binds a template version to a specific task and carries its own
state + execution trace. The interpretive planner receives the active process
text as a constraint; gates pause execution (resumable on approval), deviations
are flagged for approval, and everything is audit-queryable.

Layout:
- ``templates`` — storage + versioning (history preserved) + preset seeding.
- ``instances`` — selection/binding a template version to a task + lifecycle.
- ``enforcement`` — the planner constraint, gate/deviation/override semantics.
- ``retrospective`` — adherence/deviation/outcome metrics + historical queries.
"""

from tirzah.process.templates import (
    PRESET_TEMPLATES,
    create_template,
    get_template,
    latest_template,
    list_templates,
    revise_template,
    seed_presets,
    template_versions,
)

__all__ = [
    "PRESET_TEMPLATES",
    "create_template",
    "get_template",
    "latest_template",
    "list_templates",
    "revise_template",
    "seed_presets",
    "template_versions",
]
