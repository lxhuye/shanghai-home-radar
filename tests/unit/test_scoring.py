from decimal import Decimal

import pytest
from home_radar_scoring.engine import ScoringConfiguration, WeightedScoreEngine


def configuration() -> ScoringConfiguration:
    return ScoringConfiguration(
        version="test",
        weights={
            "fair_value_discount": Decimal("35"),
            "liquidity": Decimal("20"),
            "location": Decimal("15"),
            "property_quality": Decimal("15"),
            "seller_motivation": Decimal("10"),
            "optionality": Decimal("5"),
        },
        thresholds={
            "watch": Decimal("65"),
            "contact": Decimal("75"),
            "view": Decimal("85"),
            "attack": Decimal("90"),
        },
    )


def test_weighted_score_is_auditable() -> None:
    result = WeightedScoreEngine(configuration()).score(
        {
            "fair_value_discount": Decimal("90"),
            "liquidity": Decimal("80"),
            "location": Decimal("70"),
            "property_quality": Decimal("80"),
            "seller_motivation": Decimal("60"),
            "optionality": Decimal("70"),
        }
    )
    assert result.score == Decimal("79.50")
    assert result.decision == "CONTACT"
    assert result.contributions["fair_value_discount"] == Decimal("31.5")


def test_score_rejects_missing_factors() -> None:
    with pytest.raises(ValueError, match="missing score factors"):
        WeightedScoreEngine(configuration()).score({})
