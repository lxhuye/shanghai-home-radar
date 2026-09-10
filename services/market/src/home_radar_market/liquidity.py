from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from home_radar_market.config import LiquidityConfig


def _bounded(value: Decimal) -> Decimal:
    return min(max(value, Decimal()), Decimal("1"))


@dataclass(frozen=True)
class LiquidityInputs:
    active_inventory: int | None
    new_listings: int | None
    listing_exits: int | None
    median_days_on_market: Decimal | None
    price_cut_ratio: Decimal | None
    relisting_rate: Decimal | None
    buyer_pool_ratio: Decimal | None
    baseline_confidence: Decimal


@dataclass(frozen=True)
class LiquidityComponent:
    raw_value: Decimal
    normalized_score: Decimal
    weight: Decimal
    semantic_note: str


@dataclass(frozen=True)
class LiquidityResult:
    score: Decimal | None
    confidence: Decimal
    components: dict[str, LiquidityComponent]
    available_weight: Decimal


def calculate_liquidity(inputs: LiquidityInputs, config: LiquidityConfig) -> LiquidityResult:
    components: dict[str, LiquidityComponent] = {}

    def add(name: str, raw: Decimal | None, normalized: Decimal, note: str) -> None:
        if raw is not None:
            components[name] = LiquidityComponent(
                raw, _bounded(normalized), config.weights[name], note
            )

    inventory = Decimal(inputs.active_inventory) if inputs.active_inventory is not None else None
    add(
        "inventory_depth",
        inventory,
        inventory / Decimal(config.active_inventory_target) if inventory is not None else Decimal(),
        "Observed active asking inventory; it does not represent transactions.",
    )
    new_listings = Decimal(inputs.new_listings) if inputs.new_listings is not None else None
    add(
        "new_listing_velocity",
        new_listings,
        new_listings / Decimal(config.new_listing_target)
        if new_listings is not None
        else Decimal(),
        "New asking listings observed in the selected window.",
    )
    exits = Decimal(inputs.listing_exits) if inputs.listing_exits is not None else None
    add(
        "listing_exit_velocity",
        exits,
        exits / Decimal(config.listing_exit_target) if exits is not None else Decimal(),
        "No-longer-observed listings; disappearance is not interpreted as a sale.",
    )
    add(
        "days_on_market_speed",
        inputs.median_days_on_market,
        Decimal("1") - inputs.median_days_on_market / Decimal(config.days_on_market_target)
        if inputs.median_days_on_market is not None
        else Decimal(),
        "Inverse normalized median active-listing age.",
    )
    add(
        "price_stability",
        inputs.price_cut_ratio,
        Decimal("1") - inputs.price_cut_ratio if inputs.price_cut_ratio is not None else Decimal(),
        "Lower observed asking-price cut incidence scores higher.",
    )
    add(
        "relisting_stability",
        inputs.relisting_rate,
        Decimal("1") - inputs.relisting_rate if inputs.relisting_rate is not None else Decimal(),
        "Lower relisting incidence scores higher.",
    )
    add(
        "buyer_pool_depth",
        inputs.buyer_pool_ratio,
        inputs.buyer_pool_ratio if inputs.buyer_pool_ratio is not None else Decimal(),
        "Share of asking inventory inside the configured broad buyer price band.",
    )
    available_weight = sum((item.weight for item in components.values()), Decimal())
    sample_signal = _bounded(
        Decimal((inputs.active_inventory or 0) + (inputs.new_listings or 0))
        / Decimal(config.active_inventory_target + config.new_listing_target)
    )
    confidence = _bounded(inputs.baseline_confidence) * available_weight * sample_signal
    if available_weight < config.minimum_available_weight:
        return LiquidityResult(None, confidence, components, available_weight)
    weighted = sum((item.normalized_score * item.weight for item in components.values()), Decimal())
    return LiquidityResult(
        weighted / available_weight * Decimal("100"),
        confidence,
        components,
        available_weight,
    )
