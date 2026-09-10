from __future__ import annotations

from decimal import Decimal

from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import DecisionInputs, DecisionReason

WARNING_TEXT = {
    "HIGH_OBSOLESCENCE_RISK": "长期淘汰风险偏高",
    "SUPPLY_SHOCK_RISK": "未来竞争供应可能形成压力",
    "LOW_FORECAST_CONFIDENCE": "未来模型证据覆盖不足",
    "UNCALIBRATED_MODEL": "Future 模型尚未完成成交校准",
    "LOW_RENTAL_SUPPORT": "租赁需求证据不足",
    "AGING_PRODUCT_RISK": "楼龄与产品老化风险偏高",
    "LIQUIDITY_DECAY_RISK": "买方池与流动性可能走弱",
    "PLANNING_DEPENDENCY": "未来改善较依赖规划兑现",
}


def build_reasons(
    inputs: DecisionInputs, config: DecisionConfig
) -> tuple[tuple[DecisionReason, ...], tuple[DecisionReason, ...]]:
    positive = _positive_reasons(inputs, config)
    negative = _negative_reasons(inputs)
    return (
        tuple(positive[: config.explanations.maximum_positive_reasons]),
        tuple(negative[: config.explanations.maximum_negative_reasons]),
    )


def _positive_reasons(inputs: DecisionInputs, config: DecisionConfig) -> list[DecisionReason]:
    reasons: list[DecisionReason] = []
    if inputs.ask_discount_to_fair_value > 0:
        pct = (inputs.ask_discount_to_fair_value * Decimal("100")).quantize(Decimal("0.1"))
        reasons.append(
            DecisionReason(
                "FAIR_VALUE_DISCOUNT",
                f"价格较 Fair Value 低 {pct}%",
                "P4",
                {"discount_pct": str(pct)},
            )
        )
    if (
        inputs.liquidity_score is not None
        and inputs.liquidity_score >= config.explanations.high_liquidity_score
    ):
        reasons.append(
            DecisionReason(
                "HIGH_LIQUIDITY",
                "总价与产品处于较高流动性区间",
                "P3/P4",
                {"liquidity_score": str(inputs.liquidity_score)},
            )
        )
    if (
        inputs.future_score is not None
        and inputs.future_score >= config.classification.high_future_score
    ):
        reasons.append(
            DecisionReason(
                "STRONG_FUTURE_QUALITY",
                "未来资产质量处于高分区间",
                "P5",
                {"future_score": str(inputs.future_score)},
            )
        )
    if inputs.structural_alpha >= config.explanations.positive_structural_alpha:
        reasons.append(
            DecisionReason(
                "POSITIVE_STRUCTURAL_ALPHA",
                "结构性因素相对市场基线为正",
                "P5",
                {"structural_alpha": str(inputs.structural_alpha)},
            )
        )
    return reasons


def _negative_reasons(inputs: DecisionInputs) -> list[DecisionReason]:
    reasons = [
        DecisionReason(code, text, "P4/P5", {"warning": code})
        for code in dict.fromkeys(inputs.valuation_warnings + inputs.future_warnings)
        if (text := WARNING_TEXT.get(code)) is not None
    ]
    if inputs.liquidity_score is None:
        reasons.append(
            DecisionReason(
                "MISSING_LIQUIDITY",
                "缺少可用的流动性基线",
                "P3",
                {},
            )
        )
    return reasons
