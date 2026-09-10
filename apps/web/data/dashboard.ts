export type Decision = "ATTACK" | "VIEW" | "CONTACT" | "WATCH" | "PASS";

export type Candidate = {
  id: string;
  community: string;
  location: string;
  decision: Decision;
  decisionNote: string;
  area: number;
  layout: string;
  floor: string;
  metro: string;
  metroDistance: number;
  ask: number;
  previousAsk: number;
  fairValue: number;
  executableLow: number;
  executableHigh: number;
  priceDrop: number;
  daysOnMarket: number;
  valueScore: number;
  futureScore: number;
  buyScore: number;
  obsolescenceRisk: number;
  reasons: string[];
  history: number[];
};

export const candidates: Candidate[] = [
  {
    id: "SHR-0942",
    community: "高兴花园",
    location: "闵行 · 春申",
    decision: "ATTACK",
    decisionNote: "Price reset creates a rare margin of safety",
    area: 74.2,
    layout: "2室 2厅",
    floor: "中楼层 / 6F",
    metro: "15号线 景西路",
    metroDistance: 680,
    ask: 3.08,
    previousAsk: 3.28,
    fairValue: 3.31,
    executableLow: 2.96,
    executableHigh: 3.01,
    priceDrop: 6.1,
    daysOnMarket: 143,
    valueScore: 93,
    futureScore: 82,
    buyScore: 91,
    obsolescenceRisk: 24,
    reasons: ["Two price cuts in 21 days", "Deep buyer pool near ¥3M", "Seller is replacing home"],
    history: [3.38, 3.38, 3.32, 3.28, 3.28, 3.18, 3.08],
  },
  {
    id: "SHR-1178",
    community: "东安二村",
    location: "徐汇 · 斜土路",
    decision: "VIEW",
    decisionNote: "Scarce central location with manageable total price",
    area: 39.8,
    layout: "1室 1厅",
    floor: "高楼层 / 6F",
    metro: "4/7号线 东安路",
    metroDistance: 420,
    ask: 3.16,
    previousAsk: 3.26,
    fairValue: 3.29,
    executableLow: 3.03,
    executableHigh: 3.09,
    priceDrop: 3.1,
    daysOnMarket: 76,
    valueScore: 86,
    futureScore: 88,
    buyScore: 87,
    obsolescenceRisk: 41,
    reasons: ["Strong employment access", "Low total-price liquidity", "Walk-up building limits upside"],
    history: [3.32, 3.32, 3.28, 3.26, 3.26, 3.2, 3.16],
  },
  {
    id: "SHR-1061",
    community: "中远两湾城",
    location: "普陀 · 宜川",
    decision: "CONTACT",
    decisionNote: "Good liquidity, but executable price needs proof",
    area: 52.6,
    layout: "1室 1厅",
    floor: "中楼层 / 32F",
    metro: "3/4号线 中潭路",
    metroDistance: 510,
    ask: 3.25,
    previousAsk: 3.34,
    fairValue: 3.32,
    executableLow: 3.12,
    executableHigh: 3.18,
    priceDrop: 2.7,
    daysOnMarket: 98,
    valueScore: 79,
    futureScore: 80,
    buyScore: 78,
    obsolescenceRisk: 31,
    reasons: ["High transaction frequency", "Metro access supports resale", "Dense supply caps premium"],
    history: [3.38, 3.38, 3.36, 3.34, 3.34, 3.3, 3.25],
  },
];

export const districtPulse = [
  { district: "闵行", listings: 862, newCount: 31, drops: 18, median: "¥41,200", trend: -1.8 },
  { district: "浦东", listings: 741, newCount: 27, drops: 12, median: "¥53,800", trend: -0.7 },
  { district: "普陀", listings: 598, newCount: 21, drops: 11, median: "¥62,400", trend: -1.1 },
  { district: "杨浦", listings: 527, newCount: 18, drops: 9, median: "¥66,900", trend: -0.4 },
  { district: "徐汇", listings: 398, newCount: 11, drops: 7, median: "¥78,600", trend: 0.2 },
];
