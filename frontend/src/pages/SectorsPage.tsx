import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Money from "../components/Money";
import TrendChart from "../components/TrendChart";
import {
  ApiError,
  apiFetch,
  assignGroup,
  createGroup,
  deleteGroup,
  generateRotation,
  getGroupExposure,
  getGroupHoldings,
  getGroups,
  getGroupTrend,
  getRotation,
  isBudgetExhausted,
  seedGroups,
  updateGroup,
} from "../lib/api";
import type { HoldingGroup, Portfolio, RotationItem, TrendRange } from "../lib/types";

// Groups created without an explicit color (e.g. via seed-from-sectors) come
// back from the API with color: "" — fall back to a small default palette by
// list position so every group still reads as a distinct color. The implicit
// Ungrouped bucket (group_id: null) always renders muted grey, regardless of
// what the API sends for its color field. Single helper shared by the
// management list, the exposure bars, and the trend chart.
const DEFAULT_PALETTE = ["#4f46e5", "#0d9488", "#d97706", "#7c3aed", "#db2777", "#0891b2"];
const UNGROUPED_COLOR = "#94a3b8";

// Fallback is deterministic per group identity (not list position), so a
// colorless (e.g. seeded) group renders the SAME swatch in the Manage list,
// the Exposure bars, and the Trend chart — which are each ordered differently.
// The null/Ungrouped bucket is always muted grey.
function resolveColor(groupId: number | null, color: string): string {
  if (groupId === null) return UNGROUPED_COLOR;
  return color || DEFAULT_PALETTE[groupId % DEFAULT_PALETTE.length];
}

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

// --- Group management row ----------------------------------------------------

function GroupRow({
  group,
  onRename,
  onRecolor,
  onDelete,
}: {
  group: HoldingGroup;
  onRename: (id: number, name: string) => void;
  onRecolor: (id: number, color: string) => void;
  onDelete: (id: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(group.name);

  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <span
        aria-hidden="true"
        className="h-3 w-3 shrink-0 rounded-full"
        style={{ backgroundColor: resolveColor(group.id, group.color) }}
      />
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) onRename(group.id, name.trim());
            setEditing(false);
          }}
          className="flex flex-1 flex-wrap items-center gap-2"
        >
          <input
            aria-label={`Rename ${group.name}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="flex-1 rounded-md border border-border px-2 py-1 text-sm text-text"
          />
          <button type="submit" className="text-xs text-accent underline">
            Save
          </button>
          <button
            type="button"
            onClick={() => {
              setName(group.name);
              setEditing(false);
            }}
            className="text-xs text-muted underline"
          >
            Cancel
          </button>
        </form>
      ) : (
        <>
          <span className="flex-1 text-sm text-text">{group.name}</span>
          <span className="text-xs text-muted">
            {group.holding_count} holding{group.holding_count === 1 ? "" : "s"}
          </span>
          <input
            type="color"
            aria-label={`${group.name} color`}
            value={HEX_COLOR.test(group.color) ? group.color : resolveColor(group.id, group.color)}
            onChange={(e) => onRecolor(group.id, e.target.value)}
            className="h-6 w-6 rounded border border-border"
          />
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-xs text-accent underline"
          >
            Rename {group.name}
          </button>
          <button
            type="button"
            onClick={() => onDelete(group.id)}
            className="text-xs text-loss underline"
          >
            Delete {group.name}
          </button>
        </>
      )}
    </li>
  );
}

// --- Guru's rotation view ------------------------------------------------------
// Rotations/observations carry group NAMES (not ids) — map name → id via the
// `groups` query already on the page so the colour dots match the Manage
// list, Exposure bars, and Trend chart. A name with no matching group (i.e.
// "Ungrouped") falls back to the same muted grey via resolveColor(null, "").

const SIGNAL_TONE: Record<"favour" | "trim" | "hold", string> = {
  favour: "bg-gain/10 text-gain",
  trim: "bg-flag/10 text-flag",
  hold: "border border-border bg-bg text-muted",
};

function RotationPanel({ groups }: { groups: HoldingGroup[] }) {
  const qc = useQueryClient();

  const rotationQuery = useQuery({
    queryKey: ["rotation"],
    queryFn: getRotation,
  });

  const generate = useMutation({
    mutationFn: () => generateRotation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rotation"] }),
  });

  const unconfigured =
    generate.isError && generate.error instanceof ApiError && generate.error.status === 503;
  const alreadyGenerating =
    generate.isError && generate.error instanceof ApiError && generate.error.status === 409;
  const budgetExhausted = generate.isError && isBudgetExhausted(generate.error);
  const generateFailed = generate.isError && !unconfigured && !alreadyGenerating && !budgetExhausted;

  const latest = rotationQuery.data ?? null;

  function colorForGroupName(name: string): string {
    const match = groups.find((g) => g.name === name);
    return match ? resolveColor(match.id, match.color) : resolveColor(null, "");
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-sm text-accent">
            ✦
          </span>
          <h2 className="font-medium text-text">Guru's rotation view</h2>
        </div>
        {latest && (
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">
              Generated {new Date(latest.created_at).toLocaleString()}
            </span>
            <button
              type="button"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
              className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {generate.isPending ? "Regenerating…" : "Regenerate"}
            </button>
          </div>
        )}
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
      {generateFailed && (
        <p className="mt-3 text-sm text-loss">Could not generate the rotation view — please try again.</p>
      )}

      {rotationQuery.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {rotationQuery.isError && (
        <p className="mt-3 text-sm text-loss">Failed to load the rotation view.</p>
      )}

      {rotationQuery.isSuccess && !latest && (
        <div className="mt-4">
          <p className="text-sm text-muted">
            Get the Guru's read on where to rotate money next across your groups.
          </p>
          <button
            type="button"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            className="mt-3 rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {generate.isPending ? "Generating…" : "Generate rotation view"}
          </button>
          <p className="mt-3 text-xs text-muted">The Guru is not regulated financial advice.</p>
        </div>
      )}

      {latest && (
        <div className="mt-4 space-y-4">
          <p className="rounded-md bg-accent-subtle p-3 text-sm text-accent">
            {latest.payload.market_view}
          </p>

          <ul className="flex flex-wrap gap-2">
            {latest.payload.groups.map((g) => (
              <li
                key={g.name}
                className="flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs"
              >
                <span
                  aria-hidden="true"
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: colorForGroupName(g.name) }}
                />
                <span className="text-text">{g.name}</span>
                <span className={`rounded-full px-1.5 py-0.5 font-medium ${SIGNAL_TONE[g.signal]}`}>
                  {g.signal.toUpperCase()}
                </span>
              </li>
            ))}
          </ul>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              Suggested rotations
            </p>
            <ul className="mt-2 space-y-2">
              {latest.payload.rotations.map((r: RotationItem, i: number) => (
                <li key={`${r.from_group}-${r.to_group}-${i}`} className="text-sm">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: colorForGroupName(r.from_group) }}
                    />
                    <span className="font-medium text-text">{r.from_group}</span>
                    <span className="text-muted">→</span>
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: colorForGroupName(r.to_group) }}
                    />
                    <span className="font-medium text-text">{r.to_group}</span>
                    <span className="rounded-full border border-border px-1.5 py-0.5 text-xs text-muted">
                      {r.conviction.toUpperCase()}
                    </span>
                  </div>
                  <p className="mt-1 text-muted">{r.rationale}</p>
                </li>
              ))}
            </ul>
          </div>

          {latest.payload.caveats.length > 0 && (
            <ul className="space-y-1 text-xs text-muted">
              {latest.payload.caveats.map((c, i) => (
                <li key={`${c}-${i}`} className="flex items-start gap-1.5">
                  <span aria-hidden="true" className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-flag" />
                  {c}
                </li>
              ))}
            </ul>
          )}

          <p className="text-xs text-muted">{latest.payload.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

// --- Page ---------------------------------------------------------------------

export default function SectorsPage() {
  const qc = useQueryClient();
  const [portfolioFilter, setPortfolioFilter] = useState("");
  const [range, setRange] = useState<TrendRange>("30d");
  const [metric, setMetric] = useState<"value" | "pct">("value");
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupColor, setNewGroupColor] = useState("#4f46e5");

  const groupsQuery = useQuery({ queryKey: ["groups"], queryFn: getGroups });

  const portfoliosQuery = useQuery({
    queryKey: ["portfolios"],
    queryFn: () => apiFetch<Portfolio[]>("/api/portfolios"),
  });
  const realPortfolios = (portfoliosQuery.data ?? []).filter((p) => p.kind === "real");

  const holdingsQuery = useQuery({
    queryKey: ["group-holdings"],
    queryFn: getGroupHoldings,
  });

  const exposureQuery = useQuery({
    queryKey: ["group-exposure", portfolioFilter],
    queryFn: () => getGroupExposure(portfolioFilter === "" ? null : Number(portfolioFilter)),
  });

  const trendQuery = useQuery({
    queryKey: ["group-trend", range],
    queryFn: () => getGroupTrend(range),
  });

  const invalidateGroupsAndExposure = () => {
    qc.invalidateQueries({ queryKey: ["groups"] });
    qc.invalidateQueries({ queryKey: ["group-exposure"] });
    qc.invalidateQueries({ queryKey: ["group-holdings"] });
  };

  const createMutation = useMutation({
    mutationFn: (body: { name: string; color?: string }) => createGroup(body),
    onSuccess: () => {
      invalidateGroupsAndExposure();
      setNewGroupName("");
    },
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => updateGroup(id, { name }),
    onSuccess: invalidateGroupsAndExposure,
  });

  const recolorMutation = useMutation({
    mutationFn: ({ id, color }: { id: number; color: string }) => updateGroup(id, { color }),
    onSuccess: () => {
      invalidateGroupsAndExposure();
      qc.invalidateQueries({ queryKey: ["group-trend"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteGroup(id),
    onSuccess: invalidateGroupsAndExposure,
  });

  const seedMutation = useMutation({
    mutationFn: () => seedGroups(),
    onSuccess: invalidateGroupsAndExposure,
  });

  const assignMutation = useMutation({
    mutationFn: (body: { symbol: string; group_id: number | null }) => assignGroup(body),
    onSuccess: invalidateGroupsAndExposure,
  });

  if (groupsQuery.isPending) return <p className="text-muted">Loading…</p>;
  if (groupsQuery.isError) return <p className="text-loss">Failed to load groups.</p>;
  const groups = groupsQuery.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Sectors</h1>

      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-text">Manage groups</h2>
          <button
            type="button"
            onClick={() => seedMutation.mutate()}
            disabled={seedMutation.isPending}
            className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {seedMutation.isPending ? "Seeding…" : "Seed from sectors"}
          </button>
        </div>
        {seedMutation.isError && (
          <p className="mt-2 text-sm text-loss">Could not seed groups — please try again.</p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Groups</h3>
            {groups.length === 0 ? (
              <p className="mt-2 text-sm text-muted">
                No groups yet — seed from sectors or add one below.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-border">
                {groups.map((g) => (
                  <GroupRow
                    key={g.id}
                    group={g}
                    onRename={(id, name) => renameMutation.mutate({ id, name })}
                    onRecolor={(id, color) => recolorMutation.mutate({ id, color })}
                    onDelete={(id) => deleteMutation.mutate(id)}
                  />
                ))}
              </ul>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (newGroupName.trim()) {
                  createMutation.mutate({ name: newGroupName.trim(), color: newGroupColor });
                }
              }}
              className="mt-4 flex flex-wrap items-end gap-2"
            >
              <label className="text-xs text-muted" htmlFor="new-group-name">
                New group
                <input
                  id="new-group-name"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  className="mt-1 block w-40 rounded-md border border-border px-2 py-1 text-sm text-text"
                  required
                />
              </label>
              <input
                type="color"
                aria-label="New group color"
                value={newGroupColor}
                onChange={(e) => setNewGroupColor(e.target.value)}
                className="h-8 w-8 rounded border border-border"
              />
              <button
                type="submit"
                disabled={createMutation.isPending}
                className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                {createMutation.isPending ? "Adding…" : "Add group"}
              </button>
            </form>
            {createMutation.isError && (
              <p className="mt-2 text-sm text-loss">Could not create the group — the name may already be taken.</p>
            )}
          </div>

          <div>
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Holdings</h3>
            {holdingsQuery.isPending && <p className="mt-2 text-sm text-muted">Loading holdings…</p>}
            {holdingsQuery.isError && <p className="mt-2 text-sm text-loss">Failed to load holdings.</p>}
            {holdingsQuery.data && holdingsQuery.data.length === 0 && (
              <p className="mt-2 text-sm text-muted">No holdings yet — add one in Portfolios.</p>
            )}
            {holdingsQuery.data && holdingsQuery.data.length > 0 && (
              <ul className="mt-2 space-y-2">
                {holdingsQuery.data.map((h) => (
                  <li key={h.symbol} className="flex items-center justify-between gap-2 text-sm">
                    <span>
                      <span className="font-medium text-text">{h.symbol}</span>{" "}
                      <span className="text-muted">{h.name}</span>
                    </span>
                    <select
                      aria-label={`${h.symbol} group`}
                      value={h.group_id ?? ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        assignMutation.mutate({
                          symbol: h.symbol,
                          group_id: val === "" ? null : Number(val),
                        });
                      }}
                      className="rounded-md border border-border px-2 py-1 text-sm text-muted"
                    >
                      <option value="">Ungrouped</option>
                      {groups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <div className="flex items-center justify-between">
          <h2 className="font-medium text-text">Exposure</h2>
          <label className="text-xs text-muted" htmlFor="exposure-portfolio-filter">
            Portfolio
            <select
              id="exposure-portfolio-filter"
              value={portfolioFilter}
              onChange={(e) => setPortfolioFilter(e.target.value)}
              className="mt-1 block rounded-md border border-border px-2 py-1 text-sm text-text"
            >
              <option value="">All portfolios</option>
              {realPortfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        {exposureQuery.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
        {exposureQuery.isError && <p className="mt-3 text-sm text-loss">Failed to load exposure.</p>}
        {exposureQuery.data && exposureQuery.data.groups.length === 0 && (
          <p className="mt-3 text-sm text-muted">No exposure yet — add holdings to a real portfolio.</p>
        )}
        {exposureQuery.data && exposureQuery.data.groups.length > 0 && (
          <div className="mt-4 space-y-3">
            {exposureQuery.data.groups.map((item) => (
              <div key={item.group_id ?? "ungrouped"}>
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="text-text">{item.name}</span>
                  <span className="text-muted">
                    <Money value={item.value_base} ccy="GBP" /> · {item.pct}%{" · "}
                    <Money value={item.day_change_base} ccy="GBP" signed />
                  </span>
                </div>
                <div className="mt-1 h-2 rounded-full bg-bg">
                  <div
                    className="h-2 rounded-full"
                    style={{
                      width: `${Math.min(100, Math.max(0, Number(item.pct)))}%`,
                      backgroundColor: resolveColor(item.group_id, item.color),
                    }}
                  />
                </div>
              </div>
            ))}
            <p className="pt-2 text-sm font-medium text-text">
              Total <Money value={exposureQuery.data.total_base} ccy="GBP" />
            </p>
          </div>
        )}
        {exposureQuery.data && exposureQuery.data.unpriced.length > 0 && (
          <p className="mt-3 rounded-md bg-[#FFFBEB] p-2 text-xs text-flag">
            {exposureQuery.data.unpriced.length} holding
            {exposureQuery.data.unpriced.length === 1 ? "" : "s"} unpriced — excluded from today's change.
          </p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-medium text-text">Trend</h2>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 rounded-md border border-border p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setMetric("value")}
                aria-pressed={metric === "value"}
                className={`rounded px-2 py-1 ${
                  metric === "value" ? "bg-accent-subtle text-accent" : "text-muted"
                }`}
              >
                Value
              </button>
              <button
                type="button"
                onClick={() => setMetric("pct")}
                aria-pressed={metric === "pct"}
                className={`rounded px-2 py-1 ${
                  metric === "pct" ? "bg-accent-subtle text-accent" : "text-muted"
                }`}
              >
                Weight
              </button>
            </div>
            <label className="text-xs text-muted" htmlFor="trend-range">
              Range
              <select
                id="trend-range"
                value={range}
                onChange={(e) => setRange(e.target.value as TrendRange)}
                className="mt-1 block rounded-md border border-border px-2 py-1 text-xs text-text"
              >
                <option value="30d">30 days</option>
                <option value="90d">90 days</option>
                <option value="1y">1 year</option>
              </select>
            </label>
          </div>
        </div>

        {trendQuery.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
        {trendQuery.isError && <p className="mt-3 text-sm text-loss">Failed to load the trend.</p>}
        {trendQuery.data && (
          <div className="mt-4">
            <TrendChart
              series={trendQuery.data.series.map((s) => ({
                ...s,
                color: resolveColor(s.group_id, s.color),
              }))}
              metric={metric}
            />
          </div>
        )}
      </section>

      <RotationPanel groups={groups} />
    </div>
  );
}
