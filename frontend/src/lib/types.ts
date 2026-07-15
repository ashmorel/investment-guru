export interface Me {
  id: number;
  email: string;
  is_admin: boolean;
}

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
export interface GuruReport<P = unknown> { id: number; kind: "review" | "digest" | "take" | "orso" | "news" | "decision"; portfolio_id: number | null; payload: P; model: string; created_at: string; }

export interface HoldingDecision {
  symbol: string;
  action: "hold" | "increase" | "reduce" | "exit" | "data_incomplete";
  conviction: Conviction | null;
  rationale: string;
  evidence_refs: string[];
  change_conditions: string[];
}

export interface DecisionNewsItem {
  evidence_ref: string;
  symbol: string;
  importance: "material" | "watch" | "context";
  headline: string;
  source: string;
  url: string;
  impact: string;
}

export interface CandidateIdea {
  symbol: string;
  name: string;
  instrument_type: "stock" | "etf";
  market: "US" | "UK" | "HK";
  action: "consider";
  conviction: Conviction;
  why_surfaced: string;
  portfolio_fit: string;
  principal_risk: string;
  watch_next: string[];
  evidence_refs: string[];
}

export interface DecisionBriefPayload {
  summary: string;
  holdings: HoldingDecision[];
  material_news: DecisionNewsItem[];
  portfolio_observations: string[];
  candidates: CandidateIdea[];
  unavailable_inputs: string[];
  data_as_of: string;
  disclaimer: string;
}

// --- News (Task 5) -----------------------------------------------------------
// Mirrors backend/app/api/news.py NewsItemOut/NewsGroup/NewsResponse/StockNews
// and backend/app/services/guru/schemas.py NewsSummaryPayload (the payload
// shape of a GuruReport<NewsSummary> returned by the summary endpoints).

export interface NewsItem {
  title: string;
  source: string;
  url: string;
  published_at: string;
}

export interface NewsGroup {
  symbol: string;
  name: string;
  latest_published_at: string | null;
  items: NewsItem[];
  summary_available: boolean;
}

export interface NewsResponse {
  groups: NewsGroup[];
  unavailable: string[];
  as_of: string;
}

export interface StockNews {
  symbol: string;
  name: string;
  items: NewsItem[];
  as_of: string;
}

export interface RefreshNewsResult {
  refreshed: string[];
  unavailable: string[];
}

export interface NewsSummary {
  summary: string;
  sentiment: "positive" | "negative" | "neutral" | "watch";
  key_points: string[];
  disclaimer: string;
}

export interface InvestorProfile { risk_appetite: "cautious" | "balanced" | "adventurous"; horizon: "short" | "medium" | "long"; sector_interests: string[]; free_text: string; digest_enabled: boolean; }
export interface UsageSummary { by_mode: { mode: string; calls: number; input_tokens: number; output_tokens: number; est_cost_usd: string | null }[]; total_cost_30d: string | null; }
export interface ChatThread { id: number; title: string; portfolio_id: number | null; created_at: string; }
export interface ChatMessage { id: number; role: "user" | "assistant"; content: string; created_at: string; }

export interface OrsoFundRow { id: number; code: string; name: string; asset_class: string; risk_rating: number; archived: boolean; units: string | null; contribution_pct: string | null; currency: string; value_native: string | null; value_hkd: string | null; value_display: string | null; price: string | null; price_as_of: string | null; price_source: "hsbc" | "manual" | null; }
export interface OrsoOverview { funds: OrsoFundRow[]; total_hkd: string; total_base: { currency: string; value: string } | null; total_display: string; display_currency: string; projection: { rate: string; projected_pot: string; on_track: boolean | null; gap: string | null }[] | null; flags: { stale: string[]; unpriced: string[]; split_sum_off: boolean; goals_incomplete: boolean; fx_unavailable: string[] }; as_of: string; }
export interface OrsoGoals { birth_year: number | null; retirement_target_age: number | null; retirement_target_pot: string | null; orso_monthly_contribution: string | null; }
export type OrsoAction = "keep" | "increase" | "reduce" | "exit";
export interface OrsoAdvicePayload { fund_verdicts: { code: string; action: OrsoAction; conviction: Conviction; rationale: string }[]; switch_plan: { from_code: string | null; to_code: string | null; note: string }[]; projection_comment: string; watch: string[]; disclaimer: string; }
export interface OrsoSwitchLogEntry { id: number; changed_at: string; note: string | null; }

// --- ORSO ingest (Task 9) ---------------------------------------------------
// Mirrors backend/app/services/orso/ingest.py (AllocationDraft/DraftRow/ProposedFund)
// and backend/app/api/orso.py (ApplyRequest/ApplyItem/ApplyNewFund/FundOut).

export interface ProposedFund {
  code: string;
  name: string;
  currency: string;
  asset_class: string;
  risk_rating: number;
}

export interface DraftRow {
  parsed_code: string;
  parsed_name: string | null;
  matched_fund_id: number | null;
  proposed_fund: ProposedFund | null;
  units: string | null;
  value: string | null;
  currency: string;
  contribution_pct: string | null;
  implied_price: string | null;
  flags: string[];
}

export interface AllocationDraft {
  rows: DraftRow[];
  warnings: string[];
  source: string;
}

export interface ApplyPrice {
  market_value: string;
  as_of: string;
}

export interface ApplyItem {
  fund_id: number | null;
  new_fund_code: string | null;
  units: string;
  contribution_pct: string;
  price: ApplyPrice | null;
}

export interface ApplyNewFund {
  code: string;
  name: string;
  currency: string;
  asset_class?: string;
  risk_rating?: number;
}

export interface ApplyRequest {
  new_funds: ApplyNewFund[];
  allocations: ApplyItem[];
  note?: string | null;
}

export interface ApplyResult {
  created_funds: string[];
  switched: boolean;
}

export interface OrsoFundOut {
  id: number;
  code: string;
  name: string;
  asset_class: string;
  risk_rating: number;
  archived: boolean;
  currency: string;
}

// --- Admin LLM config (Task 8) ----------------------------------------------
// Mirrors backend/app/api/admin.py LlmConfigOut/LlmConfigIn. The API key is
// write-only: the GET response never includes it, only whether one is set.

export interface LlmConfig {
  provider: string;
  advice_model: string;
  scan_model: string;
  advice_input_price: string | null;
  advice_output_price: string | null;
  scan_input_price: string | null;
  scan_output_price: string | null;
  key_set: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface LlmConfigInput {
  provider: string;
  advice_model: string;
  scan_model: string;
  api_key?: string;
  advice_input_price?: string | null;
  advice_output_price?: string | null;
  scan_input_price?: string | null;
  scan_output_price?: string | null;
}

// --- Sector/theme groups (Task 6) --------------------------------------------
// Mirrors backend/app/api/groups.py (GroupOut/SeedOut) and
// backend/app/services/groups/exposure.py (compute_group_exposure's dict
// shape, returned as-is by GET /api/groups/exposure) plus the trend route's
// dict shape in groups.py (GET /api/groups/trend).

export interface HoldingGroup {
  id: number;
  name: string;
  color: string;
  sort_order: number;
  holding_count: number;
}

export interface SeedGroupsResult {
  created: string[];
  assigned: number;
}

export interface AssignResult {
  symbol: string;
  group_id: number | null;
}

export interface GroupHolding {
  symbol: string;
  name: string;
  group_id: number | null;
  group_name: string | null;
}

export interface GroupExposureItem {
  group_id: number | null;
  name: string;
  color: string;
  value_base: string;
  pct: string;
  day_change_base: string;
}

export interface GroupExposure {
  groups: GroupExposureItem[];
  total_base: string;
  unpriced: string[];
  as_of: string;
}

export interface TrendPoint {
  as_of: string;
  value_base: string;
  pct: string;
}

export interface TrendSeries {
  group_id: number | null;
  name: string;
  color: string;
  points: TrendPoint[];
}

export interface GroupTrend {
  series: TrendSeries[];
  as_of: string;
}

export type TrendRange = "30d" | "90d" | "1y";

// --- Sector-rotation advice (Guru) -------------------------------------------
// Mirrors backend/app/services/guru/schemas.py RotationAdvicePayload
// (GroupObservation/Rotation) and the ReportOut shape returned by
// POST/GET /api/groups/rotation in backend/app/api/groups.py.

export interface RotationGroup {
  name: string;
  weight_pct: string;
  observation: string;
  signal: "favour" | "trim" | "hold";
}

export interface RotationItem {
  from_group: string;
  to_group: string;
  rationale: string;
  conviction: "low" | "med" | "high";
}

export interface RotationPayload {
  market_view: string;
  groups: RotationGroup[];
  rotations: RotationItem[];
  caveats: string[];
  disclaimer: string;
}

export interface RotationReport {
  id: number;
  kind: string;
  payload: RotationPayload;
  model: string;
  created_at: string;
}
