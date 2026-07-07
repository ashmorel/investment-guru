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
