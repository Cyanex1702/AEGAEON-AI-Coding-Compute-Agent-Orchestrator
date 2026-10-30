"""Evidence-based guidance, never an inferred model intelligence rating."""


def assess_attempt(status, summary, checks, events, changed):
    category, title, advice = "unverified", "Capability not established", (
        "Review the answer or run task-appropriate checks. General model capability is untested."
    )
    errors = [e.get("message", "") for e in events if e.get("type") == "model.error"]
    text = " ".join([summary, *errors]).lower()
    tool_errors = any(e.get("observation", {}).get("error") for e in events
                      if e.get("type") == "tool")
    workflow_issue = tool_errors or errors or any(e.get("type") == "recovery" for e in events)
    if status == "Cancelled":
        category, title, advice = "cancelled", "Session stopped", "No capability conclusion made."
    elif "endpoint" in text:
        category, title, advice = "connection", "Model connection failed", (
            "Check the endpoint and runtime. This is not evidence of insufficient reasoning."
        )
    elif any(t in text for t in ("context budget", "truncated", "time budget", "wall-time")):
        category, title, advice = "configured_limit", "Configured limit reached", (
            "Use smaller stages or adjust the context/output/time budget to fit available compute. "
            "This does not establish a reasoning limit."
        )
    elif status == "Checks passed" and checks and all(c.get("status") == "passed" for c in checks):
        category, title, advice = "verified", "Declared checks passed", (
            "This model/configuration completed this task's checks; broader capability is untested."
        )
    elif any(c.get("timed_out") or c.get("status") == "not_run" for c in checks):
        category, title, advice = "verification_incomplete", "Verification incomplete", (
            "Review check execution and dependencies before judging the model's coding ability."
        )
    elif not workflow_issue and any(any(marker in str(c.get("output", "")) for marker in
                 ("ModuleNotFoundError:", "command not found", "is not recognized"))
             or c.get("error") for c in checks):
        category, title, advice = "environment", "Check/module setup incomplete", (
            "Resolve missing commands, dependencies or project modules before judging capability."
        )
    elif workflow_issue:
        category, title, advice = "workflow", "Tool workflow needs attention", (
            "The attempt had tool/protocol errors or stalled actions. Review the trace and use "
            "smaller stages; model capability and orchestration quality are not separated yet."
        )
    elif changed and any(c.get("status") == "failed" for c in checks):
        category, title, advice = "implementation_failed", "Required checks failed", (
            "The current model/configuration may be insufficient for this task as scoped. "
            "Try a smaller testable stage; if failures persist, a stronger reasoning/coding "
            "model may help. One failed attempt does not prove a capability ceiling."
        )
    return {"category": category, "title": title, "advice": advice,
            "reasoning_level": "unknown", "basis": "observed attempt only"}
