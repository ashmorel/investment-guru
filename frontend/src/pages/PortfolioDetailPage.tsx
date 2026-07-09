import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Money from "../components/Money";
import SignalBadges from "../components/SignalBadges";
import VerdictChip from "../components/VerdictChip";
import { apiFetch, ApiError } from "../lib/api";
import type {
  AnalyzeResponse,
  GuruReport,
  PortfolioValuation,
  Position,
  ReviewPayload,
  Signal,
  SignalsResponse,
} from "../lib/types";

interface ReviewsResponse {
  reviews: GuruReport<ReviewPayload>[];
}

// Only the position-conflict (409) response has a detail worth surfacing
// verbatim — lookup failures (404) keep the friendlier fallback copy below.
function conflictDetail(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  try {
    const body = JSON.parse(err.message) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

export default function PortfolioDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const valuation = useQuery({
    queryKey: ["valuation", id],
    queryFn: () => apiFetch<PortfolioValuation>(`/api/portfolios/${id}/valuation`),
  });
  const signals = useQuery({
    queryKey: ["signals", id],
    queryFn: () => apiFetch<SignalsResponse>(`/api/portfolios/${id}/signals`),
  });
  const guruReviews = useQuery({
    queryKey: ["guru", "reviews", Number(id)],
    queryFn: () => apiFetch<ReviewsResponse>(`/api/guru/reviews?portfolio_id=${id}&limit=1`),
    retry: false,
  });
  const latestReview = guruReviews.data?.reviews[0] ?? null;
  // 404 (no reviews yet) collapses into the ordinary "no take" empty state;
  // any other query error (network/5xx) is a distinct fetch failure.
  const guruReviewsUnavailable =
    guruReviews.isError && !(guruReviews.error instanceof ApiError && guruReviews.error.status === 404);

  const [unavailable, setUnavailable] = useState<string[]>([]);
  const runAnalysis = useMutation({
    mutationFn: () =>
      apiFetch<AnalyzeResponse>(`/api/portfolios/${id}/analyze`, { method: "POST" }),
    onSuccess: (res) => {
      setUnavailable(res.unavailable_inputs);
      qc.invalidateQueries({ queryKey: ["signals", id] });
      qc.invalidateQueries({ queryKey: ["valuation", id] });
      qc.invalidateQueries({ queryKey: ["attention"] });
    },
  });

  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  const addPosition = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/instruments/lookup?symbol=${encodeURIComponent(symbol)}`);
      return apiFetch<Position>(`/api/portfolios/${id}/positions`, {
        method: "POST",
        body: JSON.stringify({
          symbol,
          quantity: quantity || null,
          avg_cost: avgCost || null,
        }),
      });
    },
    onSuccess: () => {
      setSymbol("");
      setQuantity("");
      setAvgCost("");
      setAddError(null);
      qc.invalidateQueries({ queryKey: ["valuation", id] });
    },
    onError: (err) => {
      const detail = conflictDetail(err);
      setAddError(detail ?? `Could not add ${symbol} — symbol not recognised.`);
    },
  });

  const removePosition = useMutation({
    mutationFn: (positionId: number) =>
      apiFetch<void>(`/api/positions/${positionId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["valuation", id] }),
  });

  if (valuation.isPending) return <p className="text-muted">Loading…</p>;
  if (valuation.isError) return <p className="text-loss">Failed to load portfolio.</p>;
  const v = valuation.data;

  const bySymbol: Record<string, Signal[]> = {};
  for (const s of signals.data?.signals ?? []) {
    if (!s.symbol) continue;
    (bySymbol[s.symbol] ??= []).push(s);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-text">Portfolio</h1>
        <div className="flex items-start gap-4">
          <div className="text-right">
            <p className="text-2xl font-semibold">
              <Money value={v.total_value} ccy={v.base_currency} />
            </p>
            <p className="text-sm">
              Day: <Money value={v.day_change} ccy={v.base_currency} signed />
              {" · "}P&L: <Money value={v.total_pnl} ccy={v.base_currency} signed /> (
              <Money value={v.total_pnl_pct} signed />
              %)
            </p>
          </div>
          <button
            onClick={() => runAnalysis.mutate()}
            disabled={runAnalysis.isPending}
            className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {runAnalysis.isPending ? "Analyzing…" : "Run analysis"}
          </button>
        </div>
      </div>
      {unavailable.length > 0 && (
        <p className="rounded-md bg-[#FFFBEB] p-3 text-sm text-flag">
          Some data was unavailable: {unavailable.join(", ")}. Signals may be incomplete.
        </p>
      )}
      {runAnalysis.isError && (
        <p className="rounded-md bg-loss/10 p-3 text-sm text-loss">
          Analysis failed — provider may be down. Try again.
        </p>
      )}
      {v.unpriced_positions > 0 && (
        <p className="rounded-md bg-[#FFFBEB] p-3 text-sm text-flag">
          {v.unpriced_positions} position(s) missing live prices — values may be incomplete.
        </p>
      )}
      <table className="w-full rounded-xl bg-surface text-sm shadow">
        <thead>
          <tr className="border-b border-border text-left text-muted">
            <th className="p-3">Symbol</th>
            <th className="p-3">Name</th>
            <th className="p-3 text-right">Qty</th>
            <th className="p-3 text-right">Price</th>
            <th className="p-3 text-right">Value ({v.base_currency})</th>
            <th className="p-3 text-right">Day</th>
            <th className="p-3 text-right">P&L</th>
            <th className="p-3">Signals</th>
            <th className="p-3">Guru's take</th>
            <th className="p-3" />
          </tr>
        </thead>
        <tbody>
          {v.positions.map((p) => (
            <tr key={p.position_id} className="border-b border-border">
              <td className="p-3 font-medium">{p.symbol}</td>
              <td className="p-3 text-muted">{p.name}</td>
              <td className="p-3 text-right tabular-nums">{p.quantity ?? "—"}</td>
              <td className="p-3 text-right">
                <Money value={p.price} ccy={p.native_currency} />
              </td>
              <td className="p-3 text-right"><Money value={p.market_value_base} /></td>
              <td className="p-3 text-right"><Money value={p.day_change_base} signed /></td>
              <td className="p-3 text-right"><Money value={p.unrealized_pnl_base} signed /></td>
              <td className="p-3">
                <SignalBadges signals={bySymbol[p.symbol] ?? []} />
              </td>
              <td className="p-3">
                {(() => {
                  // A 404 (nothing generated yet) reads as the same "no take"
                  // empty state as an empty review list; any other query error
                  // (e.g. 500) is a distinct fetch failure worth surfacing on
                  // its own, without blocking the rest of the table.
                  if (guruReviewsUnavailable) {
                    return <span className="text-xs text-muted">Guru take unavailable</span>;
                  }
                  const verdict = latestReview?.payload.positions.find(
                    (pv) => pv.symbol === p.symbol,
                  );
                  if (!latestReview || !verdict) {
                    return (
                      <span className="text-xs text-muted">No take yet — run a review.</span>
                    );
                  }
                  return (
                    <div className="space-y-1">
                      <VerdictChip action={verdict.action} conviction={verdict.conviction} />
                      <p
                        className="max-w-[28rem] text-xs text-muted line-clamp-2"
                        title={verdict.rationale}
                      >
                        {verdict.rationale}
                      </p>
                      <p className="text-xs text-muted">
                        Generated {new Date(latestReview.created_at).toLocaleString()}
                      </p>
                      <Link
                        to={`/guru?discuss=${encodeURIComponent(JSON.stringify(verdict))}`}
                        className="text-xs text-accent underline"
                      >
                        Ask in chat
                      </Link>
                    </div>
                  );
                })()}
              </td>
              <td className="p-3 text-right">
                <button
                  onClick={() => removePosition.mutate(p.position_id)}
                  className="text-xs text-loss"
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          addPosition.mutate();
        }}
        className="flex flex-wrap items-end gap-3 rounded-xl bg-surface p-4 shadow"
      >
        <label className="text-sm text-text">
          Symbol
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="AAPL, HSBA.L, 0700.HK"
            className="mt-1 block rounded-md border border-border px-3 py-2"
            required
          />
        </label>
        <label className="text-sm text-text">
          Quantity
          <input
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className="mt-1 block rounded-md border border-border px-3 py-2"
            inputMode="decimal"
          />
        </label>
        <label className="text-sm text-text">
          Avg cost (native ccy)
          <input
            value={avgCost}
            onChange={(e) => setAvgCost(e.target.value)}
            className="mt-1 block rounded-md border border-border px-3 py-2"
            inputMode="decimal"
          />
        </label>
        <button type="submit" className="rounded-md bg-accent px-4 py-2 text-white">
          Add position
        </button>
        {addError && <p className="text-sm text-loss">{addError}</p>}
      </form>
    </div>
  );
}
