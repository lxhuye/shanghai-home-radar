# P3 Market Baseline Model

## Scope

P3 turns authorized, source-independent market observations into versioned market context.
It does not estimate fair value, predict future prices, contact brokers, or negotiate.

An asking price is always labeled listing evidence. It is never relabeled as a transaction.
Listing disappearance is an exit signal only. It is never treated as proof of sale.

## Observation contract

`market_observation` stores one immutable piece of normalized evidence with source identity,
observation time, database arrival time, data mode, geography, segment, coverage status, and
source linkage.

| Observation type | Meaning | Required measure |
| --- | --- | --- |
| `listing` | Advertised resale asking evidence | Area, total ask, unit ask |
| `transaction` | Authorized completed-transaction evidence | Area, total price, unit price |
| `rental` | Rental asking evidence | Monthly rent or rent per square metre |
| `official_index` | Published index evidence | Index value |
| `external_baseline` | Authorized external aggregate | Provider-defined normalized measures |

The unique source identity is `(data_mode, observation_type, source, source_record_id,
observed_at)`. Listing snapshots also have a unique direct link. These constraints make feed
retries idempotent while retaining successive observations.

Successful partial runs may contribute observed positive price evidence with
`coverage_complete=false`. Failed, running, authentication-paused, or already-running crawls do
not create observations. Partial runs cannot create negative presence evidence.

## Hierarchy and segments

Every eligible observation may contribute to these geographic levels:

```text
Shanghai → district → submarket → community
```

Every level is materialized for the unsegmented population and, when known, by area bucket,
layout, and their combination. Area buckets are `<40`, `40-50`, `50-65`, `65-80`, `80-100`,
and `100+` square metres. Layouts are `1BR`, `2BR`, and `3BR+`. Unknown bedrooms remain
unsegmented and never enter `3BR+`.

## Time windows and entity weighting

P3 uses 30, 90, 180, and 365 Shanghai calendar-day windows. For an `as_of_date`, the end is
midnight after that date in `Asia/Shanghai`, converted to UTC. The interval is half-open:

```text
[window_start_at, window_end_at)
```

An observation exactly on the lower bound is included. One exactly on the upper bound is not.
Within a window, each listing or source record contributes only its latest eligible observation.
Repeated crawling therefore does not overweight a property.

## Price statistics and outliers

Total and unit prices are calculated independently. P10, P25, P50, P75, and P90 use Decimal
Type-7 linear interpolation. Empty distributions return null. One observation returns the same
value for all percentiles.

Outliers are contextual to a slice and window. They never mutate the underlying observation.
When the configured minimum sample is met, P3 uses median absolute deviation with a robust
z-score. Degenerate MAD falls back to IQR fences. Degenerate IQR does not auto-delete evidence.
Total-price and unit-price exclusion masks remain separate. Excluded IDs, method, bounds, raw
count, and effective counts are stored in baseline provenance.

## Listing metric definitions

| Metric | Definition |
| --- | --- |
| Active inventory | Distinct listing state at cutoff; incomplete negative states fall back to the last trustworthy state |
| New listings | Distinct listings with first-seen or `new` evidence inside the window |
| Price-cut count | Distinct listings with a cut event or observed ask decrease |
| Price-cut ratio | Cut listings divided by distinct listings observed in the slice and window |
| Initial/current ask | First and cutoff-latest ask per listing, followed by a median |
| Cut percentage | Per-listing `(initial-current)/initial`, floored at zero, followed by a median |
| Days on market | Cutoff minus first seen for active listings, never negative |
| Relisting rate | Distinct relisted listings divided by distinct new or relisted listings |
| Missing/inactive | Only states supported by complete coverage |
| 30/90-day changes | Current point-in-time median or inventory divided by its historical point-in-time value minus one |

Missing or zero comparison bases return null rather than a fabricated zero change.

## Confidence V1

Confidence is a deterministic weighted score from six normalized factors:

- sample size;
- freshness;
- effective source diversity;
- complete-coverage share;
- outlier stability;
- temporal continuity.

Weights and thresholds live in `config/market_baseline.yaml`. Fewer than the absolute minimum
samples is `insufficient`. Coverage below 0.5 or no recent complete evidence caps the result at
`low`. One source caps it at `medium`. Every component, weight, and cap reason is persisted.

## Liquidity V1

Liquidity is a configurable, non-LLM score. Components are inventory depth, new-listing
velocity, listing-exit velocity, days-on-market speed, asking-price stability, relisting
stability, and buyer-pool depth. Missing components are omitted and available weights are
renormalized. Insufficient available weight returns no score.

`listing_exit_velocity` means no longer observed under the lifecycle rules. Its response note
states that disappearance is not interpreted as a sale. Liquidity confidence combines baseline
confidence, available component weight, and sample signal.

## Fallback and shrinkage

Area bucket and layout never widen silently. Resolution follows community, submarket, district,
then Shanghai while preserving the requested segment.

If a community has some evidence but is below its configured threshold and the submarket is
usable, each quantile is transparently shrunk:

```text
w = local_count / (local_count + prior_strength)
resolved_quantile = w × local_quantile + (1-w) × parent_quantile
```

The API returns level used, local weight, sample count, confidence, fallback path, reason, and all
participating baseline versions. No usable parent returns `insufficient`; LISTING never falls
back to TRANSACTION or the reverse.

## Materialization, revisions, and provenance

The RQ market worker materializes daily rather than calculating on API requests. The scheduler
registers one next 01:15 Asia/Shanghai job. The job registers its successor before computing.

`baseline_materialization_run` identifies `(as_of_date, data_mode, calculation_version,
configuration_version, input_signature)`. The input signature hashes all eligible normalized
inputs and relevant dimensions. Repeating identical input reuses the successful run. Late-arriving
evidence changes the signature and appends a new revision. Older revisions remain queryable.

Each baseline contains:

- materialization run and input signature;
- exact window and input cutoff;
- observation IDs and outlier IDs;
- source list and observation interval;
- calculation and configuration versions;
- a globally unique baseline version;
- confidence and liquidity explanations.

The API reads only the newest successful materialization run for the server-controlled data mode.
It never combines windows from different runs.

## Data modes

`demo`, `sample`, and `live` coexist safely through required row-level mode fields. API clients
cannot choose a mode; deployment settings choose it. Non-live responses carry a warning.

LIVE rejects `sample_json` and local-file endpoints. The demo seed refuses to run unless the
server is explicitly in DEMO mode. Rental fixtures exist only in DEMO.

## API

- `GET /api/v1/market/baselines`
- `GET /api/v1/market/baselines/{district}/{submarket}`
- `GET /api/v1/market/communities/{community_id}/baseline`
- `GET /api/v1/market/liquidity`
- `GET /api/v1/market/data-quality`

Filters include evidence type, window, level, geography, area bucket, layout, and date where
applicable. Responses include data mode, data notice, an evidence-specific label, versions, and
provenance. Listing responses use `Listing Market Baseline`, never Fair Value.

## Operations

```bash
home-radar-market-enqueue --as-of-date 2026-09-01
home-radar-market-schedule
SHR_MARKET_DATA_MODE=demo home-radar-market-demo-seed
```

PostgreSQL 15 or newer is required because materialized slice uniqueness uses `NULLS NOT
DISTINCT`. Docker Compose uses PostgreSQL 16.

## P4 readiness boundary

The P3 contract, versioning, fallback, and API are ready to serve P4 inputs. Live fair-value work
must remain blocked until an authorized LIVE feed demonstrates adequate target-area coverage and,
for transaction-based valuation, authorized transaction evidence. Listing asks alone are not a
transaction comparable set.
