# P4 Value Score Model

## Purpose

Value Score measures current attractiveness, not intrinsic value. Fair Value is calculated first
without the target ask or seller behavior. Value Score then asks whether the current observable
price is attractive relative to that estimate.

This separation enforces:

```text
seller desperation ≠ property intrinsic value
cheap unit price ≠ undervaluation
```

## Components

The configuration is `config/valuation.yaml`.

| Component | Maximum contribution |
| --- | ---: |
| Price edge / discount | 35 |
| P3 liquidity plus property modifiers | 25 |
| Current employment and transit accessibility | 20 |
| Property quality | 15 |
| Observable seller / price-cut signal | 5 |
| **Total** | **100** |

Each component exposes its raw value, contribution, and evidence coverage. Missing inputs do not
receive a hidden favorable default.

## Price edge

Price edge uses:

```text
discount = (fair_value - current_ask) / fair_value
```

The contribution is continuous piecewise-linear interpolation through these configured points:

| Discount | Contribution |
| ---: | ---: |
| -10% | 0 |
| 0% | 5 |
| 3% | 12 |
| 5% | 18 |
| 8% | 27 |
| 10% | 32 |
| 20% or more | 35 |

The curve avoids score jumps at band boundaries. A cheaper ask improves this component but does not
enter Fair Value.

## Liquidity

P4 reuses the resolved P3 Listing Market Liquidity V1 score. It does not recreate market liquidity.
The property-level score may apply configured residual modifiers for:

- mainstream total-price range;
- mainstream area;
- mainstream layout;
- extreme ground or top floor;
- explicitly unusual property type.

The result is clamped to 0–100 and scaled to a maximum 25-point contribution. Missing P3 liquidity
produces zero contribution, a coverage flag, and a product-liquidity warning.

## Current employment and transit

P4 uses only current evidence:

- current metro distance;
- current employment accessibility when supplied;
- current mature-amenity accessibility when supplied.

Metro buckets are configurable at 500, 800, and 1200 metres through the shared P4 configuration.
Available evidence is reweighted among known fields, while the original available-weight sum is
returned as coverage and affects valuation confidence. No future rail or employment forecast is
included.

## Property quality

The deterministic quality subscore uses known values for:

- combined floor/elevator suitability by building segment;
- orientation;
- construction year relative to local comparable stock;
- explicit layout-quality classification;
- explicit hard defects.

Unknown values are excluded and reduce coverage. An empty hard-defect list counts as known only when
`hard_defects_known=true`. This prevents absence of data from being treated as absence of defects.

## Seller and price-cut signal

The 5-point seller component uses only observable structured evidence:

- price-cut count;
- cumulative reduction;
- days on market;
- recency of the latest cut;
- relisting history.

The raw subscore weights are 25%, 30%, 20%, 15%, and 10%. Marketing phrases do not contribute.
Seller signal never changes Fair Value.

## Decision bands

| Value Score | Decision |
| ---: | --- |
| Below 65 | PASS |
| 65–74.99 | WATCH |
| 75–84.99 | CONTACT |
| 85–89.99 | VIEW |
| 90–100 | ATTACK |

Thresholds are configuration, not code constants.

## Value-trap protection

The engine emits these warning codes:

| Warning | Trigger |
| --- | --- |
| `VALUE_TRAP_RISK` | Discount at least 8% plus low liquidity, extreme top-floor walk-up, abnormal product, insufficient sample, or low baseline confidence |
| `LOW_SAMPLE_CONFIDENCE` | Fewer than three retained comparables |
| `PRODUCT_LIQUIDITY_RISK` | Missing or sub-40 P3 liquidity |
| `EXTREME_FLOOR_RISK` | Top floor without elevator |
| `SOURCE_DATA_LIMITATION` | No transaction support or low/insufficient P3 baseline confidence |

A value-trap result is capped at 89, so it cannot become ATTACK. An INSUFFICIENT-confidence result
is capped at 84. Warning caps are applied after the raw score and the final decision is recalculated.

## Price-history invariance

For the same property and market evidence:

```text
ask decreases
  → price edge rises
  → seller signal may rise
  → Value Score rises

Fair Value remains unchanged
```

This invariant is covered by unit and PostgreSQL cache-invalidation tests. A new ask creates a new
input fingerprint and cached valuation result while preserving the intrinsic estimate when no other
evidence changed.

## Data modes

The server selects DEMO, SAMPLE, or LIVE. SAMPLE and DEMO responses contain:

```text
recommendation_status = demo_only
investment_recommendation = false
```

No sample Shanghai property is represented as a live recommendation. LIVE requires a matching live
target observation and live P3 market evidence.

## Five validation cases

The synthetic cases are stored in `data/sample/p4_valuation_cases.json`.

| Case | Expected behavior |
| --- | --- |
| A | Liquid, accessible 2BR at an 8% discount produces a high Value Score |
| B | Very cheap old top-floor walk-up with poor liquidity produces VALUE_TRAP and never ATTACK |
| C | Strong property above Fair Value remains PASS or WATCH |
| D | Sparse community data uses transparent hierarchy fallback and lower confidence |
| E | Repeated price cuts leave Fair Value stable while Value Score rises |

These examples validate behavior only. They do not claim live market accuracy.
