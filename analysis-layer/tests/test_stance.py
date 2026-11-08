import pytest
from pydantic import ValidationError

from app.stance import (
    Commitment,
    Conditionality,
    Endorsement,
    StanceDirection,
    StanceEvaluation,
    calculate_stance,
)
from app.prompts import TopicStanceReevaluation


def evaluation(
    direction: StanceDirection,
    endorsement: Endorsement = Endorsement.STRONG,
    commitment: Commitment = Commitment.FIRM,
    conditionality: Conditionality = Conditionality.NONE,
) -> StanceEvaluation:
    return StanceEvaluation(
        direction=direction,
        endorsement=endorsement,
        commitment=commitment,
        conditionality=conditionality,
    )


@pytest.mark.parametrize(
    ("direction", "expected_sign"),
    [
        (StanceDirection.SUPPORT, 1),
        (StanceDirection.OPPOSE, -1),
        (StanceDirection.NEUTRAL, 0),
        (StanceDirection.INSUFFICIENT, 0),
    ],
)
def test_direction_applies_sign(direction, expected_sign):
    result = calculate_stance(evaluation(direction))
    if direction is StanceDirection.INSUFFICIENT:
        assert result is None
    else:
        assert result is not None
        assert result == 0 if expected_sign == 0 else result * expected_sign > 0
        assert -1 <= result <= 1


def test_neutral_is_exactly_zero():
    assert calculate_stance(evaluation(StanceDirection.NEUTRAL)) == 0.0


def test_insufficient_has_no_numerical_stance():
    assert calculate_stance(evaluation(StanceDirection.INSUFFICIENT)) is None


def test_factors_change_absolute_stance_deterministically():
    low = evaluation(
        StanceDirection.SUPPORT,
        Endorsement.WEAK,
        Commitment.TENTATIVE,
    )
    high = evaluation(
        StanceDirection.SUPPORT,
        Endorsement.ABSOLUTE,
        Commitment.DEFINITIVE,
    )
    assert abs(calculate_stance(high)) > abs(calculate_stance(low))
    assert abs(calculate_stance(evaluation(
        StanceDirection.SUPPORT,
        endorsement=Endorsement.STRONG,
    ))) > abs(calculate_stance(evaluation(
        StanceDirection.SUPPORT,
        endorsement=Endorsement.WEAK,
    )))
    assert abs(calculate_stance(evaluation(
        StanceDirection.SUPPORT,
        commitment=Commitment.FIRM,
    ))) > abs(calculate_stance(evaluation(
        StanceDirection.SUPPORT,
        commitment=Commitment.TENTATIVE,
    )))
    conditionality_results = [
        abs(calculate_stance(evaluation(
            StanceDirection.SUPPORT,
            conditionality=conditionality,
        )))
        for conditionality in Conditionality
    ]
    assert conditionality_results == sorted(conditionality_results, reverse=True)


def test_invalid_factor_values_are_rejected():
    with pytest.raises(ValidationError):
        StanceEvaluation(
            direction="SUPPORT",
            endorsement=5,
            commitment=3,
            conditionality=0,
        )
    with pytest.raises(ValidationError):
        StanceEvaluation(
            direction="UNKNOWN",
            endorsement=3,
            commitment=3,
            conditionality=0,
        )


def test_missing_factor_is_rejected():
    with pytest.raises(ValidationError):
        StanceEvaluation(
            direction="SUPPORT",
            endorsement=3,
            commitment=3,
        )


def test_topic_stance_reevaluation_normalizes_five_point_confidence():
    result = TopicStanceReevaluation(
        direction="SUPPORT",
        endorsement=3,
        commitment=3,
        conditionality=0,
        confidence=5.01,
    )

    assert result.confidence == 1
