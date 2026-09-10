# Shanghai Home Radar Architecture

> Current runtime map: [README](../README.md). In addition to the database pipeline below,
> `scripts/browser_handoff.py` and `scripts/research_pipeline.py` implement an isolated,
> file-backed research workflow. It reuses the model engines but does not populate the main
> database or daily notifications. The Next.js app remains a fixture-backed preview.
> The following phased design is not a claim of calibrated LIVE delivery.

## Scope

This document covers P0 through P5.5: foundation, authorized-feed collection, reliability hardening,
the generic feed gateway, versioned market baselines, deterministic Fair Value, and current Value
Score, future assessment, and decision orchestration. Inquiry, seller intelligence, and
notifications remain later modules.

## Architectural principles

1. **Canonical data is source-independent.** Adapters translate external records at the edge. Downstream modules use typed internal models only.
2. **Absence needs evidence.** A missing record changes lifecycle state only after a successful, explicitly complete run of the same source and scope.
3. **History is append-only.** Snapshots and raw records reject both updates and deletes at the database boundary.
4. **Identity is stable.** Source IDs come from a registry. Scope keys come from normalized query inputs. URLs are evidence, not identity.
5. **Derived decisions are auditable.** Results identify their inputs, configuration, model version, and data cutoff.
6. **Private operations are authenticated.** Mutating collection endpoints require `X-API-Key` and remain on an operator-controlled network.
7. **Concurrent collection is scope-safe.** PostgreSQL advisory locks prevent overlapping reconciliation for the same source and scope.
8. **V0 stays maintainable.** Docker Compose and one RQ worker are sufficient. Kubernetes is excluded.

## System flow

```text
Source registry
      ↓
Provider normalizer → canonical feed → registered source adapter
      ↓
FetchResult(source_id, scope_key, completeness, raw_items, metadata)
      ↓
Raw records + canonical observations
      ↓
Listings + immutable snapshots
      ↓
COMPLETE-only source/scope reconciliation
      ↓
Listing presence + lifecycle events
      ↓
Historical listing database
      ↓
Market Baseline → Fair Value → Value Score ─┐
      ↓                                     ├→ P5.5 classification + eligibility + workflow
Future Engine → Future Score + risk ────────┘                 ↓
                                               gated relative ranking + Why Ranked
```

P0 through P3 implement the path ending at versioned market baselines. Later engines must not import adapter code or read source-specific JSON fields.

## Runtime topology

| Component | Current responsibility |
| --- | --- |
| Next.js web | Static decision-dashboard preview with demo data |
| FastAPI API | Evidence reads, P3/P4/P5 outputs, P5.5 decisions, rankings, explanations, and blind review |
| Collector worker | Resolves authorized feeds, validates coverage, normalizes, ingests, and reconciles |
| PostgreSQL + PostGIS | Canonical evidence and immutable versioned P3 through P5.5 results |
| Redis + RQ | Collector, market, valuation, future, and decision batch queues |
| Alembic | Ordered schema creation and hardening migration |
| Docker Compose | Local database, Redis, API, worker, and web orchestration |

The Python services may share one package and container image in V0. Their dependency boundaries remain logical contracts.

## Repository boundaries

```text
apps/web                    Decision UI preview
services/api                HTTP and authentication boundary
services/collector          Registry, adapters, ingestion, reconciliation, jobs
services/market             Observation projection, statistics, materialization, fallback, jobs
services/valuation          Comparable selection, adjustments, Fair Value, confidence, cache, batch jobs
services/forecasting        Source-neutral scenario contract
services/decision           P5.5 orchestration, ranking, explanations, blind validation
services/scoring            Configured score contract and engine
services/browser_agent      Human-gated P8 boundary
services/notification       P7 boundary
packages/models             Canonical typed and persistence models
packages/shared             Settings, database, logging, shared errors
packages/prompts            Future bounded LLM prompts
infra/migrations            Alembic migration history
infra/docker                Runtime images
config                      Versioned non-secret domain parameters
data/sample                 Authorized fixture feed
docs                        Product and engineering specifications
```

Adapters depend on the canonical collector contract. Valuation, forecasting, and scoring do not depend on adapters.

## P3 market baseline flow

```text
successful crawl snapshots ─┐
authorized external evidence ├→ market_observation by type and data mode
demo-only rental fixtures ───┘
                                  ↓
                         daily input signature
                                  ↓
                  30/90/180/365-day materialization
                                  ↓
          percentiles + listing metrics + confidence + liquidity
                                  ↓
           versioned market_baseline + contextual provenance
                                  ↓
              API latest successful run + explicit fallback
```

P3 is evidence-type strict. Listing asks, transactions, rentals, indexes, and external baselines
are queried and materialized independently. One run produces all windows so an API response never
needs to combine revisions. Identical input is idempotent; late input appends a new revision.

The API cannot select a data mode. Deployment configuration chooses DEMO, SAMPLE, or LIVE, and
every crawl, observation, materialization, and baseline carries that mode. LIVE rejects fixtures
and local sample feeds.

## P4 valuation flow

```text
mode-specific target observation + canonical features + history
                              ↓
              deterministic comparable selection
                    90D → 180D → 365D
                  TIER_1 → TIER_2 → TIER_3
                              ↓
            P3 exact-segment hierarchy as TIER_4
                              ↓
      residual floor/elevator/orientation/age adjustments
                              ↓
           MAD filtering + weighted median + range
                              ↓
      valuation confidence + separate current Value Score
                              ↓
       warnings + versioned valuation_result input cache
```

P4 never imports a source adapter and never treats a listing ask as a transaction. P3 owns market
location, area, layout, and liquidity effects. P4 owns unit-specific residuals and the current-price
screening decision. The target ask, listing history, and seller signal are excluded from intrinsic
Fair Value and enter only Value Score.

The four valuation endpoints read or create a fingerprinted cached result. Batch recalculation runs
through the valuation RQ queue. Changes to target price or features, comparable observations, P3
baseline versions, history, configuration, or model versions append a new result. Same-input replay
is idempotent and serialized by PostgreSQL transaction advisory lock.

The deployment selects DEMO, SAMPLE, or LIVE. API clients cannot override it. SAMPLE and DEMO
valuations are explicitly demo-only. Model details are in `docs/FAIR_VALUE_MODEL.md` and
`docs/VALUE_SCORE_MODEL.md`.

## Source registry

The collector resolves a configured source ID through a registry of adapter factories. The registry is the only supported construction path for scheduled, queued, CLI, and API-triggered runs.

The registry enforces these rules:

- an unknown source ID fails before collection;
- the configured source ID must match the adapter's `source_id`;
- the `FetchResult.source_id` must match the adapter and configuration;
- every normalized observation must carry the same source ID;
- source-specific credentials and endpoints stay in settings, not domain models.

Adding a source means adding a normalizer or adapter, a capability profile, and one registry entry. It does not change listing identity, lifecycle, valuation, or forecasting contracts.

## Stable source and scope identity

`source_id` is a stable registry key. It is not a display name or hostname.

`CrawlScope` represents the exact source query boundary. Its inputs include city and optional district, submarket, query, and normalized filters. It produces a deterministic `scope_key` from the source ID and sorted normalized scope fields.

The same logical query must always produce the same key. A material query or filter change must produce a different key. This prevents one crawl from reconciling listings that were outside its coverage.

Identity has three levels:

| Level | Key | Meaning |
| --- | --- | --- |
| Source listing | `(source, source_listing_id)` | Stable canonical listing identity within one source |
| Crawl coverage | `(source, scope_key)` | Exact population a run is allowed to reconcile |
| Presence | `(listing_id, source, scope_key)` | Lifecycle state of one listing within that population |

Cross-source property matching is outside P2. Future entity resolution may associate records without replacing their source identities or histories.

## Explicit `FetchResult`

An adapter fetch returns one validated `FetchResult` before per-item normalization:

```text
source_id
scope_key
raw_items
completeness: complete | partial
metadata
```

The result separates transport success from coverage completeness. HTTP 200 does not imply complete coverage. Pagination truncation, caps, partial exports, or an interrupted source response must produce `partial` or fail the run.

`unknown` is a persisted run default for a run that has not received a valid result. A successful adapter result cannot declare unknown completeness.

Metadata may record sanitized coverage evidence such as page counts, source timestamps, or adapter diagnostics. It must not contain credentials.

## Collection and ingestion sequence

```text
API, CLI, or scheduler
  → resolve registered adapter
  → derive stable source + scope key
  → acquire advisory lock for source + scope
  → create crawl_run(status=running, completeness=unknown)
  → fetch one FetchResult
  → verify source and scope identity
  → persist each raw item
  → normalize and ingest each valid observation in a savepoint
  → append one run-linked snapshot per observed listing
  → if and only if result is COMPLETE, reconcile unseen presence rows
  → finish run as succeeded or failed
  → release advisory lock
```

The per-item savepoint keeps one invalid record from discarding valid items. The outer run records aggregate raw, normalized, and parse-error counts.

## Advisory locks

Collection obtains a PostgreSQL advisory lock derived from `(source, scope_key)`. A concurrent worker for the same identity must not ingest and reconcile at the same time. The collector returns a classified already-running failure or serializes according to the implementation policy.

Different scopes may run independently. The lock is always released on success or failure. The database remains the coordination authority, so CLI, API, and RQ paths share the same protection.

## Canonical ingestion and idempotency

The canonical listing key is `(source, source_listing_id)`. Source URLs may change and are updated only on the current projection.

Each valid item performs these actions:

1. append its raw source record for the run;
2. resolve or create the canonical listing;
3. append a snapshot linked to the crawl run;
4. update the current listing projection;
5. append price or lifecycle events when state changes;
6. reset the matching presence row to active.

Database uniqueness on `(crawl_run_id, listing_id)` prevents duplicate snapshots within a run. `(listing_id, snapshot_at)` protects observation-time idempotency. Replaying input cannot create a second canonical listing.

## Append-only protection

`listing_snapshot` and `raw_source_record` are evidence tables. PostgreSQL triggers reject `UPDATE` and `DELETE`, not only application-level writes. Foreign keys use restrictive deletion where evidence would otherwise be lost through a cascade.

New observations append rows. Corrections require a new migration or explicit audited repair process. Routine collector code never edits history.

`listing` and `listing_presence` are current projections and may update. `crawl_run` may update while its run moves from running to a terminal state.

## Lifecycle and presence

`listing_presence` keeps lifecycle state per listing, source, and scope. Its states are:

- `active` after the listing is observed;
- `missing_candidate` after it is absent from a successful complete run;
- `inactive` after it reaches the configured consecutive-complete-miss threshold.

Observation after `missing_candidate` or `inactive` resets the miss count and creates a relisted event. A first qualifying miss creates a temporary-disappearance event. Reaching the threshold creates a final-disappearance event.

Final disappearance means inactive in the monitored source scope. It does not prove a sale.

If a listing has presence in more than one scope, absence in one scope is not evidence about another. The current listing projection must remain conservative and cannot become globally inactive while a known presence row is active.

## COMPLETE-only reconciliation

Reconciliation runs only when all of these conditions hold:

- the adapter returned a valid `FetchResult`;
- the run source and scope match the configured adapter;
- collection reached a successful terminal state;
- completeness is `complete`;
- the worker owns the source-and-scope advisory lock.

A partial, unknown, failed, authentication-blocked, or identity-mismatched run does not increment miss counters and does not mark unseen listings missing. Parse errors remain visible in run counters and raw records. The collector must not treat a source outage as a market-wide disappearance.

## Crawl-run state

`crawl_run.status` records execution state:

- `running` while the worker owns the run;
- `succeeded` after ingestion and any allowed reconciliation commit;
- `failed` after a terminal error;
- `paused_auth` when the source requires authentication;
- `already_running` when another worker owns the same source-scope lock.

`crawl_run.completeness` records coverage evidence independently:

- `unknown` before a valid fetch result or when coverage is not established;
- `complete` when the adapter asserts full coverage for its scope;
- `partial` when the fetch succeeded but did not cover the full scope.

A succeeded partial run may add positive observations. It cannot create absence evidence.

## API boundary

The current private API exposes:

| Method | Path | Purpose | Authentication |
| --- | --- | --- | --- |
| GET | `/health/live` | Process liveness | Private-network boundary |
| GET | `/health/ready` | Database readiness | Private-network boundary |
| GET | `/api/v1/listings` | Current listing projection | Private-network boundary |
| GET | `/api/v1/listings/{listing_id}` | Listing detail | Private-network boundary |
| GET | `/api/v1/listings/{listing_id}/history` | Ordered snapshots and lifecycle events | Private-network boundary |
| GET | `/api/v1/collector/runs` | Recent crawl runs | Private-network boundary |
| GET | `/api/v1/collector/runs/{run_id}` | Crawl-run detail and counters | Private-network boundary |
| POST | `/api/v1/collector/runs` | Enqueue a registered collection run | `X-API-Key` required |

The private V0.1 branch does not promise compatibility for the earlier `/api/v1/collection-runs` route.

The API key is compared securely and never logged. Read routes still rely on the private-network boundary. Public deployment needs a fuller authentication and authorization layer.

## Reliability and error handling

- Retry only temporary transport and source failures.
- Use bounded exponential backoff.
- Classify authentication, payload, identity, already-running, parse, and persistence failures.
- Do not retry authentication or CAPTCHA as ordinary transport failures.
- Store sanitized run errors and per-record normalization errors.
- Never log secrets or full private conversations.
- Keep all collection entry points on the same registry, lock, and reconciliation path.

## Configuration

Environment variables hold database, Redis, endpoint, source, scope, retry, miss-threshold, and API-key settings. Versioned files hold non-secret scoring and forecasting parameters.

Startup validation must reject an unsupported source, invalid threshold, malformed endpoint, or absent production secret. Logs may include source and scope keys but not credentials.

## Privacy and safety

- Keep the API on an operator-controlled private network.
- Change the development API key before any shared deployment.
- Restrict raw source records to the private application and backups.
- Do not bypass authentication, CAPTCHA, rate limits, licensing, or access controls.
- Do not perform aggressive collection or messaging.
- Keep inspections, offers, commitments, signing, and payment under human control.

## Observability

Structured events include run ID, source, scope key, adapter version when available, status, completeness, item counts, retry attempt, duration, and sanitized error type.

Operational review should track:

- successful complete, successful partial, and failed runs;
- last successful complete run by source and scope;
- raw, normalized, and parse-error counts;
- active, missing-candidate, inactive, and relisted transitions;
- rejected concurrent runs;
- elapsed time and retry counts.

## Later engine boundaries

Market baseline, valuation, forecasting, and scoring read canonical listings, snapshots, events, presence, and run provenance. They never infer absence from runs that were not successful and complete. They also never read crawler-specific fields as primary inputs.

## Current limitations

- The registered sample JSON adapter is an authorized-feed prototype, not real Shanghai coverage.
- Cross-source entity resolution is not implemented.
- Inactive status means absent after the configured complete-run threshold, not sold.
- Read endpoints rely on private-network isolation in V0.1.
- The dashboard uses demo data.
- P3–P10 decision engines remain outside this hardening pass.
