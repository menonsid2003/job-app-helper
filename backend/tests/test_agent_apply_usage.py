import pytest

from app.agent_apply_usage import AgentApplyUsage


def test_record_job_accumulates_tokens_and_cost(tmp_path):
    usage = AgentApplyUsage(tmp_path / "usage.json")

    usage.record_job({"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.01})
    usage.record_job({"input_tokens": 5, "output_tokens": 7, "cost_usd": 0.02})

    snap = usage.snapshot()
    assert snap["total_input_tokens"] == 15
    assert snap["total_output_tokens"] == 27
    assert snap["total_cost_usd"] == pytest.approx(0.03)
    assert snap["job_count"] == 2


def test_record_job_keeps_latest_rate_limit_info(tmp_path):
    usage = AgentApplyUsage(tmp_path / "usage.json")

    usage.record_job({"rate_limit_status": "allowed", "rate_limit_resets_at": 1787459400, "rate_limit_type": "five_hour"})
    snap = usage.snapshot()
    assert snap["rate_limit_status"] == "allowed"
    assert snap["rate_limit_type"] == "five_hour"
    assert snap["rate_limit_resets_at"] is not None

    # A job with no rate_limit_event message shouldn't clobber the last known status.
    usage.record_job({"input_tokens": 1})
    assert usage.snapshot()["rate_limit_status"] == "allowed"


def test_usage_persists_across_instances(tmp_path):
    path = tmp_path / "usage.json"
    first = AgentApplyUsage(path)
    first.record_job({"input_tokens": 100, "cost_usd": 0.5})

    second = AgentApplyUsage(path)
    snap = second.snapshot()
    assert snap["total_input_tokens"] == 100
    assert snap["total_cost_usd"] == pytest.approx(0.5)
    assert snap["job_count"] == 1
