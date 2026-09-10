# Shanghai Home Radar Product Requirements

## Document status

- Product version: V0.1
- Initial delivery: P0 foundation and P1 collector skeleton
- Intended deployment: private, long-term personal use
- Initial market: Shanghai second-hand residential property

## Product goal

Shanghai Home Radar is a decision system for a home buyer. It turns a large, changing set of listings into a small, explainable set of actions.

The product must answer four questions:

1. Is the property currently undervalued?
2. What is a realistic executable transaction price?
3. Is the property likely to remain a high-quality residential asset over the next 3–10 years?
4. Should the buyer contact, inspect, negotiate, or ignore it?

The desired steady-state outcome is fewer than 10 serious candidates and ideally 1–3 inspection-worthy properties per day.

## Target search profile

| Dimension | Initial target |
| --- | --- |
| Total price | RMB 2.3M–3.3M |
| Core budget | About RMB 3M |
| Geography | Outer Xuhui, northern Minhang, Putuo, Yangpu, and mature Pudong submarkets |
| Title | Normal residential title preferred |
| Layout | 1–2 bedrooms or compact 3 bedrooms |
| Access | Metro-accessible |
| Market quality | Strong resale liquidity |
| Surroundings | Mature employment and living infrastructure |

## Primary user

The initial user is a private buyer who operates the system, reviews its evidence, and makes every consequential decision. The system supports judgment. It does not make purchases or binding commitments.

## Product principles

- Find quality at a discount, not merely cheap property.
- Keep current value, future quality, liquidity, and obsolescence separate until the final decision stage.
- Treat asking price, fair value, broker-indicated price, seller expectation, and executable price as different facts.
- Preserve every historical observation and raw source record for auditability.
- Make valuation and forecasting deterministic and explainable before considering more complex models.
- Keep every source adapter replaceable. No decision engine may depend on a crawler-specific payload.
- Treat planned infrastructure according to its probability of completion.
- Label forecasts as probabilistic model outputs, not guaranteed prices.
- Require a human for inspections, formal monetary offers, binding commitments, signing, and payment.

## V0.1 delivery scope

### P0 foundation

P0 delivers the product and architecture documents, database schema, Docker Compose environment, configuration model, README, and development environment.

P0 is accepted when a developer can configure and start the local dependencies, apply migrations, run the API, and run the tests using documented commands.

### P1 collector

P1 delivers:

- a typed, source-neutral collector interface;
- one Shanghai property source adapter prototype;
- reliable collection into PostgreSQL;
- immutable listing snapshots;
- raw source payload storage for debugging and audit;
- retry with backoff and structured logging;
- sample seed data and unit tests.

P1 is accepted when one configured source can be collected repeatedly without duplicating canonical listings or overwriting prior observations. A failed record must not invalidate a successful collection run.

The prototype must use only authorized access paths. It must not bypass CAPTCHA, authentication, access controls, or source restrictions.

### Explicitly outside P0 and P1

The first pass does not claim production readiness for:

- cross-source entity resolution;
- calibrated disappearance and relisting decisions beyond complete-feed inference;
- market baselines;
- fair-value estimates or score calibration;
- future scenarios;
- dashboard and notifications;
- automated broker inquiry;
- seller intelligence or negotiation support.

The P0/P1 architecture and data contracts must leave room for these capabilities without coupling them to the first source.

## Required future capabilities

### Historical listing intelligence

The system must eventually detect new listings, price reductions, price increases, long listing duration, temporary disappearance, relisting, and final disappearance. Detection must be derived from immutable observations rather than destructive updates.

### Market baseline

Baselines must support this hierarchy:

```text
Shanghai
→ district
→ submarket
→ community
→ area bucket
→ layout
```

Each available level should expose price quartiles, median prices, listing supply, recent price-cut behavior, days on market, and liquidity measures. Sparse levels must fall back to a broader level and disclose that fallback.

### Fair value and executable price

Fair value must use comparable properties in this order: same community, similar floor area, similar layout, similar building age, and similar floor characteristics. Adjustments may account for floor, orientation, elevator, building age, metro distance, layout quality, road or noise exposure, liquidity, and market trend.

Each result must include:

- fair value, low estimate, and high estimate;
- comparable count and confidence;
- selected comparables;
- explicit adjustments and their effects.

Executable price must update after inquiries and remain separate from asking price and fair value.

### Decision scores

The system must preserve these outputs:

- Value Score;
- Future Score;
- Liquidity Score;
- Obsolescence Risk;
- Buy Score.

Value Score drives the initial action band:

| Score | Action |
| --- | --- |
| Below 65 | PASS |
| 65–74 | WATCH |
| 75–84 | CONTACT |
| 85–89 | VIEW |
| 90 or above | ATTACK |

All weights and thresholds must live in versioned configuration rather than application code.

### Future quality and scenarios

The future engine must distinguish tactical, medium-term, and long-term drivers. It must produce 1-year, 3-year, and 5-year bear, base, and bull scenarios, plus CAGR range, downside risk, relative outperformance probabilities, liquidity outlook, and obsolescence outlook.

### Conditional inquiry

Inquiry automation begins only after a listing meets its configured score threshold. It must be low-frequency and pause for human intervention when authentication or CAPTCHA appears. The system stores both raw replies and structured interpretations.

## Core user journeys

### Collect and preserve

1. A scheduled or manual collection run starts for one enabled source.
2. The adapter fetches permitted source records.
3. The collector validates and maps each record to the canonical contract.
4. The ingestion pipeline resolves the source listing identity.
5. It stores the raw record and appends a listing snapshot.
6. It updates only the listing's current index fields and collection metadata.
7. The run reports successes, failures, retry counts, and timestamps.

### Review an opportunity

In later phases, the buyer opens a candidate and sees its ask history, fair-value range, executable-price evidence, comparable properties, scores, future outlook, seller intelligence, and inquiry history. Every derived result links to its inputs and model version.

### Make a decision

The system recommends PASS, WATCH, CONTACT, VIEW, or ATTACK. The buyer approves any communication that can create a real-world commitment.

## Dashboard requirements

The eventual home screen shows the configured budget, scanned count, new listings, price drops, and counts by actionable decision band. Each property card shows location, physical attributes, metro access, current and historical asks, fair value, executable price, all five decision measures, and the action label.

Detailed views must expose price history, comparable evidence, valuation explanation, future outlook, seller intelligence, and inquiry history.

## Privacy and safety requirements

- Run as a private system with no public registration in V0.1.
- Keep source credentials, broker conversations, and seller information out of source control.
- Collect only information required for the stated property decision workflow.
- Restrict raw payload and inquiry access to the operator.
- Log identifiers and outcomes, but do not emit secrets or full private conversations into operational logs.
- Keep data retention and deletion operations explicit and operator-controlled.
- Do not bypass CAPTCHA or technical access controls.
- Do not send aggressive or mass messages.
- Do not make irreversible external actions.

## Quality requirements

- Typed boundaries for adapters, ingestion, API input, and API output.
- Database changes through Alembic migrations.
- Idempotent ingestion for safe retries.
- Immutable historical observations.
- Structured logging with collection-run correlation.
- Bounded retries with backoff and visible terminal failures.
- Unit tests for mapping, validation, idempotency, and snapshot preservation.
- GitHub Actions must run the basic test suite.
- Docker Compose must support local development. Kubernetes is not part of V0.

## P0/P1 success measures

- One source adapter completes an end-to-end collection into PostgreSQL.
- Reprocessing the same source record does not create a second canonical listing.
- A new observation never changes an existing snapshot.
- Raw input remains available for an audit of every ingested observation.
- Collector failures include enough context to retry or diagnose without exposing secrets.
- Local setup and test commands work from a clean checkout with documented prerequisites.

## Known initial limitations

- One prototype source does not establish complete Shanghai market coverage.
- Asking-price observations are not transaction records.
- Missing or inaccurate source fields reduce later valuation confidence.
- P0/P1 can infer absence from a declared complete feed, but it does not prove a sale or estimate executable price.
- Forecast and score specifications define contracts and guardrails; calibration requires later market data and backtesting.
