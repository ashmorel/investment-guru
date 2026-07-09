export interface Portfolio {
  id: number;
  name: string;
  kind: "real" | "watchlist";
  base_currency: string;
  position_count: number;
}

export interface Position {
  id: number;
  symbol: string;
  name: string;
  market: string;
  currency: string;
  quantity: string | null;
  avg_cost: string | null;
  notes: string | null;
}

export interface PositionValuation {
  position_id: number;
  symbol: string;
  name: string;
  market: string;
  quantity: string | null;
  avg_cost: string | null;
  native_currency: string;
  price: string | null;
  market_value_base: string | null;
  cost_basis_base: string | null;
  unrealized_pnl_base: string | null;
  unrealized_pnl_pct: string | null;
  day_change_base: string | null;
  quote_as_of: string | null;
  currency_mismatch?: boolean;
}

export interface PortfolioValuation {
  portfolio_id: number;
  base_currency: string;
  total_value: string | null;
  total_cost: string | null;
  total_pnl: string | null;
  total_pnl_pct: string | null;
  day_change: string | null;
  currency_exposure: Record<string, string>;
  priced_positions: number;
  unpriced_positions: number;
  costed_positions?: number;
  day_change_partial?: boolean;
  positions: PositionValuation[];
}

export interface DashboardData {
  portfolios: Array<{
    id: number;
    name: string;
    kind: string;
    base_currency: string;
    total_value: string | null;
    day_change: string | null;
    total_pnl_pct: string | null;
  }>;
  as_of: string;
}

export interface Signal {
  id: number;
  instrument_id: number | null;
  symbol: string | null;
  kind: string;
  severity: "info" | "watch" | "high";
  title: string;
  detail: string;
  data: Record<string, string>;
  computed_at: string;
}

export interface AttentionSignal extends Signal {
  portfolio_id: number;
  portfolio_name: string;
}

export interface AttentionResponse {
  signals: AttentionSignal[];
}

export interface SignalsResponse {
  signals: Signal[];
  computed_at: string | null;
}

export interface AnalyzeResponse {
  signals: Signal[];
  as_of: string;
  unavailable_inputs: string[];
}

export type GuruAction = "hold" | "increase" | "reduce" | "exit";
export type Conviction = "low" | "med" | "high";
export interface PositionVerdict { symbol: string; action: GuruAction; conviction: Conviction; rationale: string; }
export interface ReviewPayload { positions: PositionVerdict[]; observations: string[]; watch_next: string[]; disclaimer: string; }
export interface DigestPayload { earnings_this_week: { symbol: string; date: string | null; note: string }[]; movers: { symbol: string; note: string }[]; news_flags: { symbol: string | null; headline: string; comment: string }[]; summary: string; disclaimer: string; }
export interface TakePayload { commentary: string; risks: { kind: string; note: string }[]; ideas: { symbol: string | null; action: GuruAction; conviction: Conviction; rationale: string }[]; disclaimer: string; }
export interface GuruReport<P = unknown> { id: number; kind: "review" | "digest" | "take"; portfolio_id: number | null; payload: P; model: string; created_at: string; }
export interface InvestorProfile { risk_appetite: "cautious" | "balanced" | "adventurous"; horizon: "short" | "medium" | "long"; sector_interests: string[]; free_text: string; }
export interface UsageSummary { by_mode: { mode: string; calls: number; input_tokens: number; output_tokens: number; est_cost_usd: string | null }[]; total_cost_30d: string | null; }
export interface ChatThread { id: number; title: string; portfolio_id: number | null; created_at: string; }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; created_at: string; }
