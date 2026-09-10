# Shanghai Home Radar Forecasting Model

## Purpose

The future engine estimates relative asset quality and plausible outcomes. It does not claim a precise guaranteed future price. Its role is to identify properties likely to retain buyer demand, liquidity, and product competitiveness across market conditions.

This is the P5 implementation contract. P5 now implements deterministic, configuration-driven
research forecasts, source-independent inputs, versioned materialization, APIs, and backtesting
interfaces. It does not claim calibrated LIVE accuracy.

## Time horizons

### Tactical, 0–18 months

Inputs include active inventory, new listings, transactions, days on market, price-cut frequency, negotiation spread, mortgage and LPR environment, policy, and new-home supply.

### Medium term, 1–3 years

Inputs include employment accessibility, job-center growth, rail accessibility, neighborhood maturity, new supply, population, and buyer pool.

### Long term, 3–10 years

Inputs include structural housing demand, building-age deterioration, product competitiveness, elevator, parking, property-management quality, redevelopment or urban renewal, land scarcity, replacement risk from newer products, and liquidity resilience.

The 1-year forecast weights tactical evidence most heavily. The 3-year forecast blends tactical and structural evidence. The 5-year forecast relies more on medium- and long-term quality while widening uncertainty.

## Hierarchical return model

The explainable factor interface follows:

```text
Future Property Return
= Shanghai Beta
+ District Alpha
+ Submarket Alpha
+ Community Alpha
+ Layout / Total-price Alpha
+ Infrastructure Delta
+ Employment Delta
- Building Aging
- Product Obsolescence
- Supply Shock
± Market Cycle
```

Each factor produces a contribution range, evidence date, confidence, and model version. P5 may begin with calibrated inputs or placeholders, but the output must identify placeholders. No placeholder may appear as observed fact.

## Scenario outputs

Each listing receives:

| Horizon | Required scenarios |
| --- | --- |
| 1 year | Bear, Base, Bull |
| 3 years | Bear, Base, Bull |
| 5 years | Bear, Base, Bull |

Each scenario contains:

- a return or price range rather than a single precise target;
- the factor contributions that produced it;
- scenario assumptions;
- model confidence and data gaps.

The model also reports:

- expected CAGR range;
- downside risk;
- probability of outperforming the property's submarket;
- probability of outperforming Shanghai;
- liquidity outlook;
- obsolescence outlook.

Every consumer must label these as probabilistic model outputs, not guaranteed prices.

## Explainable V0 scenario engine

The first engine is deterministic and configuration-driven:

1. Select the latest point-in-time market and property features available before the forecast cutoff.
2. Calculate hierarchical market and property factor ranges.
3. Apply planning realization probabilities to uncompleted infrastructure.
4. Apply aging, obsolescence, supply, and cycle effects.
5. Combine factor ranges under configured bear, base, and bull assumptions.
6. Widen ranges for longer horizons, sparse evidence, and low realization confidence.
7. Save the full inputs, contributions, configuration version, and output ranges.

The implementation must avoid look-ahead bias. A backtest may use only information that existed at its simulated forecast date.

## Planning realization

Planned infrastructure never receives the same weight as operating infrastructure.

| Evidence status | Initial probability factor |
| --- | ---: |
| Operational | 100% |
| Under construction | 70% |
| Approved | 40% |
| Official planning | 15% |
| Rumor or concept | 0% |

The configuration may adjust these values through a versioned change. Each planning item records status, authoritative evidence, evidence date, affected geography, expected timing, and the status used at forecast time.

## Employment accessibility

The model produces:

- Current Employment Accessibility Score;
- Future Employment Accessibility Score.

Initial employment centers include Xujiahui, Caohejing, Hongqiao, Zhangjiang, Lujiazui, Jinqiao, and major Yangpu innovation and employment clusters.

PostGIS stores properties, communities, stations, and employment-center locations. Straight-line distance may support candidate generation, but it cannot be presented as actual travel time. The data contract must support later transit-network travel times, transfers, schedules, and service changes.

Future accessibility applies planning probability to projects that are not operational. A future score must expose how much uplift comes from each planned item.

## Future Score interface

The forecasting layer supplies evidence to the 0–100 Future Score:

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

It also supplies an independent 0–100 Obsolescence Risk, where higher means worse. Forecast scenarios, Future Score, and Obsolescence Risk share evidence but remain separate outputs.

## Liquidity outlook

Liquidity outlook assesses expected resale resilience rather than only price appreciation. Relevant evidence includes layout and total-price buyer pool, active inventory, transaction activity, days on market, price cuts, negotiation spread, community competitiveness, and replacement supply.

The output uses an explainable category or range with evidence and confidence. Sparse transaction data must reduce confidence rather than imply stability.

## Downside and relative performance

Downside risk captures adverse price and liquidity outcomes under the bear scenario. Relative performance compares the same model horizon and cutoff against versioned submarket and Shanghai benchmark scenarios.

Probabilities of outperformance require calibration. Until calibration is sufficient, the interface may return an unavailable state or a clearly labeled provisional estimate. It must not manufacture precise probabilities from unsupported assumptions.

## Forecast input contract

Inputs are source-neutral and point-in-time:

- canonical property and community attributes;
- immutable listing history;
- market baseline version;
- transaction evidence when available;
- supply and liquidity features;
- current and planned transport evidence;
- employment-center and accessibility version;
- policy, mortgage, and market-cycle inputs;
- building aging and product-obsolescence features.

Missing data remains explicit. Raw crawler payloads are not forecast inputs.

## Forecast result contract

Each result contains:

- listing ID and forecast cutoff;
- 1-year, 3-year, and 5-year bear, base, and bull ranges;
- CAGR range and downside measure;
- relative performance outputs;
- liquidity and obsolescence outlooks;
- Future Score and separate Obsolescence Risk references;
- factor contributions and assumptions;
- missing inputs and confidence;
- model, data, and configuration versions;
- created timestamp.

Forecast recalculation appends a new result. It never replaces the historical forecast used for an earlier decision.

## Upgrade path

The stable factor and result interfaces allow later Monte Carlo or Bayesian implementations. A new model must emit the same auditable concepts and run alongside the earlier version during validation. Complexity is justified only when backtesting shows better calibration or decision usefulness.

## Validation

Validation includes:

- point-in-time backtests without future data leakage;
- calibration of scenario coverage and relative-performance probabilities;
- comparison with district, submarket, and Shanghai benchmarks;
- sensitivity tests for planning status, supply shocks, and market cycles;
- cohort checks across geography, age, layout, and total-price bands;
- manual review of the factor explanation for top-ranked properties.

Forecast quality is judged by calibration, ranking usefulness, and transparent uncertainty, not by a visually precise price target.
