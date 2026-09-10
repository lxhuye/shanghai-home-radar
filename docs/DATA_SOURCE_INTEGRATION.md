# Data Source Integration

Shanghai Home Radar accepts authorized provider data through one versioned canonical feed. A
provider-specific normalizer converts its export or API response at the boundary. The existing
collector, raw-record audit, snapshots, and lifecycle reconciliation remain source-independent.

```text
Authorized source
  → provider normalizer
  → canonical feed
  → CanonicalJsonFeedAdapter
  → existing CollectorService
  → raw records, snapshots, presence, lifecycle events
```

This gateway does not bypass authentication, licensing, rate limits, CAPTCHA, or access controls.

## Canonical schema 1.x

```json
{
  "schema_version": "1.0",
  "source_id": "partner_feed",
  "scope": {
    "city": "shanghai",
    "district": "Xuhui",
    "submarket": "Huajing",
    "query": "230-330w",
    "filters": {}
  },
  "completeness": "complete",
  "coverage": {
    "page_count": 12,
    "pages_fetched": 12,
    "reported_total": 347,
    "items_returned": 347
  },
  "metadata": {
    "source_timestamp": "2026-09-01T08:00:00Z"
  },
  "items": [
    {
      "listing_id": "123456",
      "url": "https://source.example/listing/123456",
      "district": "Xuhui",
      "submarket": "Huajing",
      "community": "Example Garden",
      "price_wan": 305,
      "area_sqm": 67.4,
      "bedrooms": 2,
      "living_rooms": 1,
      "status": "active",
      "observed_at": "2026-09-01T08:00:00Z"
    }
  ]
}
```

The gateway accepts additive top-level, metadata, coverage, and listing fields in `1.1` and later
`1.x` feeds. It rejects unsupported major versions. Scope extensions are fail-closed because an
unknown boundary must never be silently excluded from reconciliation identity.

Each listing requires a stable provider `listing_id`, an HTTP(S) URL without credentials, district,
submarket, community, positive `price_wan`, and positive `area_sqm`. Coordinates must be supplied as
a longitude/latitude pair. A malformed top-level feed fails the run. A malformed individual listing
is preserved as a failed raw record and makes the otherwise successful run partial.

For `authorized_api` in LIVE mode, every row must also carry a timezone-aware `observed_at`. By
default every timestamp must be within 36 hours of the scan and no more than five minutes in the
future. Missing, invalid, stale, or future timestamps downgrade the fetch to partial, so the daily
pipeline stops before valuation and never reconciles absent listings. Configure the boundary with
`SHR_COLLECTOR_MAX_OBSERVATION_AGE_HOURS`; do not widen it unless the provider's snapshot semantics
are documented.

## Source and scope identity

`source_id` is the configured registry key, not a hostname or display name. The payload value must
equal `SHR_COLLECTOR_SOURCE`. `sample_json` and `partner_feed` use the generic JSON adapter;
`partner_csv` reads an authorized local CSV export and its companion manifest.

The configured city, district, submarket, query, and filters produce a stable scope key. The payload
scope must describe the same normalized boundary. A mismatch fails before ingestion. V0.1 does not
trust a provider-selected runtime scope because the collector acquires its advisory lock before the
fetch. This prevents a narrow response from reconciling listings outside its declared scope.

## Completeness and coverage

Transport, schema, and coverage are independent facts. HTTP 200 only proves transport success.

The run is eligible for `complete` only when:

- transport succeeded;
- the canonical envelope and configured identities are valid;
- the provider declared `complete`;
- `items_returned` equals the actual item count;
- supplied `reported_total` equals `items_returned`;
- supplied `page_count` equals `pages_fetched`;
- the capability profile's required total and pagination evidence is present.

Missing coverage, an incomplete page range, a total mismatch, or provider-declared partial produces
a successful partial run. Partial runs may ingest valid observations but never reconcile absence.
An empty complete feed is valid only with explicit zero-count coverage evidence.

`SHR_COLLECTOR_ALLOW_COMPLETE_WITHOUT_COVERAGE=true` is an explicit operator override. It should be
used only when the provider's documented export semantics independently guarantee full coverage.
The resulting override remains visible through run metadata.

## Authentication

Credentials come from environment variables or the deployment secret store. They never belong in
feed URLs, payloads, source files, run metadata, logs, or database records.

```dotenv
SHR_COLLECTOR_AUTH_MODE=NONE
SHR_COLLECTOR_BEARER_TOKEN=
SHR_COLLECTOR_BASIC_USERNAME=
SHR_COLLECTOR_BASIC_PASSWORD=
SHR_COLLECTOR_CUSTOM_HEADER_NAME=
SHR_COLLECTOR_CUSTOM_HEADER_VALUE=
```

Set one mode: `NONE`, `BEARER_TOKEN`, `BASIC_AUTH`, or `CUSTOM_HEADER`. The selected mode must have
exactly its required values. Invalid, empty, conflicting, or newline-containing credentials fail
closed. Custom auth headers cannot override transport or standard authorization headers.

Production HTTP feeds must use HTTPS. Redirects are not followed. An authenticated endpoint override
must keep the configured scheme, host, and port. Provider responses with 401 or 403 produce
`paused_auth` without retry or reconciliation.

The canonical contract rejects credential-shaped fields recursively. Raw provider listing content is
otherwise stored with its crawl run and normalization outcome for audit.

## Capabilities and adapter registration

Each registry source has a typed capability profile:

```text
supports_listing_history
supports_reported_total
supports_pagination
supports_coordinates
supports_property_attributes
supports_transaction_data
```

The profile is stored as safe booleans in crawl-run metadata. It controls required coverage evidence
and prepares later multi-provider logic without changing the database schema.

To add an authorized canonical JSON provider:

1. Add its stable ID and `ProviderCapabilities` in
   `services/collector/src/home_radar_collector/registry.py`.
2. Configure its endpoint, exact scope, authentication mode, and secret environment variables.
3. Validate a sanitized canonical response locally.
4. Run unit tests and the PostgreSQL lifecycle integration suite.
5. Enable `complete` only after every onboarding item below is documented.

Non-canonical inputs implement the `FeedNormalizer` protocol. `CsvFeedNormalizer` is the local
example. It defaults to partial and does not invent totals from its own row count. A CSV export may
declare complete only when an external provider manifest supplies trustworthy total or pagination
coverage.

```python
context = CsvFeedContext(
    source_id="partner_feed",
    scope=CrawlScope(city="shanghai", district="Xuhui"),
    completeness=CrawlCompleteness.COMPLETE,
    coverage=FeedCoverage(
        reported_total=347,
        page_count=1,
        pages_fetched=1,
    ),
)
normalizer = CsvFeedNormalizer(context)
canonical_payload = normalizer.normalize(csv_bytes)
```

Future CSV, Excel, partner API, and manual-export normalizers should only translate provider fields
and evidence into this contract. They do not modify collector lifecycle or database behavior.

## Authorized partner CSV drop

The file-drop path is intended for a broker or data partner that can export CSV but does not expose
an API. Configure the finalized file, not a directory or temporary upload:

```dotenv
SHR_COLLECTOR_SOURCE=partner_csv
SHR_COLLECTOR_ENDPOINT=data/inbox/shanghai_listings.csv
SHR_COLLECTOR_AUTH_MODE=NONE
SHR_MARKET_DATA_MODE=live
SHR_COLLECTOR_PARTNER_CSV_MAX_AGE_HOURS=36
```

Required CSV columns are `listing_id`, `url`, `district`, `submarket`, `community`, `price_wan`, and
`area_sqm`. The remaining canonical listing fields are optional. UTF-8 and UTF-8 with BOM are
accepted. Empty values remain unknown; the importer does not guess them.

The companion file is named by appending `.manifest.json`, for example
`shanghai_listings.csv.manifest.json`. A complete manifest has this shape:

```json
{
  "manifest_version": "1.0",
  "source_id": "partner_csv",
  "provider": "Example Broker",
  "license_reference": "contract-2026-01",
  "exported_at": "2026-09-04T01:00:00+08:00",
  "scope": {"city": "shanghai", "filters": {}},
  "completeness": "complete",
  "coverage": {"reported_total": 500, "items_returned": 500},
  "file_name": "shanghai_listings.csv",
  "file_sha256": "<sha256 of the exact CSV bytes>"
}
```

Create the reviewed manifest without inventing the provider total:

```bash
python scripts/prepare_partner_csv_manifest.py \
  --csv data/inbox/shanghai_listings.csv \
  --provider "Example Broker" \
  --license-reference "contract-2026-01" \
  --exported-at "2026-09-04T01:00:00+08:00" \
  --declare-complete \
  --reported-total 500
```

Before the first LIVE collection, run the fail-closed readiness gate from the repository root:

```bash
python scripts/live_readiness.py
python scripts/live_readiness.py --probe-source
```

The first command performs static checks and cannot return `READY` without a source probe. The
second command fetches and normalizes the configured source without creating a crawl run or writing
to the database. Enable unattended collection only when it exits 0 with
`ready_for_unattended_live: true`.

`--reported-total` must come from the provider's export control total and must equal the CSV rows.
Without `--declare-complete`, the tool writes a partial manifest. Without any manifest, the adapter
also ingests as partial and never reconciles missing listings.

Before signing the manifest, the tool validates every row through the same canonical field rules
used by ingestion. It rejects invalid prices, areas, URLs, statuses, timestamps and coordinate
pairs; duplicate listing IDs or URLs; credential-shaped columns; and observations later than the
declared export time. A file that would become partial at 02:00 therefore cannot be signed as a
complete export during handoff.

For an atomic handoff, write the CSV to a temporary name, rename it to the configured `.csv` path,
then write and rename the manifest last. The adapter reads the manifest before and after the CSV and
verifies the exact file hash. A mixed or changing pair fails the run. The Compose `api`, collector
worker, and `daily-runner` mount `data/inbox` read-only.

The export timestamp is also a freshness boundary. A manifest older than
`SHR_COLLECTOR_PARTNER_CSV_MAX_AGE_HOURS` is downgraded to partial. This keeps an unchanged old file
from producing a false healthy daily scan or reconciling listings as missing.

## Provider onboarding checklist

A provider cannot declare complete until all of these are supplied and reviewed:

- API or export documentation;
- written authorization to use and retain the data;
- a sanitized representative response;
- the stable listing identifier definition;
- pagination behavior and continuation rules;
- maximum page, row, and result limits;
- exact city, district, submarket, query, and filter semantics;
- update frequency and source timestamps;
- authentication method and secret-delivery process;
- field definitions, units, null behavior, and status vocabulary;
- deletion, expiry, sale, and unlisting behavior;
- reported-total definition and consistency guarantees;
- rate limits, retry guidance, and service windows;
- capability profile values;
- evidence that credentials are absent from response bodies and exports;
- complete, partial, empty, malformed, authorization, and retry fixtures.

The operator should first run the provider as partial and compare returned counts with its manifest.
Only documented and tested full-coverage behavior may be promoted to complete.
