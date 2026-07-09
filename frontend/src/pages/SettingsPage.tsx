import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../lib/api";
import type { InvestorProfile, UsageSummary } from "../lib/types";

const RISK_OPTIONS: { value: InvestorProfile["risk_appetite"]; label: string }[] = [
  { value: "cautious", label: "Cautious" },
  { value: "balanced", label: "Balanced" },
  { value: "adventurous", label: "Adventurous" },
];

const HORIZON_OPTIONS: { value: InvestorProfile["horizon"]; label: string }[] = [
  { value: "short", label: "Short (<2 years)" },
  { value: "medium", label: "Medium (2–5 years)" },
  { value: "long", label: "Long (5+ years)" },
];

function formatUsd(value: string | null): string {
  const n = value === null ? 0 : Number(value);
  return `$${n.toFixed(2)}`;
}

export default function SettingsPage() {
  const qc = useQueryClient();
  const profile = useQuery({
    queryKey: ["guru", "profile"],
    queryFn: () => apiFetch<InvestorProfile>("/api/guru/profile"),
  });
  const usage = useQuery({
    queryKey: ["guru", "usage"],
    queryFn: () => apiFetch<UsageSummary>("/api/guru/usage/summary"),
  });

  const [form, setForm] = useState<InvestorProfile | null>(null);
  const [newSector, setNewSector] = useState("");

  useEffect(() => {
    if (profile.data && form === null) setForm(profile.data);
  }, [profile.data, form]);

  const save = useMutation({
    mutationFn: (body: InvestorProfile) =>
      apiFetch<InvestorProfile>("/api/guru/profile", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => {
      setForm(data);
      qc.invalidateQueries({ queryKey: ["guru", "profile"] });
    },
  });

  if (profile.isError) return <p className="text-loss">Failed to load settings.</p>;
  if (usage.isError) return <p className="text-loss">Failed to load usage.</p>;
  if (!form || usage.isPending) return <p className="text-muted">Loading…</p>;

  const usageData = usage.data;

  function addSector() {
    const value = newSector.trim();
    if (!form) return;
    if (value && !form.sector_interests.includes(value)) {
      setForm({ ...form, sector_interests: [...form.sector_interests, value] });
    }
    setNewSector("");
  }

  function removeSector(sector: string) {
    if (!form) return;
    setForm({ ...form, sector_interests: form.sector_interests.filter((s) => s !== sector) });
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Settings</h1>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-border bg-surface p-5 shadow">
          <h2 className="font-medium text-text">Investor profile</h2>
          <p className="mt-1 text-sm text-muted">
            The Guru calibrates every recommendation to this profile.
          </p>

          <form
            className="mt-5 space-y-5"
            onSubmit={(e) => {
              e.preventDefault();
              if (form) save.mutate(form);
            }}
          >
            <fieldset>
              <legend
                id="risk-appetite-label"
                className="text-xs font-medium uppercase tracking-wide text-muted"
              >
                Risk appetite
              </legend>
              <div
                role="radiogroup"
                aria-labelledby="risk-appetite-label"
                className="mt-2 inline-flex rounded-lg bg-bg p-1"
              >
                {RISK_OPTIONS.map((opt) => (
                  <label
                    key={opt.value}
                    className={`cursor-pointer rounded-md px-4 py-2 text-sm ${
                      form.risk_appetite === opt.value
                        ? "bg-surface text-text shadow"
                        : "text-muted"
                    }`}
                  >
                    <input
                      type="radio"
                      name="risk_appetite"
                      value={opt.value}
                      checked={form.risk_appetite === opt.value}
                      onChange={() => setForm({ ...form, risk_appetite: opt.value })}
                      className="sr-only"
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="block" htmlFor="horizon">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">
                Horizon
              </span>
              <select
                id="horizon"
                value={form.horizon}
                onChange={(e) =>
                  setForm({ ...form, horizon: e.target.value as InvestorProfile["horizon"] })
                }
                className="mt-2 block rounded-md border border-border px-3 py-2 text-sm text-text"
              >
                {HORIZON_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted">
                Sector interests
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {form.sector_interests.map((s) => (
                  <button
                    key={s}
                    type="button"
                    aria-label={`Remove ${s}`}
                    onClick={() => removeSector(s)}
                    className="flex items-center gap-1 rounded-full bg-accent-subtle px-3 py-1 text-sm text-accent"
                  >
                    <span>{s}</span>
                    <span aria-hidden="true">✕</span>
                  </button>
                ))}
                <input
                  value={newSector}
                  onChange={(e) => setNewSector(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addSector();
                    }
                  }}
                  aria-label="Add sector interest"
                  placeholder="Add sector"
                  className="w-32 rounded-full border border-border px-3 py-1 text-sm text-text"
                />
                <button
                  type="button"
                  onClick={addSector}
                  className="rounded-full border border-border px-3 py-1 text-sm text-muted"
                >
                  + Add
                </button>
              </div>
            </div>

            <label className="block" htmlFor="free_text">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">
                Anything else the Guru should know
              </span>
              <textarea
                id="free_text"
                value={form.free_text}
                onChange={(e) => setForm({ ...form, free_text: e.target.value })}
                rows={4}
                className="mt-2 block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
              />
            </label>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={save.isPending}
                className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {save.isPending ? "Saving…" : "Save profile"}
              </button>
              {save.isSuccess && <p className="text-sm text-gain">Saved just now</p>}
              {save.isError && (
                <p className="text-sm text-loss">Could not save — please try again.</p>
              )}
            </div>
          </form>
        </section>

        <section className="rounded-xl border border-border bg-surface p-5 shadow">
          <h2 className="font-medium text-text">Guru usage</h2>
          <p className="mt-1 text-xs uppercase tracking-wide text-muted">
            Estimated API spend · last 30 days
          </p>
          <p className="mt-2 text-3xl font-semibold tabular-nums text-text">
            {formatUsd(usageData.total_cost_30d)}
          </p>

          <table className="mt-5 w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="p-2 font-medium">Mode</th>
                <th className="p-2 text-right font-medium">Calls</th>
                <th className="p-2 text-right font-medium">Tokens in</th>
                <th className="p-2 text-right font-medium">Tokens out</th>
                <th className="p-2 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {usageData.by_mode.map((m) => (
                <tr key={m.mode} className="border-t border-border">
                  <td className="p-2 capitalize">{m.mode}</td>
                  <td className="p-2 text-right tabular-nums">{m.calls}</td>
                  <td className="p-2 text-right tabular-nums">{m.input_tokens}</td>
                  <td className="p-2 text-right tabular-nums">{m.output_tokens}</td>
                  <td className="p-2 text-right tabular-nums">{formatUsd(m.est_cost_usd)}</td>
                </tr>
              ))}
              {usageData.by_mode.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-2 text-muted">
                    No usage yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          <p className="mt-4 text-xs text-muted">
            Every LLM call is logged with model + tokens. No hard caps — visibility only.
          </p>
        </section>
      </div>
    </div>
  );
}
