# Shanghai Home Radar Roadmap

> The actionable backlog and acceptance criteria are in [README](../README.md).
> This phase history must not be read as an end-to-end product readiness checklist.
> Browser research and evidence intake now have a separate local UI; unifying its observations,
> the database pipeline and daily buyer delivery remains unfinished. No P6 release is claimed.

## Current delivery boundary

The repository covers P0 through P5.5. P5.5 combines versioned market, valuation, and future
outputs into explainable classifications, workflow states, and relative ranks without a total score.

The 50-listing blind review is the next product gate. P6 follows it. Calibrated LIVE use remains
blocked until verified outcomes and employment, supply, and mode-isolation gates pass.

## Phase map

| Phase | Outcome | Gate |
| --- | --- | --- |
| P0 | Foundation | Local stack, migrations, API skeleton, settings, docs, and tests |
| P1 | Collector | One registered authorized-feed adapter creates canonical listings and immutable observations |
| P1.5/P2 | Hardening and lifecycle | Complete-only source-scope reconciliation and auditable listing history |
| P3 | Market baseline | Target areas have point-in-time price, supply, days-on-market, and liquidity baselines |
| P4 | Fair Value + Value Score | Daily auditable Top 10 potentially undervalued listings |
| P5 | Future Engine | Future Score, Obsolescence Risk, and 1Y/3Y/5Y scenarios |
| P5.5 | Decision orchestration | Four quadrants, workflow, eligible ranking, Why Ranked, and blind KPIs |
| P6 | Dashboard | Live decision summary, property cards, and drill-down |
| P7 | Notifications | Only configured high-score opportunities trigger alerts |
| P8 | Browser inquiry | Conditional low-frequency inquiry with human controls |
| P9 | Seller Intelligence | Motivation, inquiry evidence, and executable-price feedback |
| P10 | Negotiation Copilot | Evidence-based support with human authority retained |

## P0 foundation

P0 establishes:

- product and engineering documents;
- repository boundaries;
- PostgreSQL, PostGIS, Redis, FastAPI, RQ, Next.js, and Docker Compose;
- Alembic-managed schema;
- typed settings and non-secret domain configuration;
- structured logging, sample data, tests, CI, and local commands.

The P0 validation record belongs in the hardening report. The roadmap does not claim a check passed unless its exact result is recorded there.

## P1 collector

P1 establishes:

- a typed source-neutral observation contract;
- a source registry and one authorized JSON-feed prototype;
- raw payload persistence;
- canonical identity by `(source, source_listing_id)`;
- append-only snapshots;
- per-record savepoints;
- bounded retry and backoff;
- queued, CLI, and direct collection entry points.

P1 collection alone is not enough to infer absence. That capability is supplied by the P1.5/P2 hardening contract below.

## P1.5/P2 hardening and historical lifecycle

### Delivered design

- `FetchResult` separates returned items from source, scope, metadata, and completeness.
- `crawl_run` records status, completeness, counts, errors, source, and scope.
- `CrawlScope` creates a stable scope key from normalized source query inputs.
- `listing_presence` tracks active, missing-candidate, and inactive state per listing, source, and scope.
- only a succeeded complete run can reconcile unseen listings;
- the first complete miss creates `missing_candidate`;
- the configured consecutive-complete-miss threshold creates `inactive`;
- observation after missing or inactive creates `relisted` and resets the counter;
- PostgreSQL rejects `UPDATE` and `DELETE` on raw records and snapshots;
- advisory locks protect one source and scope from overlapping collection and reconciliation;
- the source registry rejects unknown or mismatched identities;
- `POST /api/v1/collector/runs` requires `X-API-Key`;
- run-list, run-detail, and listing-history reads expose the audit trail.

### Acceptance matrix

P1.5/P2 is ready for its final report when verification covers:

- complete versus partial and failed reconciliation;
- first miss, repeated miss, threshold transition, and relisting;
- same source with different scopes;
- concurrent same-scope collection;
- source identity mismatch;
- raw and snapshot update and delete rejection;
- migration from the initial schema and migration from an empty database;
- API-key rejection and acceptance;
- run list, run detail, and ordered listing history;
- Docker startup and an end-to-end fixture run.

Exact commands and results must be recorded in `HARDENING_REPORT.md`. This list is a gate, not a claim that checks ran.

### Lifecycle limitations

- `inactive` means absent after the threshold in a complete monitored scope. It does not prove sale.
- A successful partial run may ingest observed listings but cannot create absence evidence.
- Cross-source entity resolution remains unimplemented.
- The sample feed does not provide real Shanghai market coverage.

## P3 market baseline

Implementation and formulas are documented in `docs/MARKET_BASELINE_MODEL.md`. Validation results are recorded in `P3_VALIDATION_REPORT.md`.

### Entry gate

P3 starts only after:

1. the P1.5/P2 validation matrix has recorded passing results;
2. Alembic upgrade and Docker checks have recorded exact outcomes;
3. at least one authorized real source or operator-controlled export is available;
4. source scopes can declare defensible completeness;
5. target-area coverage and collection cadence are measurable;
6. run history shows that outages and partial runs do not create disappearance events;
7. community names and geographic coverage can be normalized without source-specific logic leaking downstream.

### Goal

Create point-in-time market context for outer Xuhui, northern Minhang, Putuo, Yangpu, and mature Pudong submarkets.

### Work

- curate district, submarket, community, area-bucket, and layout identities;
- calculate P25, P50, P75, medians, listing counts, new listings, price-drop ratio, days on market, and liquidity measures;
- exclude failed and partial runs from negative-presence evidence;
- use lifecycle events and immutable snapshots for point-in-time features;
- implement sparse-sample fallback through the hierarchy;
- record data cutoff, sample size, scope coverage, and calculation version;
- add community and metro spatial data without calling straight-line distance travel time.

### Exit criteria

- Every baseline exposes level, cutoff, sample size, scope coverage, and fallback path.
- Recalculation is reproducible from immutable inputs.
- Missing communities and target-area coverage are measurable.
- Source outages cannot distort supply or days-on-market metrics.

## P4 fair value and Value Score

Status: implemented and validated in `P4_VALIDATION_REPORT.md`.

### Goal

Produce a daily Top 10 of potentially undervalued listings with buyer-reviewable evidence.

### Work

- select comparables by community, area, layout, building age, and floor characteristics;
- configure adjustments for floor, orientation, elevator, age, metro, layout, noise, liquidity, and market trend;
- produce fair-value low, central, and high estimates with comparable count and confidence;
- calculate configured Value Score components and action bands;
- keep ask, fair value, broker indication, seller expectation, and executable price separate;
- expose comparables, adjustments, missing inputs, data cutoff, and versions.

### Exit criteria

- A daily job produces a reproducible Top 10.
- Every estimate and score links to point-in-time evidence.
- Sparse comparables reduce confidence or block a strong action.
- No LLM acts as the primary valuation engine.

P4 now delivers deterministic comparables, residual adjustments, weighted-median Fair Value ranges,
confidence, current Value Score, warning caps, cached results, batch jobs, APIs, five synthetic
cases, and real PostgreSQL/PostGIS validation. LIVE accuracy remains unclaimed until licensed
transaction outcomes exist.

## P5 through P10

### P5 Future Engine

P5 delivers current and future employment accessibility, Future Score, independent Obsolescence
Risk, Structural Alpha, and explainable 1Y/3Y/5Y bear, base, and bull scenarios. Planned projects
use realization probabilities.

The technical engine, source-independent contracts, persistence, cache, APIs, batch jobs,
synthetic cases, and backtesting interface are complete. Calibration and live investment claims
remain blocked until P4 has sufficient verified transaction outcomes and employment and supply
inputs have measured LIVE coverage.

### P5.5 Decision Orchestration

P5.5 combines P3, P4, and P5 without recalculating them. Opportunity classification, eligibility,
and workflow remain separate. Ranking is gated and lexicographic, and Value Traps never enter the
eligible list merely because their asking price falls. `ATTACK` is disabled. Frozen blind batches
support Precision@5, Precision@10, trap false positives, VIEW acceptance, and Top 10 acceptance.

### P6 Dashboard

Replace demo values with live decision data. Add price history, comparable evidence, valuation explanation, future outlook, seller intelligence, and inquiry history.

### P7 Notifications

Alert only on configured score and confidence thresholds. Deduplicate alerts and record why each fired.

### P8 Browser inquiry

Queue qualified listings only. Use low-frequency messages, preserve raw replies, pause at authentication or CAPTCHA, and keep all consequential actions human-controlled.

### P9 Seller Intelligence

Combine ask history, duration, seller reason, vacancy, broker guidance, offers, and inquiry evidence into a versioned executable-price range.

### P10 Negotiation Copilot

Summarize evidence and prepare negotiation support. The buyer retains authority for inspections, offers, commitments, signing, and payment.

## Cross-phase invariants

- Source adapters remain replaceable and registry-controlled.
- Source and scope identity stay stable.
- Failed, partial, or unknown runs never produce absence evidence.
- Raw records and snapshots reject updates and deletes.
- Derived outputs identify data cutoff, model version, and configuration version.
- Secrets and private conversations stay out of logs and source control.
- Retries are bounded and idempotent.
- No CAPTCHA or access-control bypass is permitted.
- No irreversible or binding action occurs without human approval.
- Migrations and tests accompany schema or behavior changes.

## Program risks

| Risk | Control |
| --- | --- |
| Partial feeds imitate disappearance | Explicit completeness and COMPLETE-only reconciliation |
| Overlapping workers race lifecycle state | Advisory lock by stable source and scope |
| Scope changes mix populations | Deterministic scope identity |
| Source identity drifts | Central registry and identity validation |
| History is edited or cascade-deleted | Database UPDATE/DELETE triggers and restricted foreign keys |
| One source is partial or unstable | Independent adapters and per-scope run history |
| Asking data differs from transactions | Separate price semantics and confidence |
| Sparse comparables create false discounts | Hierarchical fallback and confidence gates |
| Planned projects inflate forecasts | Versioned realization probabilities |
| Automation creates access risk | Private API key, authorized sources, and human control |
