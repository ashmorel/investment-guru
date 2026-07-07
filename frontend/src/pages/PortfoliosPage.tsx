import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { Portfolio } from "../lib/types";

export default function PortfoliosPage() {
  const qc = useQueryClient();
  const portfolios = useQuery({
    queryKey: ["portfolios"],
    queryFn: () => apiFetch<Portfolio[]>("/api/portfolios"),
  });
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"real" | "watchlist">("real");
  const [ccy, setCcy] = useState("GBP");

  const create = useMutation({
    mutationFn: () =>
      apiFetch<Portfolio>("/api/portfolios", {
        method: "POST",
        body: JSON.stringify({ name, kind, base_currency: ccy }),
      }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["portfolios"] });
    },
  });

  if (portfolios.isPending) return <p className="text-muted">Loading…</p>;
  if (portfolios.isError) return <p className="text-loss">Failed to load portfolios.</p>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Portfolios</h1>
      <ul className="divide-y divide-border rounded-xl bg-surface shadow">
        {portfolios.data.map((p) => (
          <li key={p.id} className="flex items-center justify-between p-4">
            <Link to={`/portfolios/${p.id}`} className="font-medium text-text">
              {p.name}
            </Link>
            <span className="text-sm text-muted">
              {p.kind} · {p.position_count} positions · {p.base_currency}
            </span>
          </li>
        ))}
        {portfolios.data.length === 0 && (
          <li className="p-4 text-muted">No portfolios yet — create one below.</li>
        )}
      </ul>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="flex flex-wrap items-end gap-3 rounded-xl bg-surface p-4 shadow"
      >
        <label className="text-sm text-text">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 block rounded-md border border-border px-3 py-2"
            required
          />
        </label>
        <label className="text-sm text-text">
          Type
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as "real" | "watchlist")}
            className="mt-1 block rounded-md border border-border px-3 py-2"
          >
            <option value="real">Real</option>
            <option value="watchlist">Watchlist</option>
          </select>
        </label>
        <label className="text-sm text-text">
          Base currency
          <select
            value={ccy}
            onChange={(e) => setCcy(e.target.value)}
            className="mt-1 block rounded-md border border-border px-3 py-2"
          >
            <option>GBP</option>
            <option>USD</option>
            <option>HKD</option>
          </select>
        </label>
        <button type="submit" className="rounded-md bg-accent px-4 py-2 text-white">
          Create
        </button>
      </form>
    </div>
  );
}
