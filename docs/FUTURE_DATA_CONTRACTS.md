# Future Data Contracts

## Boundary

P5A stores source-independent evidence. Provider authentication, crawling, licensing, and payload
normalization remain outside the model. P5B consumes only canonical contracts.

Python protocols exist for employment, transport, supply, urban renewal, rental, public services,
buyer pool, market cycle, macro and financing, and complete listing inputs.

## Employment centers

`employment_center` contains:

```text
data_mode
name
category
PostGIS point
current_employment_weight
future_employment_weight
effective_from / effective_to
source / source_record_id / source_timestamp
confidence
provenance
```

Records are point-in-time and mode-specific.

## Future projects

`future_project` supports transport, urban renewal, housing supply, and public-service projects:

```text
project_type
status
PostGIS point and geographic scope
expected_completion
explicit probability
effective dates
source / source_record_id / source_date
confidence
provenance
data_mode
```

Status realization weights are applied by the model, not by a provider adapter.

## Factor observations

`future_factor_observation` is append-only evidence scoped to Shanghai, district, submarket,
community, or listing. It stores current and future scores separately, optional raw values and
units, observed and effective dates, source confidence, explanation, metadata, provenance, and
data mode.

The repository selects the most specific valid observation at the assessment cutoff. It rejects
look-ahead timestamps and never joins evidence across data modes.

## P3 and P4 dependencies

- P3 listing baselines supply current liquidity context.
- P3 rental baselines supply rental evidence when a real rental baseline exists.
- Missing rental evidence returns INSUFFICIENT. The engine does not manufacture rent or yield.
- P4 supplies Fair Value, Value Score, valuation version, baseline version, confidence, and
  transaction-support state.

## Materialized assessment

`future_assessment` stores one immutable result per listing, mode, input fingerprint, and model
version tuple. It contains Future Score, Obsolescence Risk, Structural Alpha, scenarios, factor and
risk breakdowns, confidence, calibration state, warnings, P4 anchor, versions, data timestamp, and
provenance.

A PostgreSQL transaction advisory lock protects same-listing, same-mode calculation. A changed
factor observation, P3/P4 version, model version, configuration, cutoff, project, or employment
center changes the fingerprint and creates a new assessment.

## Mode isolation

LIVE, SAMPLE, and DEMO are mandatory on all P5 evidence and results. API callers cannot select a
mode. The server runtime mode controls every query, and every factor returned by one assessment has
the same mode.
