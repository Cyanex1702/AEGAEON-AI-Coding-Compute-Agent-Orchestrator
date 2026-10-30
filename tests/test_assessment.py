from aegaeon_lite.assessment import assess_attempt


def assess(summary="", checks=None, events=None, status="Partial", changed=True):
    return assess_attempt(status, summary, checks or [], events or [], changed)


def test_failed_code_gets_cautious_upgrade_guidance():
    result = assess(checks=[{"status": "failed"}])
    assert result["category"] == "implementation_failed"
    assert "stronger" in result["advice"] and result["reasoning_level"] == "unknown"


def test_protocol_error_is_not_diagnosed_as_a_reasoning_limit():
    result = assess(checks=[{"status": "failed"}], events=[
        {"type": "tool", "observation": {"error": "stale edit"}}])
    assert result["category"] == "workflow"


def test_connection_and_budget_are_distinguished():
    assert assess("model endpoint unavailable")["category"] == "connection"
    assert assess("model output truncated")["category"] == "configured_limit"


def test_passed_checks_do_not_claim_general_intelligence():
    result = assess(status="Checks passed", checks=[{"status": "passed"}])
    assert result["category"] == "verified"
    assert result["reasoning_level"] == "unknown"


def test_skipped_or_timed_out_checks_do_not_blame_model():
    assert assess(checks=[{"status": "not_run"}])["category"] == "verification_incomplete"
    result = assess(checks=[{"status": "failed", "timed_out": True}])
    assert result["category"] == "verification_incomplete"
    result = assess(checks=[{"status": "failed", "output": "ModuleNotFoundError: pytest"}])
    assert result["category"] == "environment"


def test_unverified_answer_is_not_ranked_and_cancel_is_not_failure():
    assert assess(status="Answer")["category"] == "unverified"
    assert assess(status="Cancelled")["category"] == "cancelled"
