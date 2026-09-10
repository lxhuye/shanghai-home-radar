# Shanghai Home Radar 开发指南（历史操作说明）

本文保留早期 README 的接口与开发命令，部分阶段描述是历史记录，不代表当前产品已可上线。
当前架构、两条运行链路的断点与开发优先级以根目录 [README](../README.md) 为准。
命令中的路径均相对于仓库根目录；真实数据与本机验收产物不随开源代码分发。

The private browser monitor is available at <http://127.0.0.1:5057/>. It now includes passive
anti-bot detection, human handoff, six-hour checks, and a listing/price-change view. The first
61 records from the Xuhui 250–300 wan page were captured on 2026-09-08. Each new observation now
automatically runs the existing P4 valuation, P5 future assessment, and P5.5 decision engines in
an isolated research batch, with reference ranges, comparable explanations, downloads, and health
checks in the same page. This partial feed does not establish LIVE market coverage or calibrated
investment recommendations. See [the research pipeline](BROWSER_RESEARCH_PIPELINE.md).
See [the browser monitor runbook](BROWSER_HANDOFF.md) for Docker and local-browser startup.

Shanghai Home Radar is a private, explainable decision system for Shanghai second-hand homes. The
current repository covers P0 through P5.5: authorized-feed collection and hardening, the generic feed
gateway, versioned market baselines, deterministic Fair Value ranges, valuation confidence, and
current Value Score, the uncalibrated Future Engine, and deterministic decision orchestration.

The collection path keeps source adapters independent from valuation and forecasting. It stores raw records and snapshots as append-only evidence. It infers absence only after a successful complete run of the same stable source and scope.

The web page remains a decision-dashboard preview. Its scores are demo data. P3 through P5.5 APIs
disclose whether evidence is DEMO, SAMPLE, or LIVE. SAMPLE and DEMO analysis is explicitly
research-only.

## Repository contents

- FastAPI health, listing, listing-history, and crawl-run APIs
- PostgreSQL 16, PostGIS, SQLAlchemy, and Alembic migrations
- Redis and an RQ worker
- versioned 30/90/180/365-day market baselines, confidence, fallback, and Liquidity V1
- deterministic comparable selection, residual adjustments, weighted-median Fair Value ranges,
  confidence, Value Score, warning rules, and versioned valuation cache
- typed `FetchResult`, `CrawlScope`, and canonical listing observation contracts
- source registry with an authorized local-file or HTTP JSON adapter prototype
- stable source and scope identities
- raw source records, immutable snapshots, listing presence, and lifecycle events
- complete-only reconciliation with a configurable missing threshold
- PostgreSQL advisory locking by source and scope
- database protection against snapshot and raw-record updates and deletes
- `X-API-Key` protection for mutating collection requests
- source-independent future data contracts, employment and planning evidence, Future Score,
  independent Obsolescence Risk, Structural Alpha, and 1Y/3Y/5Y scenario ranges
- separate opportunity classification, workflow, eligibility, lexicographic ranking, Why Ranked,
  and frozen blind-review metrics without an aggregate Buy Score
- a timezone-aware daily pipeline with immutable Top opportunity artifacts, a human-readable digest,
  current-crawl change signals, a separately filtered fresh-opportunity list, and optional webhook
  delivery that remains explicitly research-only before calibration
- sample Shanghai-shaped data and backend and web validation commands

P3 definitions are in [MARKET_BASELINE_MODEL.md](MARKET_BASELINE_MODEL.md). P4 is defined
in [FAIR_VALUE_MODEL.md](FAIR_VALUE_MODEL.md) and [VALUE_SCORE_MODEL.md](VALUE_SCORE_MODEL.md).
Private historical validation reports are not distributed with the source code.
P5 methodology is in [FUTURE_MODEL.md](FUTURE_MODEL.md), [OBSOLESCENCE_MODEL.md](OBSOLESCENCE_MODEL.md),
and [SCENARIO_FORECAST_MODEL.md](SCENARIO_FORECAST_MODEL.md).
P5.5 is defined in [DECISION_ORCHESTRATION.md](DECISION_ORCHESTRATION.md).
Authorized listing and transaction acquisition paths are tracked in
[AUTHORIZED_DATA_PROCUREMENT.md](AUTHORIZED_DATA_PROCUREMENT.md).

## Quick start with Docker

Docker Desktop with Compose V2 is required.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api alembic upgrade head
docker compose exec api home-radar-seed
```

Open:

- Dashboard: <http://localhost:3000>
- API documentation: <http://localhost:8000/docs>
- Readiness: <http://localhost:8000/health/ready>

Read current listings:

```bash
curl 'http://localhost:8000/api/v1/listings?limit=10'
curl 'http://localhost:8000/api/v1/market/baselines?window_days=90'
curl 'http://localhost:8000/api/v1/market/data-quality'
curl 'http://localhost:8000/api/v1/valuation/listings/LISTING_UUID'
curl 'http://localhost:8000/api/v1/decisions/listings/LISTING_UUID'
curl 'http://localhost:8000/api/v1/decisions/opportunities?limit=10'
```

Enqueue a collection run. The header value must match `SHR_API_KEY` in `.env`.

```bash
curl -X POST \
  -H 'X-API-Key: dev-only-change-me' \
  'http://localhost:8000/api/v1/collector/runs'
```

Read recent runs and one run:

```bash
curl 'http://localhost:8000/api/v1/collector/runs'
curl 'http://localhost:8000/api/v1/collector/runs/RUN_UUID'
```

Read one listing's ordered history:

```bash
curl 'http://localhost:8000/api/v1/listings/LISTING_UUID/history'
```

Run the configured source directly:

```bash
docker compose exec api home-radar-collect
```

Stop services while keeping database and Redis volumes:

```bash
docker compose down
```

Change the development API key before any shared deployment. V0.1 read routes rely on an operator-controlled private network.

## Local development

Use Python 3.12 and Node.js 22.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install '.[dev]'
cp .env.example .env
docker compose up -d db redis
.venv/bin/alembic upgrade head
.venv/bin/home-radar-seed
.venv/bin/uvicorn home_radar_api.main:app --reload
```

Start the worker in another shell:

```bash
.venv/bin/rq worker collector --url redis://localhost:6379/0
.venv/bin/rq worker market --url redis://localhost:6379/0 --with-scheduler
.venv/bin/home-radar-market-schedule
.venv/bin/rq worker valuation --url redis://localhost:6379/0
.venv/bin/home-radar-valuation-enqueue
.venv/bin/rq worker future --url redis://localhost:6379/0
.venv/bin/home-radar-future-enqueue
.venv/bin/rq worker decision --url redis://localhost:6379/0
.venv/bin/home-radar-decision-enqueue
```

Start the web app in another shell:

```bash
cd apps/web
npm ci
npm run dev
```

## Validation commands

These commands are the requested checks. Their final results are not implied here.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

```bash
cd apps/web
npm run lint
npm run typecheck
npm run build
```

Migration and Docker checks must be rerun for the public revision; private historical reports
are not distributed with the source code.

## Collector feed contract

`CanonicalJsonFeedAdapter` is an authorized-feed gateway, not a scraper. It accepts a local JSON file or an HTTP endpoint through the versioned canonical contract. Full setup and provider onboarding are documented in [DATA_SOURCE_INTEGRATION.md](DATA_SOURCE_INTEGRATION.md).

```json
{
  "schema_version": "1.0",
  "source_id": "sample_json",
  "scope": {
    "city": "shanghai",
    "district": "Xuhui",
    "submarket": "Huajing",
    "filters": {}
  },
  "completeness": "complete",
  "coverage": {
    "page_count": 1,
    "pages_fetched": 1,
    "reported_total": 1,
    "items_returned": 1
  },
  "metadata": {
    "source_timestamp": "2026-09-01T00:00:00Z"
  },
  "items": [
    {
      "listing_id": "source-123",
      "url": "https://permitted-source.example/listing/123",
      "district": "Xuhui",
      "submarket": "Huajing",
      "community": "Example Garden",
      "price_wan": 305,
      "area_sqm": 67.4,
      "bedrooms": 2,
      "living_rooms": 1,
      "status": "active"
    }
  ]
}
```

Configure source, endpoint, and scope in `.env`:

```dotenv
SHR_COLLECTOR_SOURCE=sample_json
SHR_COLLECTOR_ENDPOINT=https://permitted-source.example/feed.json
SHR_COLLECTOR_CITY=shanghai
SHR_COLLECTOR_DISTRICT=Xuhui
SHR_COLLECTOR_SUBMARKET=Huajing
SHR_COLLECTOR_QUERY=
SHR_COLLECTOR_AUTH_MODE=NONE
```

`CrawlScope` converts normalized source and query fields into a stable `scope_key`. A material scope change creates a different key. This prevents a narrower feed from marking listings outside that feed as missing.

The adapter returns an explicit `FetchResult`:

```text
source_id
scope_key
raw_items
completeness
metadata
```

HTTP success does not imply complete coverage. The gateway separately records transport success, schema validity, and coverage validity. Missing or inconsistent coverage becomes partial and cannot reconcile absence.

## Reconciliation behavior

Only a run with `status=succeeded` and `completeness=complete` reconciles unseen listings in the same source and scope.

```text
observed
  → active, miss count 0

first qualifying absence
  → missing_candidate, miss count 1

consecutive complete misses reach configured threshold
  → inactive

observed after missing_candidate or inactive
  → active, miss count 0, relisted event
```

Incomplete, unknown, failed, authentication-blocked, or identity-mismatched runs do not increment miss counts. Inactive means absent from the monitored complete feed after the threshold. It does not prove a sale.

PostgreSQL advisory locks prevent overlapping collection and reconciliation for the same source and scope. Different scopes remain independent.

## API

| Method | Path | Purpose | Protection |
| --- | --- | --- | --- |
| GET | `/health/live` | Process liveness | Private network |
| GET | `/health/ready` | Database readiness | Private network |
| GET | `/api/v1/listings` | Filtered current projection | Private network |
| GET | `/api/v1/listings/{listing_id}` | Listing detail | Private network |
| GET | `/api/v1/listings/{listing_id}/history` | Ordered snapshots and lifecycle events | Private network |
| GET | `/api/v1/collector/runs` | Recent crawl runs | Private network |
| GET | `/api/v1/collector/runs/{run_id}` | Run status, completeness, counts, and errors | Private network |
| POST | `/api/v1/collector/runs` | Enqueue a registered source | `X-API-Key` |
| GET | `/api/v1/valuation/listings/{listing_id}` | Cached Fair Value and Value Score | Private network |
| POST | `/api/v1/valuation/evaluate` | Evaluate structured canonical property input | Private network |
| GET | `/api/v1/valuation/listings/{listing_id}/comparables` | Selected comparable explanation | Private network |
| GET | `/api/v1/valuation/listings/{listing_id}/explanation` | Baseline and adjustment explanation | Private network |
| GET | `/api/v1/future/listings/{listing_id}` | Cached P5 assessment | Private network |
| GET | `/api/v1/future/listings/{listing_id}/factors` | Current/future factor evidence | Private network |
| GET | `/api/v1/future/listings/{listing_id}/scenarios` | 1Y/3Y/5Y scenario ranges | Private network |
| GET | `/api/v1/future/listings/{listing_id}/risks` | Obsolescence and warning breakdown | Private network |
| GET | `/api/v1/decisions/listings/{listing_id}` | P5.5 investment card and workflow | Private network |
| GET | `/api/v1/decisions/opportunities` | Ranked eligible opportunity universe | Private network |
| GET | `/api/v1/decisions/listings/{listing_id}/why-ranked` | Deterministic rank explanation | Private network |
| POST | `/api/v1/decisions/validation/batches` | Freeze a blind-review batch | `X-API-Key` |
| POST | `/api/v1/decisions/validation/batches/{batch_id}/labels` | Add human blind labels | `X-API-Key` |
| POST | `/api/v1/decisions/validation/batches/{batch_id}/reveal` | Reveal model output and KPIs | `X-API-Key` |

The earlier `/api/v1/collection-runs` route is not a compatibility promise for this private V0.1 branch.

## Data behavior

- `listing` is a mutable current projection keyed by `(source, source_listing_id)`.
- `crawl_run` records source, scope, status, completeness, counts, metadata, and errors.
- `listing_presence` stores lifecycle state per listing, source, and scope.
- `listing_snapshot` and `raw_source_record` reject both updates and deletes.
- A successful partial run may add observations but cannot infer absence.
- Reprocessing cannot create a second canonical listing.
- Every later score and forecast must carry input cutoff, model version, and configuration version.

## Source rules

All collection entry points resolve adapters through the source registry. Configured source ID, adapter source ID, fetch-result source ID, and observation source ID must agree.

No adapter may bypass authentication, CAPTCHA, rate limits, licensing, or source access controls.

## Repository map

```text
apps/web                       Next.js dashboard preview
services/api                   FastAPI boundary and private mutation auth
services/collector             registry, adapters, ingestion, reconciliation, jobs
services/valuation             P4 comparables, adjustments, Fair Value, scoring, cache, jobs
services/forecasting           P5 factors, risk, scenarios, cache, batch jobs, and backtesting
services/decision              P5.5 classification, workflow, ranking, explanations, and blind tests
services/scoring               configurable weighted score engine
services/browser_agent         human-gated P8 boundary
services/notification          P7 boundary
packages/models                SQLAlchemy domain models
packages/shared                settings, database, logging
packages/prompts               future bounded LLM prompts
infra/migrations               Alembic history
infra/docker                   runtime images
config                         versioned domain parameters
data/sample                    safe fixture feed
docs                           product and engineering specifications
```

## Known limitations

- The sample adapter does not provide real Shanghai coverage or transaction evidence.
- No authorized live source credentials or licensed export are included.
- The dashboard does not query the live decision API.
- Cross-source property matching is not implemented.
- Disappearance is source-and-scope evidence, not proof of sale.
- Read APIs rely on private-network isolation.
- P4 remains uncalibrated against live verified transactions; the backend reports no accuracy claim.
- P5 and P5.5 remain UNCALIBRATED because verified LIVE outcomes and coverage gates are unavailable.
- Live dashboard integration, alerts, and inquiry remain later work.

## Next gate

P5.5 is technically complete as a research workflow. The 50-listing blind review and data
calibration are the next product gates, followed by P6 dashboard integration. LIVE
calibration remains blocked until verified transaction outcomes, employment and supply coverage,
and mode-isolation gates are sufficient. See [ROADMAP.md](ROADMAP.md).

Product and model decisions remain in [PRD.md](PRD.md), [SCORING_MODEL.md](SCORING_MODEL.md), and [FORECASTING_MODEL.md](FORECASTING_MODEL.md).
