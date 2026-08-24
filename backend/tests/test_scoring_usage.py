import pytest

from app.scoring_usage import ScoringUsage, estimate_cost_usd


def test_estimate_cost_matches_exact_model_id():
    # claude-opus-5: $5/$25 per MTok
    cost = estimate_cost_usd("claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(30.00)


def test_estimate_cost_resolves_dated_snapshot_to_its_family_pricing():
    # The exact bug this guards against: ANTHROPIC_MODEL set to an old dated
    # snapshot id in .env used to miss the pricing dict's exact-string lookup
    # and silently fall back to (5x pricier) Opus 5 rates.
    dated = estimate_cost_usd("claude-haiku-4-5-20251001", input_tokens=1_000_000, output_tokens=1_000_000)
    canonical = estimate_cost_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert dated == canonical == pytest.approx(6.00)  # $1 + $5 per MTok


def test_estimate_cost_falls_back_to_opus_pricing_for_a_truly_unknown_model(caplog):
    cost = estimate_cost_usd("some-future-model-nobody-added-yet", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(30.00)  # Opus 5 rates
    assert any("no pricing entry" in record.message for record in caplog.records)


def test_scoring_usage_record_call_accumulates_using_correct_model_pricing(tmp_path):
    usage = ScoringUsage(tmp_path / "usage.json")

    usage.record_call({"model": "claude-haiku-4-5-20251001", "input_tokens": 1_000_000, "output_tokens": 1_000_000})

    snap = usage.snapshot()
    assert snap["total_cost_usd"] == pytest.approx(6.00)
    assert snap["call_count"] == 1
