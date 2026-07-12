import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import AttentionPanel from "../components/AttentionPanel";
import GuruTakePanel from "../components/GuruTakePanel";
import Money from "../components/Money";
import NewsPanel from "../components/NewsPanel";
import { apiFetch } from "../lib/api";
import type { AnalyzeResponse, DashboardData } from "../lib/types";

export default function DashboardPage() {
  const qc = useQueryClient();
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardData>("/api/dashboard"),
  });

  const [unavailable, setUnavailable] = useState<string[]>([]);
  const runAnalysis = useMutation({
    mutationFn: async () => {
      const portfolios = dash.data?.portfolios ?? [];
      const results = await Promise.all(
        portfolios.map((p) =>
          apiFetch<AnalyzeResponse>(`/api/portfolios/${p.id}/analyze`, { method: "POST" }),
        ),
      );
      // Union of unavailable inputs across every analyzed portfolio.
      return [...new Set(results.flatMap((r) => r.unavailable_inputs))];
    },
    onSuccess: (union) => {
      setUnavailable(union);
      qc.invalidateQueries({ queryKey: ["attention"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  if (dash.isPending) return <p className="text-muted">Loading…</p>;
  if (dash.isError) return <p className="text-loss">Failed to load dashboard.</p>;
  const { portfolios, as_of } = dash.data;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-text">Dashboard</h1>
        <div className="flex items-center gap-3">
          <p className="text-xs text-muted">as of {new Date(as_of).toLocaleString()}</p>
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
      {portfolios.length === 0 ? (
        <p className="rounded-xl bg-surface p-6 text-muted shadow">
          No portfolios yet. <Link to="/portfolios" className="text-accent underline">Create one</Link>{" "}
          or <Link to="/import" className="text-accent underline">import a Yahoo CSV</Link>.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {portfolios.map((p) => (
            <Link
              key={p.id}
              to={`/portfolios/${p.id}`}
              className="rounded-xl bg-surface p-5 shadow transition hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <h2 className="font-medium text-text">{p.name}</h2>
                <span className="text-xs uppercase tracking-wide text-muted">{p.kind}</span>
              </div>
              <p className="mt-3 text-2xl font-semibold text-text">
                <Money value={p.total_value} ccy={p.base_currency} />
              </p>
              <p className="mt-1 text-sm text-muted">
                Day <Money value={p.day_change} ccy={p.base_currency} signed /> · P&L{" "}
                <Money value={p.total_pnl_pct} signed />%
              </p>
            </Link>
          ))}
        </div>
      )}
      <GuruTakePanel />
      <AttentionPanel />
      <NewsPanel />
    </div>
  );
}
