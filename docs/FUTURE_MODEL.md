# Shanghai Home Radar Future Model

## Status

P5 is implemented as a deterministic research model. It is not calibrated for investment use.
Every assessment reports its data mode, confidence, calibration state, missing inputs, input
fingerprint, and P3, P4, P5, scenario, data, and configuration versions.

## Separation from current value

P5 keeps these outputs independent:

- P4 Fair Value estimates current intrinsic value.
- P4 Value Score measures current purchase attractiveness.
- P5 Future Score measures medium- and long-term residential competitiveness.
- P5 Obsolescence Risk measures product and demand replacement risk.
- P5 scenarios describe ranges around P4 Fair Value.

The asking price is never a scenario anchor. It can change Value Score and the quality/value
quadrant, but it cannot change intrinsic P5 price ranges.

## Factor contract

The engine always exposes these factors:

```text
employment_accessibility
transport_accessibility
supply_scarcity
buyer_pool_depth
community_competitiveness
urban_renewal
rental_demand
public_services
planning_realization
market_cycle
building_aging
product_obsolescence
```

Each factor contains current score, future score, delta, confidence, source, source timestamp,
data mode, explanation, and metadata. Missing evidence produces null scores and an explicit
INSUFFICIENT explanation. It never receives a hidden favorable value.

## Future Score

The configured component weights are:

| Factor | Weight |
| --- | ---: |
| Employment accessibility | 20 |
| Transport accessibility | 15 |
| Supply scarcity | 15 |
| Buyer pool depth | 15 |
| Community competitiveness | 10 |
| Urban renewal | 10 |
| Rental demand | 5 |
| Public services | 5 |
| Planning realization | 5 |

For available factors:

```text
Future Score = Σ(future score × weight) / Σ(available weight)
```

The score is withheld when available weight is below the configured 65% coverage gate. Missing
factors and effective coverage remain visible even when the score is available.

## Employment accessibility

`employment_center` stores configurable PostGIS points, categories, current and future employment
weights, effective dates, source, timestamp, and confidence. V0 calculates a weighted distance
score using a configurable curve. It labels this as a straight-line approximation, not transit
travel time. The data contract supports replacement with routing evidence without changing the
factor interface.

No Shanghai employment-center name is hard-coded in the model.

## Transport and planning

Projects use CURRENT, UNDER_CONSTRUCTION, APPROVED, PLANNED, and CONCEPTUAL states. Initial
realization factors are 1.00, 0.70, 0.40, 0.15, and 0.00. A project-specific probability can only
reduce the configured state weight.

Transport calculations exclude employment effects. Employment uplift remains in its own factor,
which prevents double counting. Planning-derived transport uplift is capped by configuration.

## Structural Alpha

Structural Alpha is a bounded relative index, not a promised return. The model keeps explicit
components for district, submarket, community, and property alpha, infrastructure and employment
deltas, scarcity, buyer pool, renewal, aging, product obsolescence, supply shock, and market cycle.

Positive and negative contributions remain visible. Shanghai market beta is not included in this
index; the scenario engine applies it separately.

## Confidence and calibration

Confidence combines data coverage, freshness, source authority, planning certainty, employment,
supply and rental quality, transaction calibration, and historical continuity. SAMPLE and DEMO
outputs are capped at LOW. UNCALIBRATED outputs are also capped at LOW.

Calibration is derived from verified evidence gates and counts. A configuration switch cannot
silently mark a model calibrated. Until verified transaction backtests, employment coverage,
supply coverage, and LIVE isolation pass, the system remains RESEARCH / UNCALIBRATED and emits no
BUY, STRONG BUY, INVEST, or equivalent conclusion.

## Quality/value matrix

P5 exposes, but does not merge, P4 Value Score and P5 Future Score:

| Future quality | Current value | Classification |
| --- | --- | --- |
| High | High | QUALITY_AT_DISCOUNT |
| High | Low | GOOD_BUT_EXPENSIVE |
| Low | High | VALUE_TRAP |
| Low | Low | PASS |

The active high-score thresholds are versioned in `config/forecasting.yaml`.
