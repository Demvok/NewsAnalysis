from __future__ import annotations

from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field


class StanceDirection(str, Enum):
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT = "INSUFFICIENT"


class Endorsement(IntEnum):
    WEAK = 1
    MODERATE = 2
    STRONG = 3
    ABSOLUTE = 4


class Commitment(IntEnum):
    TENTATIVE = 1
    MODERATE = 2
    FIRM = 3
    DEFINITIVE = 4


class Conditionality(IntEnum):
    NONE = 0
    LIMITED = 1
    HIGH = 2


class StanceEvaluation(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    direction: StanceDirection
    endorsement: Endorsement = Field(ge=1, le=4)
    commitment: Commitment = Field(ge=1, le=4)
    conditionality: Conditionality = Field(ge=0, le=2)


_DIRECTION_MULTIPLIERS = {
    StanceDirection.SUPPORT: 1.0,
    StanceDirection.OPPOSE: -1.0,
    StanceDirection.NEUTRAL: 0.0,
}
_CONDITIONALITY_FACTORS = {
    Conditionality.NONE: 1.0,
    Conditionality.LIMITED: 0.85,
    Conditionality.HIGH: 0.65,
}


def calculate_stance(evaluation: StanceEvaluation) -> float | None:
    """Calculate the canonical stance from validated categorical factors."""
    if evaluation.direction is StanceDirection.INSUFFICIENT:
        return None
    if evaluation.direction is StanceDirection.NEUTRAL:
        return 0.0

    endorsement = int(evaluation.endorsement)
    commitment = int(evaluation.commitment)
    conditionality = Conditionality(evaluation.conditionality)
    baseline_intensity = 0.65 * (endorsement / 4) + 0.35 * (commitment / 4)
    stance = (
        _DIRECTION_MULTIPLIERS[evaluation.direction]
        * baseline_intensity
        * _CONDITIONALITY_FACTORS[conditionality]
    )
    if not -1 <= stance <= 1:
        raise ValueError(f"calculated stance is outside [-1, 1]: {stance}")
    return stance


def evaluation_status(evaluation: StanceEvaluation) -> str:
    """Return the persisted status for a categorical evaluation."""
    return (
        "INSUFFICIENT_EVIDENCE"
        if evaluation.direction is StanceDirection.INSUFFICIENT
        else "ANALYZED"
    )
