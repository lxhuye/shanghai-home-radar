# P4 Fair Value Model

## Purpose and boundary

P4 estimates a reasonable current value range for one canonical property. It does not predict
future appreciation, infer a seller's undisclosed bottom price, automate inquiry, or use an LLM as
the pricing engine. P5 forecasting and later seller-intelligence phases remain outside this model.

The implementation is deterministic. Every result records the selected evidence, weights,
adjustments, uncertainty, configuration, input fingerprint, and model versions.

## Required outputs

Each evaluation returns:

- fair value, low estimate, and high estimate;
- listing-derived, transaction-supported, or mixed-source basis;
- numeric and classified confidence;
- current ask and discount to fair value;
- a documented executable price only when structured evidence exists;
- comparable and effective comparable counts;
- P3 baseline level, confidence, version, and fallback reason;
- selected comparables, residual adjustments, warnings, provenance, and versions.

One precise number without a range is never returned.

## Pipeline

```text
canonical target property
  → mode and data-quality check
  → 90D / 180D / 365D comparable expansion
  → TIER_1 / TIER_2 / TIER_3 selection
  → P3 hierarchy resolution as TIER_4 support
  → deterministic similarity and normalized weights
  → unit-specific residual adjustments
  → robust outlier filtering and weighted median
  → sensitivity interval
  → confidence and valuation basis
  → separate Value Score and warning rules
```

Collector adapters are not imported. The engine reads canonical listings, typed market
observations, immutable snapshots, events, communities, and versioned P3 baselines.

## Comparable hierarchy

The configuration is `config/valuation.yaml`. Thresholds are not embedded in selection code.

| Tier | Required relationship |
| --- | --- |
| TIER_1 | Same community, exact layout, area within 10%, and construction year within 5 years |
| TIER_2 | Same community, exact or configured adjacent layout, area within 20%, and known age within 10 years |
| TIER_3 | Same submarket, compatible product type, exact or adjacent layout, and area within 30% |
| TIER_4 | Resolved P3 community, shrunk community/submarket, submarket, district, or Shanghai baseline |

Selection first tries observations younger than 90 days. It expands to 180 and then 365 days only
when the configured minimum sample is not met. A narrow tier is retained whenever it reaches the
minimum. Every selected observation exposes its tier, similarity, weight, freshness, evidence type,
and selection reasons.

Observations are de-duplicated by evidence type and canonical entity. A target listing is never its
own comparable. Listing and verified transaction observations remain distinct.

## Similarity and weights

For comparable `i`, similarity is:

```text
Sᵢ = Σ(wⱼ × factorᵢⱼ)
```

The configured factors are geography, community, area, layout, building age, floor, elevator,
orientation, freshness, and source confidence. The factor weights sum to 1.

Raw comparable weight is:

```text
raw_weightᵢ = Sᵢ × tier_multiplierᵢ × evidence_multiplierᵢ
```

Verified transaction observations receive the configured 1.25 evidence multiplier. Listing asks
receive 1.0. This preference does not reinterpret a listing as a transaction. Final weights are
normalized to sum to 1.

Effective comparable count is the inverse concentration measure:

```text
effective_count = 1 / Σ(normalized_weightᵢ²)
```

## P3 baseline integration

P4 requests the exact P3 area bucket and layout slice. It uses the existing P3 resolver:

```text
community
  → community/submarket shrinkage
  → submarket
  → district
  → Shanghai
```

P4 does not silently widen area or layout. Transaction baselines are preferred when their sample
reaches the configured strong-support threshold. Otherwise the listing baseline remains the main
baseline and transaction observations can still contribute as comparables.

The response exposes `baseline_level_used`, `baseline_confidence`, `baseline_version`, fallback
path, and fallback reason.

## Baseline effects versus residual adjustments

P3 already contains district, submarket, community, area bucket, and layout effects. P4 therefore
does not add those premiums again.

P4 adjusts only explicit unit-level differences between a comparable and the target:

| Adjustment | P4 rule |
| --- | --- |
| Floor and elevator | One combined interaction term segmented by walk-up, elevator building, or high-rise |
| Orientation | Difference between configured target and comparable orientation rates |
| Building age | Relative construction-year difference, capped; no universal Shanghai depreciation |
| Metro | Only a meaningful measured difference inside the same community |
| Layout quality | Only an explicit stored classification, separate from bedroom/layout baseline |
| Road/noise | Only explicit structured evidence; otherwise UNKNOWN |

The combined adjustment is capped at 18%. A sixth-floor walk-up is represented by the combined
floor/elevator term, not by two additive penalties. Unknown target or comparable features produce
no silent price adjustment. Missing target fields expand uncertainty and reduce confidence.

## Robust aggregation

Each comparable unit price is normalized to the target:

```text
adjusted_unit_priceᵢ = unit_priceᵢ × (1 + total_residual_adjustmentᵢ)
adjusted_valueᵢ = adjusted_unit_priceᵢ × target_area
```

When enough comparables exist, contextual outliers are removed with a MAD robust-z rule. If MAD is
zero and only a minority differs from the median, the median cluster is retained.

The central estimate is a weighted median over adjusted comparables plus the configured P3 baseline
weight. The low and high estimates include:

- weighted P25 and P75 sensitivity;
- P3 P25 and P75 when available;
- comparable dispersion;
- comparable sample shortfall;
- missing target attributes;
- configured base adjustment uncertainty.

The interval rate is capped at 22%. The central estimate does not use the target asking price,
price cuts, days on market, or seller signal.

## Valuation basis and transaction support

The engine returns:

| Result | Meaning |
| --- | --- |
| `LISTING_DERIVED` | Only listing-derived comparable or baseline evidence supports the estimate |
| `TRANSACTION_SUPPORTED` | Transaction evidence supports the estimate without listing evidence |
| `MIXED_SOURCE` | Both transaction and listing evidence contribute |

Transaction support is `NONE`, `WEAK`, or `STRONG`. Strong support requires the configured count of
recent verified transaction comparables or a transaction baseline with that sample count. No
ask-to-close discount is assumed.

## Confidence

Confidence is a weighted sum of nine 0–1 factors:

| Factor | Weight |
| --- | ---: |
| Comparable count | 0.16 |
| Effective comparable weight | 0.13 |
| Tier quality | 0.12 |
| P3 baseline confidence | 0.12 |
| Freshness | 0.10 |
| Source diversity | 0.09 |
| Price dispersion | 0.10 |
| Target attribute completeness | 0.08 |
| Transaction support | 0.10 |

Configured thresholds classify the result as HIGH, MEDIUM, LOW, or INSUFFICIENT. Unknown property
attributes lower completeness. Listing-only results cannot receive the transaction-support points.
A result can still be screened when confidence is insufficient, but the warning and score cap are
visible.

## Asking and executable prices

The model stores these separately:

```text
current_ask
fair_value
estimated_executable_price
```

Current ask affects discount and Value Score, not fair value. Historical price cuts affect only the
seller-signal component. `estimated_executable_price` remains null unless the structured request
provides a positive price and its evidence description. Marketing phrases are not evidence.

## Cache and invalidation

`valuation_result` is an immutable versioned result cache. Its fingerprint covers:

- target price and property features;
- latest mode-specific target observation;
- all eligible comparable observation IDs, prices, timestamps, confidence, and metadata;
- P3 valuation and listing-liquidity baseline versions;
- price-history signals;
- Shanghai evaluation date;
- valuation, scoring, and configuration versions.

The same fingerprint and version tuple is idempotent. A price or feature change, late comparable,
new P3 baseline, history change, configuration change, or model version creates a new result.
PostgreSQL transaction advisory locks serialize the same listing and mode. Earlier results remain
available for backtesting.

## API

```text
GET  /api/v1/valuation/listings/{listing_id}
POST /api/v1/valuation/evaluate
GET  /api/v1/valuation/listings/{listing_id}/comparables
GET  /api/v1/valuation/listings/{listing_id}/explanation
```

GET requests accept an optional timezone-aware `as_of`. The deployment chooses DEMO, SAMPLE, or
LIVE. Clients cannot switch modes. SAMPLE and DEMO outputs are labeled `demo_only` and explicitly
state that they are not live investment recommendations.

## Backtesting interface

`home_radar_valuation.backtesting` compares a stored valuation at T0 with a later verified
transaction at T1. It calculates MAE, MAPE, median absolute percentage error, pairwise ranking
quality, and error calibration by confidence bucket. It refuses an empty outcome set. Synthetic
closes must never be passed as verified outcomes, and no current accuracy claim is made.

## Current limitations

- The repository contains no licensed live Shanghai transaction dataset.
- Employment and mature-amenity accessibility are optional structured inputs and currently absent
  from the canonical listing feed.
- Community metro distance is available, but unit-specific intra-community metro variation is often
  unknown.
- Layout quality, hard defects, and road/noise exposure remain UNKNOWN unless explicitly supplied.
- Cross-source entity resolution is not implemented.
- The web dashboard still uses demo presentation data; P4 delivers the backend decision API.
