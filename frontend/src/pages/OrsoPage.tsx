import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Money from "../components/Money";
import VerdictChip from "../components/VerdictChip";
import { ApiError, apiFetch, isBudgetExhausted } from "../lib/api";
import type {
  ChatThread,
  GuruReport,
  OrsoAdvicePayload,
  OrsoGoals,
  OrsoOverview,
  OrsoSwitchLogEntry,
} from "../lib/types";

interface AdviceListResponse {
  reports: GuruReport<OrsoAdvicePayload>[];
}

interface SwitchLogResponse {
  entries: OrsoSwitchLogEntry[];
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

// --- Current allocation -----------------------------------------------------

function AllocationCard({ overview }: { overview: OrsoOverview }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<Record<number, { units: string; contribution_pct: string }>>({});
  const [note, setNote] = useState("");
  const [priceEdit, setPriceEdit] = useState<{ fundId: number; price: string; asOf: string } | null>(
    null,
  );
  const [refreshUnavailable, setRefreshUnavailable] = useState(false);

  function startEdit() {
    const init: Record<number, { units: string; contribution_pct: string }> = {};
    for (const f of overview.funds) {
      init[f.id] = { units: f.units ?? "0", contribution_pct: f.contribution_pct ?? "0" };
    }
    setRows(init);
    setNote("");
    setEditing(true);
  }

  const invalidateAfterChange = () => {
    qc.invalidateQueries({ queryKey: ["orso", "overview"] });
    qc.invalidateQueries({ queryKey: ["orso", "switchlog"] });
  };

  const saveAllocation = useMutation({
    mutationFn: () =>
      apiFetch("/api/orso/allocation", {
        method: "PUT",
        body: JSON.stringify({
          allocations: overview.funds.map((f) => ({
            fund_id: f.id,
            units: rows[f.id]?.units ?? f.units ?? "0",
            contribution_pct: rows[f.id]?.contribution_pct ?? f.contribution_pct ?? "0",
          })),
          note: note.trim() ? note.trim() : undefined,
        }),
      }),
    onSuccess: () => {
      invalidateAfterChange();
      setEditing(false);
    },
  });

  const savePrice = useMutation({
    mutationFn: (input: { fund_id: number; price: string; as_of: string }) =>
      apiFetch("/api/orso/prices/manual", { method: "PUT", body: JSON.stringify(input) }),
    onSuccess: () => {
      invalidateAfterChange();
      setPriceEdit(null);
    },
  });

  const refresh = useMutation({
    mutationFn: () =>
      apiFetch<{ refreshed: number[]; unavailable: boolean }>("/api/orso/prices/refresh", {
        method: "POST",
      }),
    onSuccess: (res) => {
      if (res.unavailable) setRefreshUnavailable(true);
      qc.invalidateQueries({ queryKey: ["orso", "overview"] });
      qc.invalidateQueries({ queryKey: ["orso", "switchlog"] });
    },
  });

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-start justify-between">
        <h2 className="font-medium text-text">Current allocation</h2>
        <div className="text-right">
          <button
            type="button"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending || refreshUnavailable}
            className="text-sm text-muted underline disabled:opacity-50"
          >
            {refresh.isPending ? "Refreshing…" : "⟳ Refresh prices"}
          </button>
          {refreshUnavailable && (
            <p className="mt-1 text-xs text-muted">Manual prices only — the fetcher is unavailable.</p>
          )}
        </div>
      </div>

      <p className="mt-3 text-3xl font-semibold text-text">
        <Money value={overview.total_hkd} ccy="HKD" />
      </p>
      <p className="mt-1 text-sm text-muted">
        {overview.total_base ? (
          <>
            ≈ <Money value={overview.total_base.value} ccy={overview.total_base.currency} />
          </>
        ) : (
          "≈ —"
        )}
        {" · "}
        <button type="button" onClick={startEdit} className="text-accent underline">
          edit allocation
        </button>
      </p>

      {overview.flags.split_sum_off && (
        <p className="mt-3 rounded-md bg-[#FFFBEB] p-2 text-xs text-flag">
          The contribution split does not add up to 100%.
        </p>
      )}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="p-2 font-medium">Code</th>
              <th className="p-2 font-medium">Fund</th>
              <th className="p-2 font-medium">Risk</th>
              <th className="p-2 text-right font-medium">Units</th>
              <th className="p-2 text-right font-medium">Price (as of)</th>
              <th className="p-2 text-right font-medium">Value HKD</th>
              <th className="p-2 text-right font-medium">Split</th>
            </tr>
          </thead>
          <tbody>
            {overview.funds.map((f) => {
              const stale = overview.flags.stale.includes(f.code);
              return (
                <tr key={f.id} className="border-t border-border">
                  <td className="p-2 font-medium text-text">{f.code}</td>
                  <td className="p-2 text-muted">{f.name}</td>
                  <td className="p-2 text-text">{f.risk_rating}</td>
                  <td className="p-2 text-right tabular-nums">
                    {editing && !f.archived ? (
                      <input
                        aria-label={`${f.code} units`}
                        value={rows[f.id]?.units ?? ""}
                        onChange={(e) =>
                          setRows((r) => ({ ...r, [f.id]: { ...r[f.id], units: e.target.value } }))
                        }
                        className="w-24 rounded-md border border-border px-2 py-1 text-right text-sm text-text"
                      />
                    ) : (
                      (f.units ?? "0")
                    )}
                  </td>
                  <td className="p-2 text-right">
                    <button
                      type="button"
                      onClick={() =>
                        setPriceEdit({
                          fundId: f.id,
                          price: f.price ?? "",
                          asOf: f.price_as_of ?? todayIso(),
                        })
                      }
                      className="text-right text-text underline decoration-dotted"
                    >
                      {f.price === null ? (
                        <span className="text-xs text-accent">Enter price</span>
                      ) : (
                        <>
                          <Money value={f.price} />{" "}
                          <span className="text-xs text-muted">(as of {f.price_as_of})</span>
                          {stale && <span className="ml-1 text-xs text-flag">⚠ stale</span>}
                        </>
                      )}
                    </button>
                  </td>
                  <td className="p-2 text-right">
                    <Money value={f.value_hkd} />
                  </td>
                  <td className="p-2 text-right tabular-nums">
                    {editing && !f.archived ? (
                      <input
                        aria-label={`${f.code} split`}
                        value={rows[f.id]?.contribution_pct ?? ""}
                        onChange={(e) =>
                          setRows((r) => ({
                            ...r,
                            [f.id]: { ...r[f.id], contribution_pct: e.target.value },
                          }))
                        }
                        className="w-16 rounded-md border border-border px-2 py-1 text-right text-sm text-text"
                      />
                    ) : (
                      `${f.contribution_pct ?? "0"}%`
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted">Tap the price to enter one manually.</p>

      {priceEdit && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            savePrice.mutate({ fund_id: priceEdit.fundId, price: priceEdit.price, as_of: priceEdit.asOf });
          }}
          className="mt-3 flex flex-wrap items-end gap-2 rounded-md bg-bg p-3"
        >
          <label className="text-xs text-muted" htmlFor="manual-price">
            Price
            <input
              id="manual-price"
              value={priceEdit.price}
              onChange={(e) => setPriceEdit({ ...priceEdit, price: e.target.value })}
              inputMode="decimal"
              className="mt-1 block w-28 rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="text-xs text-muted" htmlFor="manual-price-as-of">
            As of
            <input
              id="manual-price-as-of"
              type="date"
              value={priceEdit.asOf}
              onChange={(e) => setPriceEdit({ ...priceEdit, asOf: e.target.value })}
              className="mt-1 block rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <button
            type="submit"
            disabled={savePrice.isPending}
            className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {savePrice.isPending ? "Saving…" : "Save price"}
          </button>
          <button
            type="button"
            onClick={() => setPriceEdit(null)}
            className="text-sm text-muted underline"
          >
            Cancel
          </button>
        </form>
      )}

      {editing && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            saveAllocation.mutate();
          }}
          className="mt-4 space-y-3 rounded-md bg-bg p-3"
        >
          <label className="block text-xs text-muted" htmlFor="allocation-note">
            Note (optional)
            <input
              id="allocation-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={saveAllocation.isPending}
              className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {saveAllocation.isPending ? "Saving…" : "Save allocation"}
            </button>
            <button type="button" onClick={() => setEditing(false)} className="text-sm text-muted underline">
              Cancel
            </button>
            {saveAllocation.isError && (
              <p className="text-sm text-loss">Could not save — please try again.</p>
            )}
          </div>
        </form>
      )}
    </section>
  );
}

// --- Switching advice ---------------------------------------------------------

function AdviceCard() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const adviceList = useQuery({
    queryKey: ["orso", "advice"],
    queryFn: () => apiFetch<AdviceListResponse>("/api/orso/advice?limit=20"),
  });

  const generate = useMutation({
    mutationFn: () =>
      apiFetch<GuruReport<OrsoAdvicePayload>>("/api/orso/advice", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orso", "advice"] }),
  });

  const discuss = useMutation({
    mutationFn: (payload: OrsoAdvicePayload) =>
      apiFetch<ChatThread>("/api/guru/chat/threads", {
        method: "POST",
        body: JSON.stringify({
          title: "ORSO switching advice",
          scope: "orso",
          seed_context: payload,
        }),
      }),
    onSuccess: () => navigate("/guru"),
  });

  const unconfigured =
    generate.isError && generate.error instanceof ApiError && generate.error.status === 503;
  const alreadyGenerating =
    generate.isError && generate.error instanceof ApiError && generate.error.status === 409;
  const budgetExhausted = generate.isError && isBudgetExhausted(generate.error);

  const reports = adviceList.data?.reports ?? [];
  const latest = reports[0] ?? null;
  const older = reports.slice(1);

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-sm text-accent">
            ✦
          </span>
          <h2 className="font-medium text-text">Switching advice</h2>
        </div>
        <button
          type="button"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {generate.isPending ? "Generating…" : "Get switching advice"}
        </button>
      </div>

      {unconfigured && (
        <p className="mt-3 rounded-md bg-accent-subtle p-3 text-sm text-accent">
          The Guru isn't configured yet — an administrator needs to add an LLM API key before
          reports can be generated.
        </p>
      )}
      {alreadyGenerating && (
        <p className="mt-3 text-sm text-flag">Already generating — check back shortly.</p>
      )}
      {budgetExhausted && (
        <p className="mt-3 rounded-md bg-accent-subtle p-3 text-sm text-accent">
          Daily AI limit reached — resets tomorrow.
        </p>
      )}

      {adviceList.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {adviceList.isError && (
        <p className="mt-3 text-sm text-loss">Failed to load switching advice.</p>
      )}
      {adviceList.isSuccess && !latest && (
        <p className="mt-3 text-sm text-muted">No advice yet — get switching advice.</p>
      )}

      {latest && (
        <div className="mt-4 space-y-3">
          <ul className="space-y-2">
            {latest.payload.fund_verdicts.map((v, i) => (
              <li key={v.code + "-" + i} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium text-text">{v.code}</span>
                <VerdictChip action={v.action} conviction={v.conviction} />
                <span className="text-muted">{v.rationale}</span>
              </li>
            ))}
          </ul>

          {latest.payload.switch_plan.length > 0 && (
            <div className="rounded-lg bg-bg p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted">Switch plan</p>
              <ul className="mt-2 space-y-1 text-sm text-text">
                {latest.payload.switch_plan.map((s, i) => (
                  <li key={(s.from_code ?? "—") + "-" + (s.to_code ?? "—") + "-" + i}>
                    {s.from_code ?? "—"} → {s.to_code ?? "—"}: <span className="text-muted">{s.note}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-sm text-accent">{latest.payload.projection_comment}</p>

          {latest.payload.watch.length > 0 && (
            <ul className="space-y-1 text-sm text-muted">
              {latest.payload.watch.map((w, i) => (
                <li key={w + "-" + i}>• {w}</li>
              ))}
            </ul>
          )}

          <button
            type="button"
            onClick={() => discuss.mutate(latest.payload)}
            disabled={discuss.isPending}
            className="text-xs text-accent underline disabled:opacity-50"
          >
            Discuss in chat →
          </button>

          <p className="text-xs text-muted">
            Generated {new Date(latest.created_at).toLocaleString()}
            {older.length > 0 && (
              <> · older: {older.map((r) => new Date(r.created_at).toLocaleDateString()).join(", ")}</>
            )}
          </p>
          <p className="text-xs text-muted">{latest.payload.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

// --- Retirement goals + projection -------------------------------------------

interface GoalsForm {
  birth_year: string;
  retirement_target_age: string;
  retirement_target_pot: string;
  orso_monthly_contribution: string;
}

function toForm(g: OrsoGoals): GoalsForm {
  return {
    birth_year: g.birth_year === null ? "" : String(g.birth_year),
    retirement_target_age: g.retirement_target_age === null ? "" : String(g.retirement_target_age),
    retirement_target_pot: g.retirement_target_pot ?? "",
    orso_monthly_contribution: g.orso_monthly_contribution ?? "",
  };
}

function ProjectionBars({ overview }: { overview: OrsoOverview }) {
  if (overview.flags.goals_incomplete || !overview.projection) {
    return <p className="mt-2 text-sm text-muted">Set your retirement goals to see a projection.</p>;
  }
  const max = Math.max(...overview.projection.map((s) => Number(s.projected_pot)));
  return (
    <div className="mt-3 space-y-3">
      {overview.projection.map((s) => {
        const pct = max > 0 ? Math.max(4, (Number(s.projected_pot) / max) * 100) : 4;
        return (
          <div key={s.rate}>
            <div className="flex items-center justify-between text-xs text-muted">
              <span>{Number(s.rate) * 100}%</span>
              <span>
                <Money value={s.projected_pot} ccy="HKD" />
              </span>
            </div>
            <div className="mt-1 h-2 rounded-full bg-bg">
              <div
                className={`h-2 rounded-full ${
                  s.on_track === true ? "bg-gain" : s.on_track === false ? "bg-loss" : "bg-accent"
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
            {s.on_track !== null && s.gap !== null && (
              <p className={`mt-1 text-xs ${s.on_track ? "text-gain" : "text-loss"}`}>
                {s.on_track
                  ? `+${Math.abs(Number(s.gap)).toLocaleString()} ✓ on track`
                  : `−${Math.abs(Number(s.gap)).toLocaleString()} short`}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GoalsCard({ overview }: { overview: OrsoOverview }) {
  const qc = useQueryClient();
  const goals = useQuery({
    queryKey: ["orso", "goals"],
    queryFn: () => apiFetch<OrsoGoals>("/api/orso/goals"),
  });
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<GoalsForm | null>(null);

  useEffect(() => {
    if (goals.data && form === null) setForm(toForm(goals.data));
  }, [goals.data, form]);

  function startEdit() {
    if (goals.data) {
      setForm(toForm(goals.data));
      setEditing(true);
    }
  }

  const save = useMutation({
    mutationFn: (body: GoalsForm) =>
      apiFetch<OrsoGoals>("/api/orso/goals", {
        method: "PUT",
        body: JSON.stringify({
          birth_year: body.birth_year ? Number(body.birth_year) : null,
          retirement_target_age: body.retirement_target_age ? Number(body.retirement_target_age) : null,
          retirement_target_pot: body.retirement_target_pot || null,
          orso_monthly_contribution: body.orso_monthly_contribution || null,
        }),
      }),
    onSuccess: (data) => {
      setForm(toForm(data));
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["orso", "goals"] });
      qc.invalidateQueries({ queryKey: ["orso", "overview"] });
    },
  });

  const targetAge = goals.data?.retirement_target_age ?? null;

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-center justify-between">
        <h2 className="font-medium text-text">Retirement goals</h2>
        <button
          type="button"
          onClick={startEdit}
          className="text-sm text-accent underline"
        >
          Edit goals ✎
        </button>
      </div>

      {goals.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {goals.isError && <p className="mt-3 text-sm text-loss">Failed to load goals.</p>}

      {!editing && form && (
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted">Born</dt>
            <dd className="text-text">{goals.data?.birth_year ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted">Target age</dt>
            <dd className="text-text">{goals.data?.retirement_target_age ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted">Target pot</dt>
            <dd className="text-text">
              <Money value={goals.data?.retirement_target_pot ?? null} ccy="HKD" />
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted">Monthly</dt>
            <dd className="text-text">
              <Money value={goals.data?.orso_monthly_contribution ?? null} ccy="HKD" />
            </dd>
          </div>
        </dl>
      )}

      {editing && form && (
        <form
          className="mt-4 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(form);
          }}
        >
          <label className="block text-xs text-muted" htmlFor="goal-birth-year">
            Born
            <input
              id="goal-birth-year"
              inputMode="numeric"
              value={form.birth_year}
              onChange={(e) => setForm({ ...form, birth_year: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="block text-xs text-muted" htmlFor="goal-target-age">
            Target age
            <input
              id="goal-target-age"
              inputMode="numeric"
              value={form.retirement_target_age}
              onChange={(e) => setForm({ ...form, retirement_target_age: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="block text-xs text-muted" htmlFor="goal-target-pot">
            Target pot (HKD)
            <input
              id="goal-target-pot"
              inputMode="decimal"
              value={form.retirement_target_pot}
              onChange={(e) => setForm({ ...form, retirement_target_pot: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="block text-xs text-muted" htmlFor="goal-monthly">
            Monthly contribution (HKD)
            <input
              id="goal-monthly"
              inputMode="decimal"
              value={form.orso_monthly_contribution}
              onChange={(e) => setForm({ ...form, orso_monthly_contribution: e.target.value })}
              className="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm text-text"
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save goals"}
            </button>
            <button
              type="button"
              onClick={() => {
                if (goals.data) setForm(toForm(goals.data));
                setEditing(false);
              }}
              className="text-sm text-muted underline"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="mt-5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          Projected pot at {targetAge ?? "target age"}
        </p>
        <ProjectionBars overview={overview} />
      </div>
    </section>
  );
}

// --- Switch log ---------------------------------------------------------------

function SwitchLogCard() {
  const log = useQuery({
    queryKey: ["orso", "switchlog"],
    queryFn: () => apiFetch<SwitchLogResponse>("/api/orso/switchlog"),
  });
  const entries = log.data?.entries ?? [];

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <h2 className="font-medium text-text">Switch log</h2>
      {log.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {log.isError && <p className="mt-3 text-sm text-loss">Failed to load the switch log.</p>}
      {log.isSuccess && entries.length === 0 && (
        <p className="mt-3 text-sm text-muted">No changes yet.</p>
      )}
      {entries.length > 0 && (
        <ul className="mt-3 space-y-2 text-sm">
          {entries.map((e) => (
            <li key={e.id}>
              <span className="text-muted">{new Date(e.changed_at).toLocaleString()}</span>
              {e.note && <span className="ml-2 text-text">{e.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- Page ---------------------------------------------------------------------

export default function OrsoPage() {
  const overview = useQuery({
    queryKey: ["orso", "overview"],
    queryFn: () => apiFetch<OrsoOverview>("/api/orso/overview"),
  });

  if (overview.isPending) return <p className="text-muted">Loading…</p>;
  if (overview.isError) return <p className="text-loss">Failed to load ORSO overview.</p>;
  const data = overview.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-text">ORSO pension</h1>
        <p className="text-sm text-muted">
          HSBC ORSO (WMFS) · prices as of {new Date(data.as_of).toLocaleDateString()}
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_420px]">
        <div className="space-y-6">
          <AllocationCard overview={data} />
          <AdviceCard />
        </div>
        <div className="space-y-6">
          <GoalsCard overview={data} />
          <SwitchLogCard />
        </div>
      </div>
    </div>
  );
}
