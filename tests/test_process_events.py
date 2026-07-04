from galeed.recorder import Tracer

from tirzah.sessions.process_events import emit_process_trace_events


def test_plan_step_metadata_surfaces_in_process_events() -> None:
    tracer = Tracer(session_id="s1", source="test")
    emit_process_trace_events(
        tracer,
        [
            {
                "step": "plan.step.started",
                "step_id": "2",
                "metadata": {"construct": "CALL", "revision": 1},
            },
            {
                "step": "plan.iterate.round",
                "step_id": "3",
                "metadata": {"round": 2, "max_rounds": 5, "body": ["4", "5"]},
            },
            {
                "step": "plan.decision.selected",
                "step_id": "6",
                "metadata": {"signal": "web_research", "branch": "web", "selected_steps": ["7"]},
            },
        ],
    )
    events = tracer.as_dicts()
    assert len(events) == 3
    started = events[0]["metadata"]
    assert started["step"] == "plan.step.started"
    assert started["step_id"] == "2"
    assert started["construct"] == "CALL"
    assert started["revision"] == 1
    iterate = events[1]["metadata"]
    assert iterate["round"] == 2
    assert iterate["max_rounds"] == 5
    assert iterate["body"] == ["4", "5"]
    decision = events[2]["metadata"]
    assert decision["signal"] == "web_research"
    assert decision["branch"] == "web"
    assert decision["selected_steps"] == ["7"]