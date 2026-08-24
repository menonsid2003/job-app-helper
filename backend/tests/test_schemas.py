from app.schemas import ScoreResult, WorkAuthorization


def test_work_authorization_coerces_string_into_boolean_field():
    # Regression test: llama3.1:8b was observed putting "not_mentioned" (meant
    # for sponsorship_mentioned) into citizenship_required/security_clearance_required,
    # which previously crashed validation entirely.
    wa = WorkAuthorization.model_validate(
        {
            "citizenship_required": "not_mentioned",
            "security_clearance_required": "not_mentioned",
            "sponsorship_mentioned": "not_mentioned",
            "hard_exclude": "not_mentioned",
        }
    )
    assert wa.citizenship_required is False
    assert wa.security_clearance_required is False
    assert wa.hard_exclude is False


def test_work_authorization_coerces_affirmative_strings_to_true():
    wa = WorkAuthorization.model_validate({"citizenship_required": "true", "security_clearance_required": "Required"})
    assert wa.citizenship_required is True
    assert wa.security_clearance_required is True


def test_work_authorization_coerces_unrecognized_sponsorship_value_to_not_mentioned():
    wa = WorkAuthorization.model_validate({"sponsorship_mentioned": "unclear"})
    assert wa.sponsorship_mentioned == "not_mentioned"


def test_work_authorization_maps_sponsorship_refusal_phrase_to_no():
    wa = WorkAuthorization.model_validate({"sponsorship_mentioned": "will not sponsor"})
    assert wa.sponsorship_mentioned == "no"


def test_score_result_still_validates_with_malformed_work_authorization():
    result = ScoreResult.model_validate(
        {
            "score": 70,
            "reasoning": "Decent fit.",
            "role_category": "SWE",
            "work_authorization": {
                "citizenship_required": "not_mentioned",
                "security_clearance_required": "not_mentioned",
                "sponsorship_mentioned": "not_mentioned",
            },
        }
    )
    assert result.work_authorization.citizenship_required is False
