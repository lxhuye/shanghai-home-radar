# Shanghai Home Radar Scoring Model

## Purpose

Scoring turns evidence into a prioritized action while keeping distinct decisions visible. The strategic target is quality at a discount. A low asking price alone must not create a high recommendation.

P4 current Value Score is implemented in `services/valuation` and configured by
`config/valuation.yaml`. Its exact active formula, curves, warning caps, and tests are documented in
`docs/VALUE_SCORE_MODEL.md`. P5 Future Score and Obsolescence Risk are implemented in
`services/forecasting` and configured by `config/forecasting.yaml`. Buy Score remains disabled.

## Required outputs

Every evaluated listing preserves at least:

- Value Score, 0–100;
- Future Score, 0–100;
- Liquidity Score, 0–100;
- Obsolescence Risk, 0–100, where higher is worse;
- Buy Score, 0–100.

The API and dashboard must not expose only Buy Score. The component scores explain why a listing ranks where it does.

## Value Score

Value Score measures current-value attractiveness.

| Component | Initial weight |
| --- | ---: |
| Fair Value discount | 35 |
| Liquidity | 25 |
| Current employment and transit accessibility | 20 |
| Property quality | 15 |
| Observable seller / price-cut signal | 5 |
| **Total** | **100** |

Each component produces a normalized 0–100 score plus evidence and confidence. The configured weighted sum produces the raw Value Score:

```text
Value Score = Σ(component score × component weight) / Σ(component weight)
```

No missing component silently receives a favorable value. The result must disclose missing inputs and reduce confidence. The configuration defines whether a missing component uses a neutral value, redistribution, or prevents a recommendation.

### Fair Value discount

The discount compares the best current purchase-price estimate with the fair-value range. Before inquiry, the best estimate may be the ask. After inquiry, it may be the estimated executable-price range.

This component must distinguish:

- genuine discount supported by comparable evidence;
- apparent discount caused by weak property quality;
- low-confidence discount caused by sparse comparables.

### Liquidity

Liquidity uses market evidence such as active supply, listing duration, price-cut behavior, buyer pool, layout demand, and resale resilience. It remains a separate displayed score even though it contributes to Value Score.

### Location and accessibility

This component uses metro and employment accessibility plus mature living infrastructure. Straight-line distance alone cannot represent actual transit convenience.

### Property quality

Relevant evidence includes floor, orientation, elevator, building age, building type, layout quality, and road or noise exposure when available.

### Seller motivation

This component is low-confidence or unavailable before inquiry. Later evidence includes price history, days on market, reason for sale, vacancy, broker guidance, seller expectation, and serious existing offers.

### Optionality

Optionality represents the ability to hold, rent, resell, or serve a broad buyer pool without relying on one narrow future event.

## Value action bands

| Value Score | Initial action |
| ---: | --- |
| Below 65 | PASS |
| 65–74 | WATCH |
| 75–84 | CONTACT |
| 85–89 | VIEW |
| 90–100 | ATTACK |

These thresholds are initial defaults in configuration. A score is not permission for a binding action. Inspection, formal offers, commitments, signing, and payment require the buyer.

## Future Score

Future Score measures relative medium- and long-term residential asset quality.

| Component | Initial weight |
| --- | ---: |
| Employment accessibility | 20 |
| Rail / transport | 15 |
| Land and supply scarcity | 15 |
| Buyer pool / liquidity | 15 |
| Community competitiveness | 10 |
| Urban renewal potential | 10 |
| Rental demand | 5 |
| Public services | 5 |
| Planning realization | 5 |
| **Total** | **100** |

Current and future employment accessibility remain separate inputs. Planned infrastructure contributes only after applying its configured realization probability.

## Planning probability

| Status | Initial factor |
| --- | ---: |
| Operational | 100% |
| Under construction | 70% |
| Approved | 40% |
| Official planning | 15% |
| Rumor or concept | 0% |

Planning status, evidence date, source, and factor must be auditable. These factors live in configuration and can change only through a versioned update.

## Obsolescence Risk

Obsolescence Risk is independent of Future Score. Higher values mean greater risk that the home becomes an unattractive residential product.

The future model considers building-age deterioration, elevator availability, parking and property-management quality, layout competitiveness, replacement by newer products, new supply, and liquidity resilience. The model must show factor contributions and missing evidence.

## Combined Buy Score

Buy Score would rank decision priority after current value, future quality, liquidity,
obsolescence, and seller evidence are calibrated. P5 deliberately does not implement or expose
this opaque combined number.

The combination must satisfy these rules:

- preserve all component scores;
- treat high Obsolescence Risk as a penalty;
- never convert missing evidence into false confidence;
- update after executable-price evidence changes;
- expose component contributions and configuration version;
- allow a hard eligibility filter for budget, title, or other configured buyer constraints.

## Strategic quadrants

| Future quality | Current value | Interpretation | Default posture |
| --- | --- | --- | --- |
| High | Low | Good but expensive | WATCH |
| High | High | Quality at discount | Target |
| Low | High | Value trap | Avoid or require exceptional evidence |
| Low | Low | Weak quality and value | PASS |

Quadrant thresholds are configured independently from action bands. The dashboard shows the quadrant and the underlying scores.

## Executable-price feedback

Asking price is not purchase price. The decision record maintains:

```text
asking_price
fair_value
broker_indicated_price
seller_expected_price
estimated_executable_price_low
estimated_executable_price_high
```

Before inquiry, Value Score uses the best available public evidence. After inquiry, the system adds the raw response, structured seller evidence, confidence, and a new executable-price estimate. It then appends a new scoring result. Earlier scores remain available for audit.

## Configuration contract

Versioned configuration contains:

- component weights;
- normalization breakpoints;
- action thresholds;
- quadrant thresholds;
- planning probabilities;
- missing-data policy;
- confidence thresholds;
- eligibility filters;
- model and configuration version identifiers.

Startup validation rejects weights that do not sum to 100, invalid score ranges, overlapping action bands, and planning factors outside 0–1.

## Audit record

Each score result stores or references:

- listing and evaluation timestamp;
- input-data cutoff;
- component values and contributions;
- raw and adjusted fair-value or executable-price evidence;
- missing fields and confidence;
- model version and configuration version;
- final score, quadrant, and action.

Recalculation appends a result. It does not overwrite the prior decision state.

## Calibration and validation

P4 calibration begins only after historical snapshots and market baselines exist. P4 validates current-value ranking against later observed price changes, listing outcomes, and manually reviewed comparable sets. P5 validates future factors and scenario behavior against historical submarket and property cohorts.

The initial operating acceptance criterion for P4 is a daily, auditable Top 10 ranking of potentially undervalued listings. A high score with weak data must remain visibly low confidence.
