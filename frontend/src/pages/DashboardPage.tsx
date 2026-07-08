import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import AttentionPanel from "../components/AttentionPanel";
import Money from "../components/Money";
import { apiFetch } from "../lib/api";
import type { DashboardData } from "../lib/types";

export default function DashboardPage() {
  const qc = useQueryClient();
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardData>("/api/dashboard"),
  });

  const runAnalysis = useMutation({
    mutationFn: async () => {
      const data = await apiFetch<DashboardData>("/api/dashboard");
      await Promise.all(
        data.portfolios.map((p) =>
          apiFetch(`/api/portfolios/${p.id}/analyze`, { method: "POST" }),
        ),
      );
    },
    onSuccess: () => {
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
      <AttentionPanel />
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
      <div className="rounded-xl border border-border bg-surface p-5 shadow">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-sm text-indigo-500">
            ✦
          </span>
          <h2 className="font-medium text-text">Guru's take</h2>
        </div>
        <p className="mt-2 text-sm text-muted">
          Portfolio commentary, key risks and rebalance ideas arrive with Phase 2.
        </p>
      </div>
    </div>
  );
}
