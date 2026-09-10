# Shanghai Home Radar Data Model

## Design goals

The schema separates source evidence, current projections, and scope-aware lifecycle state. PostgreSQL is the system of record. PostGIS stores spatial data.

All timestamps are timezone-aware UTC values. Prices use exact decimals and store RMB. Raw records and snapshots are append-only evidence.

## Data flow

```text
crawl_run(source, scope, status, completeness)
  → raw_source_record (append-only source evidence)
  → listing (current canonical projection)
  → listing_snapshot (append-only observed state)
  → listing_presence (current state per source and scope)
  → listing_event (derived lifecycle and price events)
```

## Identity rules

| Entity | Identity | Rule |
| --- | --- | --- |
| Source | Registry `source_id` | Stable configuration key |
| Scope | `(source, scope_key)` | Stable hash of normalized query boundary |
| Listing | `(source, source_listing_id)` | Unique canonical listing within a source |
| Presence | `(listing_id, source, scope_key)` | One lifecycle projection per crawl population |
| Run snapshot | `(crawl_run_id, listing_id)` | At most one observed snapshot per listing per run |
| Timed snapshot | `(listing_id, snapshot_at)` | Idempotent observation time |

`source_url` is mutable evidence. It is not an identity key. Cross-source property identity is not part of P2.

## `crawl_run`

One bounded execution for a registered source and stable scope.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `id` | UUID | Primary key and correlation ID |
| `source` | varchar(50) | Registered source ID |
| `scope_key` | varchar(255) | Stable normalized scope identity |
| `status` | varchar(24) | `running`, `succeeded`, `failed`, `paused_auth`, or `already_running` |
| `completeness` | varchar(24) | `unknown`, `complete`, or `partial` |
| `started_at` | timestamptz | Worker start time |
| `finished_at` | timestamptz | Terminal time, nullable while running |
| `raw_item_count` | integer | Items returned by `FetchResult` |
| `normalized_item_count` | integer | Items accepted as canonical observations |
| `parse_error_count` | integer | Items rejected during normalization or persistence |
| `error_type` | varchar(160) | Sanitized terminal classification |
| `error_message` | text | Sanitized terminal detail |
| `metadata` | jsonb | Non-secret scope and coverage evidence |
| `created_at` | timestamptz | Database insert time |

The index `(source, scope_key, started_at)` supports run history and reconciliation audits.

Status and completeness answer different questions. A run can succeed with partial coverage and ingest observed items. Only `succeeded` plus `complete` permits absence reconciliation.

## `raw_source_record`

The exact record fetched from a source before canonical normalization.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `crawl_run_id` | UUID | Required restricted foreign key to `crawl_run` |
| `listing_id` | UUID | Nullable restricted foreign key after canonical resolution |
| `source_record_id` | varchar(255) | Adapter-extracted record identity, nullable on malformed records |
| `observed_at` | timestamptz | Source observation time or retrieval time |
| `payload` | jsonb | Exact source item |
| `normalization_status` | varchar(24) | `pending`, `success`, or `failed` |
| `normalization_error` | text | Sanitized failure detail |
| `created_at` | timestamptz | Database insert time |

An index on `(crawl_run_id, source_record_id)` supports per-run audit. Repeated runs intentionally preserve repeated evidence.

PostgreSQL triggers reject both `UPDATE` and `DELETE` on this table. Collector retries append new run evidence rather than rewriting an earlier record.

## `listing`

The current searchable projection of one source listing.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `source` | varchar(50) | Registered source ID |
| `source_listing_id` | varchar(255) | Stable source record identity |
| `source_url` | text | Latest observed URL |
| `district` | varchar(80) | Normalized district |
| `submarket` | varchar(120) | Normalized submarket |
| `community` | varchar(160) | Normalized community label |
| `longitude`, `latitude` | numeric(10,7) | Nullable WGS84 coordinates |
| `coordinates` | geometry(Point, 4326) | Nullable PostGIS point |
| `total_price` | numeric(14,2) | Current asking price |
| `unit_price` | numeric(14,2) | Current asking price per square metre |
| `area_sqm` | numeric(8,2) | Positive floor area |
| `bedrooms`, `living_rooms` | integer | Nullable non-negative counts |
| `floor` | varchar(80) | Normalized or source floor description |
| `total_floors` | integer | Nullable positive count |
| `orientation` | varchar(80) | Nullable |
| `year_built` | integer | Nullable and range-checked by the typed boundary |
| `elevator` | boolean | Nullable when unknown |
| `building_type` | varchar(80) | Nullable |
| `first_seen_at` | timestamptz | First accepted observation |
| `last_seen_at` | timestamptz | Latest accepted observation |
| `status` | varchar(32) | Current conservative lifecycle projection |
| `created_at`, `updated_at` | timestamptz | Projection timestamps |

The database enforces unique `(source, source_listing_id)`. Market, price, status, community, and spatial indexes support later queries.

Current status values are `active`, `missing_candidate`, `inactive`, and `sold`. Presence rows hold the scope-specific source of truth for inferred absence.

## `listing_snapshot`

The observed listing state at one point in time.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `listing_id` | UUID | Required restricted foreign key to `listing` |
| `crawl_run_id` | UUID | Restricted foreign key to provenance run |
| `snapshot_at` | timestamptz | Observation time |
| `total_price` | numeric(14,2) | Observed asking price |
| `unit_price` | numeric(14,2) | Observed unit price |
| `status` | varchar(32) | Observed or reconciled lifecycle state |
| `raw_payload` | jsonb | Preserved observation payload or reconciliation reason |

Constraints:

- unique `(listing_id, snapshot_at)`;
- unique `(crawl_run_id, listing_id)`;
- index `(listing_id, snapshot_at)` for ordered history;
- restricted foreign keys prevent evidence loss through cascade deletion;
- database triggers reject both `UPDATE` and `DELETE`.

New observations and lifecycle changes append snapshots. A correction cannot edit an earlier row through normal application access.

## `listing_presence`

The scope-aware current lifecycle state for a canonical listing.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `listing_id` | UUID | Restricted foreign key and composite primary key part |
| `source` | varchar(50) | Source ID and composite primary key part |
| `scope_key` | varchar(255) | Stable scope and composite primary key part |
| `state` | varchar(24) | `active`, `missing_candidate`, or `inactive` |
| `consecutive_complete_misses` | integer | Non-negative miss counter |
| `last_seen_complete_run_at` | timestamptz | Latest complete run that observed the listing |
| `created_at`, `updated_at` | timestamptz | Projection timestamps |

The primary key is `(listing_id, source, scope_key)`. The index `(source, scope_key, state)` supports reconciliation.

Only a successful complete run of the same source and scope changes absence state. Partial, unknown, or failed runs leave the row unchanged.

## `listing_event`

Append-oriented derived events explain material changes.

| Column | PostgreSQL type | Meaning |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `listing_id` | UUID | Restricted foreign key to `listing` |
| `crawl_run_id` | UUID | Nullable for legacy rows; otherwise restricted provenance foreign key |
| `event_type` | varchar(40) | Event category |
| `occurred_at` | timestamptz | Effective event time |
| `previous_value` | text | Prior price or status |
| `current_value` | text | New price or status |
| `details` | jsonb | Miss count, scope, run, or other explanation |

Event types are `new`, `price_cut`, `price_increase`, `missing_candidate`, `inactivated`, and `relisted`.

Events link decisions to state transitions. Snapshots and run provenance remain the underlying evidence.

## Reconciliation state machine

| Starting state | Evidence | Result | Counter | Event |
| --- | --- | --- | ---: | --- |
| No presence row | Listing observed | `active` | 0 | `new` for a new canonical listing |
| `active` | Absent from successful complete run | `missing_candidate` | 1 | `missing_candidate` |
| `missing_candidate` | Absent from another successful complete run below threshold | `missing_candidate` | Increment | No duplicate transition event |
| `missing_candidate` | Consecutive complete misses reach threshold | `inactive` | Threshold | `inactivated` |
| `missing_candidate` or `inactive` | Listing observed again | `active` | 0 | `relisted` |
| Any | Partial, unknown, or failed run | Unchanged | Unchanged | None from absence |

The default final threshold is configurable. It must be at least two. Inactive means no longer observed in this source scope after the threshold. It does not prove sale or legal removal.

## Scope aggregation rule

Presence is reconciled within one `(source, scope_key)` only. A run must not update another scope's presence rows. If any known scope still reports a listing active, the listing projection cannot claim global inactivity based on another scope's absence.

## Append-only and deletion behavior

- `raw_source_record` and `listing_snapshot` reject `UPDATE` and `DELETE` through database triggers.
- Restricted foreign keys prevent deleting a parent when historical evidence refers to it.
- `listing`, `listing_presence`, and an in-progress `crawl_run` are mutable projections.
- New evidence and recalculated decisions append rows.
- Retention or repair requires an explicit operator-controlled workflow, not routine ingestion.

## Market and decision schema tables

### `community`

Stores district, submarket, community, coordinates, build year, households, building type, metro facts, active supply, market medians, and liquidity score. Community alias mapping remains future work.

### `market_baseline`

Stores append-only Shanghai, district, submarket, community, area-bucket, and layout revisions for one evidence type and 30/90/180/365-day window. It includes P10 through P90, effective samples, listing metrics, confidence, liquidity, exact cutoffs, versions, and provenance. It links to `baseline_materialization_run`; late data appends a revision.

### `market_observation`

Stores source-independent `listing`, `transaction`, `rental`, `official_index`, and `external_baseline` evidence. Data mode and observation type are mandatory. Type-aware constraints keep listing asks, transactions, rents, and indexes semantically separate. Contextual outlier decisions live in baseline provenance and never mutate this table.

### `baseline_materialization_run`

Stores one versioned materialization input signature, cutoff, status, duration, and output counts. Identical input reuses a successful run. Late-arriving evidence creates a new input signature and revision.

### `valuation_result`

Stores one immutable P4 result for a canonical listing, data mode, input fingerprint, and model
version tuple. It contains Fair Value and its range, valuation basis, transaction support, confidence,
ask discount, optional documented executable price, Value Score, decision, comparable counts, P3
baseline references, comparables, adjustments, warnings, price history, score components, and
provenance.

The unique input/version key makes recomputation idempotent. Target changes, late comparable data,
new P3 versions, scoring configuration, or model versions append a new row. The table never stores a
fabricated seller bottom price. SAMPLE and DEMO results remain mode-separated from LIVE.

### `decision_assessment`

Stores one immutable P5.5 orchestration for an exact P3, P4, P5, data, and configuration version
chain. It keeps classification, workflow, eligibility, component values, hard risks, structured Why
Ranked evidence, and provenance. It does not store an aggregate decision score or investment
conclusion. Relative rank is calculated from the latest eligible universe rather than frozen here.

### `decision_validation_batch` and `decision_validation_item`

Freeze decision and listing snapshots for blind human review. Model classification and rank remain
hidden while the batch is in `blind_labeling`. Reveal requires all items to have one of the four
human labels and stores the resulting product KPIs. Revealed batches and their frozen model
snapshots are treated as immutable validation evidence.

### `inquiry`

Stores raw broker messages and structured seller evidence. It keeps broker-indicated price, seller expectation, offers, vacancy, mortgage, lease, hukou, tax, confidence, and timestamps. Raw messages remain authoritative.

## Price semantics

| Concept | Meaning |
| --- | --- |
| Asking price | Current advertised price |
| Fair value | Comparable-based explainable estimate |
| Broker-indicated price | Price level stated by a broker |
| Seller-expected price | Stated or inferred seller expectation |
| Estimated executable price | Current negotiable transaction range |

The schema must not reuse one field for these meanings.

## Spatial model

Coordinates use WGS84 and SRID 4326. Straight-line distance may support filtering, but it cannot be labeled transit time. Later accessibility records must preserve method, data version, employment-center version, and current versus future score.

## Migration history

- `0001_initial_schema` creates the initial P0/P1 domain and evidence tables.
- `0002_persistence_hardening` renames `collection_run` to `crawl_run`, adds source-scope provenance and completeness, adds `listing_presence`, links snapshots to runs, strengthens foreign keys, migrates old lifecycle labels, and rejects both updates and deletes on evidence tables.
- `0003_market_baseline_engine` adds crawl data modes, source-independent observations, versioned materialization runs, and the P3 baseline schema. It conservatively migrates legacy baselines as SAMPLE/LISTING/30-day evidence and records that assumption.
- `0004_fair_value_engine` adds the versioned P4 `valuation_result` cache, semantic constraints,
  input/version idempotency, listing provenance, and decision-score indexes.
- `0005_future_engine` adds source-independent P5 evidence and immutable future assessments.
- `0006_decision_orchestration` adds immutable P5.5 decisions and frozen blind-validation batches.

Later schema changes must use Alembic. Manual production edits are outside the supported workflow.

## Privacy and retention

- Credentials never enter domain tables.
- Routine logs exclude full source payloads and private inquiry messages.
- Raw evidence and seller intelligence remain inside the private application boundary.
- Backups preserve append-only history and inherit database access restrictions.
- Explicit operator action controls any retention deletion or audited repair.
