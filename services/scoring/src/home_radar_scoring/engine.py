from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, model_validator


class ScoreDecision(BaseModel):
    score: Decimal = Field(ge=0, le=100)
    decision: str
    contributions: dict[str, Decimal]
    config_version: str


class ScoringConfiguration(BaseModel):
    version: str
    weights: dict[str, Decimal]
    thresholds: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringConfiguration:
        if sum(self.weights.values()) != Decimal("100"):
            raise ValueError("scoring weights must sum to 100")
        return self


class WeightedScoreEngine:
    """Explainable configurable scoring for normalized 0-100 factor inputs."""

    def __init__(self, configuration: ScoringConfiguration) -> None:
        self.configuration = configuration

    def score(self, factors: dict[str, Decimal]) -> ScoreDecision:
        missing = self.configuration.weights.keys() - factors.keys()
        if missing:
            raise ValueError(f"missing score factors: {', '.join(sorted(missing))}")

        contributions: dict[str, Decimal] = {}
        for factor, weight in self.configuration.weights.items():
            value = factors[factor]
            if value < 0 or value > 100:
                raise ValueError(f"factor {factor} must be between 0 and 100")
            contributions[factor] = value * weight / Decimal("100")

        score = sum(contributions.values(), start=Decimal("0")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        decision = self._decision(score)
        return ScoreDecision(
            score=score,
            decision=decision,
            contributions=contributions,
            config_version=self.configuration.version,
        )

    def _decision(self, score: Decimal) -> str:
        ordered = sorted(self.configuration.thresholds.items(), key=lambda item: item[1])
        decision = "PASS"
        for name, minimum in ordered:
            if score >= minimum:
                decision = name.upper()
        return decision
