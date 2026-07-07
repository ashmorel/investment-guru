import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import Money from "../components/Money";
import { apiFetch } from "../lib/api";
import type { DashboardData } from "../lib/types";

export default function DashboardPage() {
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardData>("/api/dashboard"),
  });

  if (dash.isPending) return <p className="text-muted">Loading…</p>;
  if (dash.isError) return <p className="text-loss">Failed to load dashboard.</p>;
  const { portfolios, as_of } = dash.data;

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-text">Dashboard</h1>
        <p className="text-xs text-muted">as of {new Date(as_of).toLocaleString()}</p>
      </div>
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
