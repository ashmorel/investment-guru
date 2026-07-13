import type { DraftRow, ProposedFund } from "../../lib/types";

/** Frontend view-model for one row of the editable review table. Built from
 * a server `DraftRow` (or from a FundSearch selection) and edited in place
 * before being turned into an `ApplyRequest` on Confirm. Pulled out of
 * DraftReview.tsx (a component file) so Fast Refresh doesn't warn about
 * mixed component/non-component exports. */
export interface EditableRow {
  key: string;
  matchedFundId: number | null;
  proposed: ProposedFund | null;
  displayCode: string;
  displayName: string;
  units: string;
  value: string;
  contributionPct: string;
  currency: string;
  flags: string[];
}

export function draftRowToEditable(row: DraftRow, seq: number): EditableRow {
  return {
    key: `draft-${row.parsed_code || "row"}-${seq}`,
    matchedFundId: row.matched_fund_id,
    proposed: row.proposed_fund,
    displayCode: row.matched_fund_id != null ? row.parsed_code : (row.proposed_fund?.code ?? row.parsed_code),
    displayName: row.parsed_name ?? row.proposed_fund?.name ?? row.parsed_code,
    units: row.units ?? "",
    value: row.value ?? "",
    contributionPct: row.contribution_pct ?? "",
    currency: row.currency,
    flags: row.flags,
  };
}

export function fundToEditableRow(
  fund: { id: number; code: string; name: string; currency: string },
  seq: number,
): EditableRow {
  return {
    key: `search-${fund.id}-${seq}`,
    matchedFundId: fund.id,
    proposed: null,
    displayCode: fund.code,
    displayName: fund.name,
    units: "",
    value: "",
    contributionPct: "",
    currency: fund.currency,
    flags: [],
  };
}

export function impliedPrice(units: string, value: string): string | null {
  const u = Number(units);
  const v = Number(value);
  if (!units.trim() || !value.trim() || !Number.isFinite(u) || !Number.isFinite(v) || u === 0) return null;
  return (v / u).toFixed(4);
}

/** Recompute the parse-warning flags from the row's CURRENT (possibly edited)
 * values instead of trusting the stale server-side `flags`, so a flag clears
 * once the user fixes the underlying value. `unmatched` isn't derivable from
 * the edited fields — it's carried over from whatever the server decided. */
export function liveFlags(row: EditableRow): string[] {
  const flags: string[] = [];
  if (row.units.trim() && !Number.isFinite(Number(row.units))) flags.push("unparseable_units");
  if (row.value.trim() && !Number.isFinite(Number(row.value))) flags.push("unparseable_value");
  if (row.contributionPct.trim() && !Number.isFinite(Number(row.contributionPct))) {
    flags.push("unparseable_pct");
  }
  if (row.flags.includes("unmatched")) flags.push("unmatched");
  return flags;
}

export function pctSum(rows: EditableRow[]): number {
  return rows.reduce((sum, r) => sum + (Number(r.contributionPct) || 0), 0);
}

export function formatPct(n: number): string {
  return (Math.round(n * 100) / 100).toString();
}

const FLAG_LABELS: Record<string, string> = {
  unmatched: "Unmatched",
  unparseable_units: "Unparseable units",
  unparseable_value: "Unparseable value",
  unparseable_pct: "Unparseable %",
};

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replace(/_/g, " ");
}
