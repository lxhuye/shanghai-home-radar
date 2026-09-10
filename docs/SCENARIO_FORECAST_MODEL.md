# Scenario Forecast Model

## Output contract

P5 produces Bear, Base, and Bull scenarios for 1, 3, and 5 years. Each scenario exposes:

- low and high price range;
- low and high nominal return;
- low and high CAGR;
- scenario probability;
- confidence;
- Shanghai market-beta range;
- Structural Alpha adjustment.

Probabilities sum to one for each horizon. There is no single target price.

## Method

The scenario anchor is the current P4 Fair Value:

```text
annual asset adjustment
= bounded Structural Alpha
× configured maximum annual adjustment
× scenario sensitivity

scenario CAGR range
= configured Shanghai market-beta CAGR range
+ annual asset adjustment
± confidence widening

scenario price range
= P4 Fair Value × (1 + scenario CAGR range) ^ horizon
```

The displayed price boundaries are rounded to RMB 10,000. This avoids implying unsupported
precision.

## Market Beta separation

Shanghai Bear, Base, and Bull CAGR ranges and probabilities live in versioned configuration.
Structural Alpha is applied separately. This preserves the conceptual hierarchy:

```text
Shanghai Beta
+ District Alpha
+ Submarket Alpha
+ Community Alpha
+ Property Alpha
+ Infrastructure Delta
+ Employment Delta
- Aging
- Product Obsolescence
- Supply Shock
± Market Cycle
```

The engine does not train one opaque price function.

## Confidence widening

Long-horizon ranges compound annual uncertainty. LOW and INSUFFICIENT confidence apply additional
configured CAGR width. SAMPLE and DEMO modes cannot receive confidence above LOW.

## Calibration

The current scenario assumptions are research priors. They are not claimed as calibrated Shanghai
return distributions. Production calibration requires verified point-in-time outcomes and reports
scenario coverage, downside calibration, ranking accuracy, relative outperformance accuracy, and
confidence calibration.

The backtesting interface rejects SAMPLE, DEMO, synthetic, or unverified outcomes when publishing
metrics.
