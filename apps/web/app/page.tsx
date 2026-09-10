import { candidates, districtPulse, type Candidate, type Decision } from "@/data/dashboard";

function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10h11M11 6l4 4-4 4" />
    </svg>
  );
}

function Sparkline({ points }: { points: number[] }) {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const coordinates = points
    .map((point, index) => {
      const x = 3 + (index / (points.length - 1)) * 104;
      const y = 28 - ((point - min) / range) * 22;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg className="sparkline" viewBox="0 0 110 32" role="img" aria-label="Asking price history">
      <path d="M3 28H107" className="sparkline-baseline" />
      <polyline points={coordinates} />
      <circle cx="107" cy="28" r="2.5" />
    </svg>
  );
}

function Score({ label, value, risk = false }: { label: string; value: number; risk?: boolean }) {
  return (
    <div className="score">
      <span>{label}</span>
      <strong className={risk ? "risk" : value >= 85 ? "excellent" : ""}>{value}</strong>
      <span className="score-track" aria-hidden="true">
        <span className={risk ? "risk-bar" : ""} style={{ width: `${value}%` }} />
      </span>
    </div>
  );
}

function DecisionBadge({ decision }: { decision: Decision }) {
  return <span className={`decision decision-${decision.toLowerCase()}`}>{decision}</span>;
}

function Price({ value }: { value: number }) {
  return <>{`¥${value.toFixed(2)}M`}</>;
}

function CandidateCard({ candidate, rank }: { candidate: Candidate; rank: number }) {
  return (
    <article className="candidate-card">
      <div className="candidate-rank">0{rank}</div>
      <div className="candidate-body">
        <header className="candidate-header">
          <div>
            <div className="candidate-eyebrow">
              <DecisionBadge decision={candidate.decision} />
              <span>{candidate.id}</span>
            </div>
            <h3>{candidate.community}</h3>
            <p>{candidate.location}</p>
          </div>
          <button className="icon-button" aria-label={`Open ${candidate.community} details`}>
            <ArrowIcon />
          </button>
        </header>

        <div className="property-facts">
          <span><strong>{candidate.area}</strong> ㎡</span>
          <span><strong>{candidate.layout}</strong></span>
          <span><strong>{candidate.floor}</strong></span>
          <span><strong>{candidate.metroDistance}m</strong> · {candidate.metro}</span>
        </div>

        <div className="price-panel">
          <div className="price-main">
            <span>Current ask</span>
            <strong><Price value={candidate.ask} /></strong>
            <em>↓ {candidate.priceDrop}% from <Price value={candidate.previousAsk} /></em>
          </div>
          <div>
            <span>Fair value</span>
            <strong><Price value={candidate.fairValue} /></strong>
          </div>
          <div>
            <span>Executable</span>
            <strong>¥{candidate.executableLow.toFixed(2)}–{candidate.executableHigh.toFixed(2)}M</strong>
          </div>
          <div className="history-cell">
            <span>{candidate.daysOnMarket} days listed</span>
            <Sparkline points={candidate.history} />
          </div>
        </div>

        <div className="card-lower">
          <div className="scores">
            <Score label="Value" value={candidate.valueScore} />
            <Score label="Future" value={candidate.futureScore} />
            <Score label="Buy" value={candidate.buyScore} />
            <Score label="Obsolescence" value={candidate.obsolescenceRisk} risk />
          </div>
          <div className="decision-notes">
            <strong>{candidate.decisionNote}</strong>
            <ul>
              {candidate.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function DashboardPage() {
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Shanghai Home Radar home">
          <LogoMark />
          <span>Shanghai Home Radar<small>Private buyer intelligence</small></span>
        </a>
        <nav aria-label="Primary navigation">
          <a className="active" href="#radar">Radar</a>
          <a href="#market">Market</a>
          <a href="#collector">Collector</a>
        </nav>
        <div className="sync-status demo-status"><span /> Demo dataset</div>
      </header>

      <div className="demo-banner" role="note" aria-label="Demo data notice">
        <strong>DEMO · SAMPLE DATA</strong>
        <span>All property, pricing, scoring, collector, and market figures below are illustrative.</span>
      </div>

      <section className="hero" id="top">
        <div>
          <p className="section-label">Tuesday, 1 September · Morning brief</p>
          <h1>Three homes are<br /><em>worth your attention.</em></h1>
          <p className="hero-copy">One price reset moved into the attack zone overnight. The rest of the market can wait.</p>
        </div>
        <div className="budget-card">
          <span>Search mandate</span>
          <strong>¥2.3M <i>to</i> ¥3.3M</strong>
          <p>1–2 bedrooms · Metro accessible · High resale liquidity</p>
          <button>Adjust criteria <ArrowIcon /></button>
        </div>
      </section>

      <section className="metric-grid" aria-label="Daily market summary">
        <div><span>Scanned</span><strong>3,126</strong><small>across 5 districts</small></div>
        <div><span>New today</span><strong>108</strong><small className="positive">+14 vs 7-day avg</small></div>
        <div><span>Price drops</span><strong>57</strong><small>21 meaningful</small></div>
        <div className="action-metric"><span>Contact</span><strong>8</strong><small>Awaiting 3 replies</small></div>
        <div className="action-metric"><span>View</span><strong>3</strong><small>2 newly promoted</small></div>
        <div className="attack-metric"><span>Attack</span><strong>1</strong><small>Move today</small></div>
      </section>

      <div className="dashboard-grid" id="radar">
        <section className="candidate-list">
          <div className="section-heading">
            <div>
              <p className="section-label">Today&apos;s priority queue</p>
              <h2>Quality at a discount</h2>
            </div>
            <div className="filters" aria-label="Candidate filters">
              <button className="selected">Top picks <span>3</span></button>
              <button>All candidates <span>24</span></button>
            </div>
          </div>
          {candidates.map((candidate, index) => (
            <CandidateCard candidate={candidate} rank={index + 1} key={candidate.id} />
          ))}
        </section>

        <aside>
          <section className="aside-card" id="market">
            <div className="aside-heading">
              <div><p className="section-label">Market pulse</p><h2>Target districts</h2></div>
              <span>7D</span>
            </div>
            <div className="district-table">
              {districtPulse.map((district) => (
                <div className="district-row" key={district.district}>
                  <div><strong>{district.district}</strong><span>{district.listings} active</span></div>
                  <div><strong>{district.median}</strong><span>median / ㎡</span></div>
                  <div className={district.trend > 0 ? "trend-up" : "trend-down"}>
                    {district.trend > 0 ? "+" : ""}{district.trend}%
                  </div>
                </div>
              ))}
            </div>
            <p className="market-note"><span>Signal</span> Negotiation spreads are widening fastest in northern Minhang.</p>
          </section>

          <section className="aside-card thesis-card">
            <p className="section-label">Radar thesis</p>
            <blockquote>Buy resilient housing quality only when the seller gives you the margin of safety.</blockquote>
            <div className="quadrant">
              <div><span>WATCH</span><small>Good, expensive</small></div>
              <div className="target"><span>TARGET</span><small>Quality + discount</small></div>
              <div><span>PASS</span><small>Weak, expensive</small></div>
              <div><span>TRAP</span><small>Cheap for a reason</small></div>
            </div>
          </section>

          <section className="aside-card collector-card" id="collector">
            <div className="collector-top"><p className="section-label">Collector health</p><span>Healthy</span></div>
            <h3>Last run completed</h3>
            <p>3,126 listings · 08:37 · 4m 12s</p>
            <div className="source-row"><span><i className="source-icon">源</i> Prototype source</span><strong>100%</strong></div>
            <div className="source-row"><span><i className="source-icon muted">队</i> Ingestion queue</span><strong>0 pending</strong></div>
          </section>
        </aside>
      </div>

      <footer>
        <span>Shanghai Home Radar · V0.1</span>
        <span>Demo sample data · Probabilistic signals, not investment advice</span>
      </footer>
    </main>
  );
}
