# P5.5 Decision Orchestration

## Boundary

P5.5 combines stored P3, P4, and P5 outputs. It does not recalculate market baselines, Fair Value,
Value Score, Future Score, Obsolescence Risk, or Structural Alpha. It does not create a Buy Score or
another weighted total. Every decision records the exact upstream versions and an input fingerprint.

`VIEW` is a workflow state for human research. It is not an investment recommendation. `ATTACK` is
not part of the P5.5 state type and configuration loading fails if it is enabled.

## Inputs and outputs

The orchestrator consumes:

- P3 baseline confidence and product liquidity carried in the P4 score explanation;
- P4 ask, Fair Value range, Value Score, valuation confidence, warnings, and versions;
- P5 Future Score, Obsolescence Risk, Structural Alpha, confidence, calibration state, warnings,
  evidence versions, and coverage.

It emits three separate decisions:

| Output | Values | Meaning |
| --- | --- | --- |
| Opportunity classification | `QUALITY_AT_DISCOUNT`, `GOOD_BUT_EXPENSIVE`, `VALUE_TRAP`, `LOW_QUALITY`, `INSUFFICIENT_DATA` | Value/Future quadrant only |
| Eligibility | `ELIGIBLE`, `HARD_RISK_FILTERED`, `INSUFFICIENT` | Whether the listing may enter relative ranking |
| Workflow | `PASS`, `WATCH`, `CONTACT`, `VIEW` | Next human research step |

The default high thresholds are 75 for both Value Score and Future Score. Coverage thresholds and
all workflow gates live in `config/decision.yaml`.

## Ranking

Ranking is lexicographic, not additive:

```text
current configured decision universe
  → confidence gate
  → hard-risk filter
  → Value/Future quadrant
  → liquidity
  → Future Score
  → Value Score
  → lower Obsolescence Risk
  → Structural Alpha
  → stable listing-id tie break
```

Only `ELIGIBLE` listings receive a rank. A `VALUE_TRAP` or `LOW_QUALITY` listing is filtered even if
its Value Score exceeds every eligible listing. This prevents price cuts from turning weak assets
into top opportunities.

## Why Ranked

Reasons are deterministic structured records. Each contains a code, rendered text, source stage,
and evidence values. The explanation may include Fair Value discount, product liquidity, future
quality, Structural Alpha, hard risks, calibration state, and missing evidence. No language model
generates or changes rank reasons.

## Blind validation

A blind batch freezes the listing snapshot, decision version, model result, and relative rank before
human labels are entered. During `blind_labeling`, API responses hide `model_result` and
`frozen_rank`. All cases must be labeled before reveal. A revealed batch is immutable.

Human labels are:

- `WORTH_VIEWING` for 值得看;
- `WAIT` for 可以等;
- `VALUE_TRAP` for 价值陷阱;
- `PASS` for 直接 PASS.

The default target batch size is 50. The product metrics are:

- Precision@5 and Precision@10, using `WORTH_VIEWING` as the strict positive label;
- Value Trap False Positive Rate, where an eligible or VIEW result is a false positive;
- VIEW acceptance rate, the share of VIEW cases labeled `WORTH_VIEWING`;
- Top 10 manual acceptance rate, accepting either `WORTH_VIEWING` or `WAIT`.

Metrics with a zero denominator return `null` rather than a fabricated zero.

## API and batch operation

```text
GET  /api/v1/decisions/listings/{listing_id}
GET  /api/v1/decisions/opportunities
GET  /api/v1/decisions/listings/{listing_id}/why-ranked
POST /api/v1/decisions/validation/batches
GET  /api/v1/decisions/validation/batches/{batch_id}
POST /api/v1/decisions/validation/batches/{batch_id}/labels
POST /api/v1/decisions/validation/batches/{batch_id}/reveal
```

Validation routes require `X-API-Key`. Daily materialization uses the `decision` RQ queue and
`home-radar-decision-enqueue`. Opportunity reads rank the latest current-version assessment per
listing, so stale assessments cannot create duplicate ranks.

## Calibration boundary

P5.5 is implemented for research and product validation. It stays uncalibrated until authorized
LIVE listing coverage, verified transaction outcomes, employment data, and residential supply data
pass their upstream gates. Development priority now favors those data lines over new model layers.
