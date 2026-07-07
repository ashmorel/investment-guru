import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../lib/api";
import type { Portfolio } from "../lib/types";

interface PreviewRow {
  symbol: string;
  quantity: string | null;
  purchase_price: string | null;
  comment: string | null;
  known: boolean;
}

interface CommitResult {
  created: number;
  updated: number;
  skipped: number;
  portfolio_id: number;
}

export default function ImportWizardPage() {
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<PreviewRow[] | null>(null);
  const [target, setTarget] = useState<string>("new");
  const [newName, setNewName] = useState("Imported");
  const [newKind, setNewKind] = useState<"real" | "watchlist">("real");
  const [newCcy, setNewCcy] = useState("GBP");
  const [merge, setMerge] = useState<"update" | "skip" | "replace">("update");
  const [result, setResult] = useState<CommitResult | null>(null);

  const portfolios = useQuery({
    queryKey: ["portfolios"],
    queryFn: () => apiFetch<Portfolio[]>("/api/portfolios"),
  });

  const preview = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", file!);
      const resp = await fetch("/api/imports/preview", {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!resp.ok) throw new Error(await resp.text());
      return (await resp.json()) as { rows: PreviewRow[] };
    },
    onSuccess: (data) => setRows(data.rows),
  });

  const commit = useMutation({
    mutationFn: () =>
      apiFetch<CommitResult>("/api/imports/commit", {
        method: "POST",
        body: JSON.stringify({
          portfolio_id: target === "new" ? null : Number(target),
          new_portfolio:
            target === "new"
              ? { name: newName, kind: newKind, base_currency: newCcy }
              : null,
          merge,
          rows: (rows ?? [])
            .filter((r) => r.known)
            .map((r) => ({ symbol: r.symbol, quantity: r.quantity, avg_cost: r.purchase_price })),
        }),
      }),
    onSuccess: setResult,
  });

  if (result) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold text-text">Import complete</h1>
        <p className="text-muted">
          Created {result.created}, updated {result.updated}, skipped {result.skipped}.
        </p>
        <Link to={`/portfolios/${result.portfolio_id}`} className="text-accent underline">
          View portfolio
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Import from Yahoo Finance</h1>

      <section className="space-y-3 rounded-xl bg-surface p-4 shadow">
        <h2 className="font-medium text-text">1. Upload</h2>
        <label className="block text-sm text-muted">
          CSV file
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block"
          />
        </label>
        <button
          onClick={() => preview.mutate()}
          disabled={!file || preview.isPending}
          className="rounded-md bg-accent px-4 py-2 text-white disabled:opacity-50"
        >
          Upload & preview
        </button>
        {preview.isError && (
          <p className="text-sm text-loss">Could not parse that file as a Yahoo export.</p>
        )}
      </section>

      {rows && (
        <section className="space-y-3 rounded-xl bg-surface p-4 shadow">
          <h2 className="font-medium text-text">2. Review & assign</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted">
                <th className="p-2">Symbol</th>
                <th className="p-2 text-right">Quantity</th>
                <th className="p-2 text-right">Purchase price</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className={r.known ? "" : "bg-[#FEECEC]"}>
                  <td className="p-2 font-medium">{r.symbol}</td>
                  <td className="p-2 text-right tabular-nums">{r.quantity ?? "—"}</td>
                  <td className="p-2 text-right tabular-nums">{r.purchase_price ?? "—"}</td>
                  <td className="p-2">
                    {r.known ? "OK" : <span className="text-loss">not recognised — will be excluded</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm text-muted">
              Target portfolio
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="mt-1 block rounded-md border border-border px-3 py-2"
              >
                <option value="new">Create new…</option>
                {(portfolios.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.kind})
                  </option>
                ))}
              </select>
            </label>
            {target === "new" && (
              <>
                <label className="text-sm text-muted">
                  Name
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    className="mt-1 block rounded-md border border-border px-3 py-2"
                  />
                </label>
                <label className="text-sm text-muted">
                  Type
                  <select
                    value={newKind}
                    onChange={(e) => setNewKind(e.target.value as "real" | "watchlist")}
                    className="mt-1 block rounded-md border border-border px-3 py-2"
                  >
                    <option value="real">Real</option>
                    <option value="watchlist">Watchlist</option>
                  </select>
                </label>
                <label className="text-sm text-muted">
                  Base currency
                  <select
                    value={newCcy}
                    onChange={(e) => setNewCcy(e.target.value)}
                    className="mt-1 block rounded-md border border-border px-3 py-2"
                  >
                    <option>GBP</option>
                    <option>USD</option>
                    <option>HKD</option>
                  </select>
                </label>
              </>
            )}
            <fieldset className="text-sm text-muted">
              <legend>If a symbol already exists</legend>
              {(["update", "skip", "replace"] as const).map((m) => (
                <label key={m} className="mr-3">
                  <input
                    type="radio"
                    name="merge"
                    checked={merge === m}
                    onChange={() => setMerge(m)}
                    className="mr-1"
                  />
                  {m}
                </label>
              ))}
            </fieldset>
            <button
              onClick={() => commit.mutate()}
              disabled={commit.isPending}
              className="rounded-md bg-accent px-4 py-2 text-white disabled:opacity-50"
            >
              3. Import {rows.filter((r) => r.known).length} rows
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
