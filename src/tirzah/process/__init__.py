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

from tirzah.process.instances import (  # noqa: E402
    abandon_instance,
    active_instance_for_session,
    complete_instance,
    get_instance,
    list_instances,
    record_event,
    start_instance,
)
from tirzah.process.enforcement import (  # noqa: E402
    flag_deviation,
    reach_gate,
    record_override,
    render_process_constraint,
    resolve_deviation,
    resolve_gate,
)
from tirzah.process.retrospective import (  # noqa: E402
    build_retrospective,
    similar_task_history,
    usage_metrics,
)

__all__ += [
    "abandon_instance",
    "active_instance_for_session",
    "complete_instance",
    "get_instance",
    "list_instances",
    "record_event",
    "start_instance",
    "flag_deviation",
    "reach_gate",
    "record_override",
    "render_process_constraint",
    "resolve_deviation",
    "resolve_gate",
    "build_retrospective",
    "similar_task_history",
    "usage_metrics",
]

from tirzah.process.refinement import review_process, trial_process  # noqa: E402
from tirzah.process.selection import suggest_process  # noqa: E402

__all__ += ["review_process", "trial_process", "suggest_process"]

from tirzah.process.evolution import (  # noqa: E402
    analyze_template_evolution,
    apply_evolution,
    propose_evolution,
)

__all__ += ["analyze_template_evolution", "apply_evolution", "propose_evolution"]
