import { flagLabel, formatPct, impliedPrice, pctSum, type EditableRow } from "./draftModel";

export default function DraftReview({
  rows,
  onRowsChange,
  serverWarnings,
  onBack,
  onConfirm,
  confirmPending,
  confirmError,
}: {
  rows: EditableRow[];
  onRowsChange: (rows: EditableRow[]) => void;
  serverWarnings: string[];
  onBack: () => void;
  onConfirm: () => void;
  confirmPending: boolean;
  confirmError: string | null;
}) {
  function updateRow(key: string, patch: Partial<EditableRow>) {
    onRowsChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  const sum = pctSum(rows);
  const sumOff = rows.length > 0 && Math.abs(sum - 100) > 0.005;
  const otherWarnings = serverWarnings.filter((w) => !w.startsWith("pct_sum="));

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <p className="text-xs font-semibold uppercase tracking-wide text-accent">Step 2 of 3</p>
      <h2 className="mt-1 text-lg font-medium text-text">Review &amp; edit</h2>

      {(sumOff || otherWarnings.length > 0) && (
        <div role="alert" className="mt-3 space-y-1 rounded-md bg-[#FFFBEB] p-3 text-sm text-flag">
          {sumOff && <p>⚠ Contributions add up to {formatPct(sum)}% (not 100%)</p>}
          {otherWarnings.map((w) => (
            <p key={w}>⚠ {w}</p>
          ))}
        </div>
      )}

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="p-2 font-medium">Fund</th>
              <th className="p-2 text-right font-medium">Units</th>
              <th className="p-2 text-right font-medium">Value</th>
              <th className="p-2 text-right font-medium">Contribution %</th>
              <th className="p-2 font-medium">Currency</th>
              <th className="p-2 text-right font-medium">Implied price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-border align-top">
                <td className="p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-text">{row.displayName}</span>
                    {row.matchedFundId != null ? (
                      <span className="rounded-full bg-gain/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gain">
                        Matched
                      </span>
                    ) : (
                      <span className="rounded-full bg-accent-subtle px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
                        New fund
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted">{row.displayCode}</p>
                  {row.flags.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {row.flags.map((f) => (
                        <span
                          key={f}
                          className="rounded-full bg-loss/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-loss"
                        >
                          {flagLabel(f)}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="p-2 text-right">
                  <input
                    aria-label={`${row.displayCode} units`}
                    value={row.units}
                    onChange={(e) => updateRow(row.key, { units: e.target.value })}
                    className="w-24 rounded-md border border-border px-2 py-1 text-right text-sm text-text"
                  />
                </td>
                <td className="p-2 text-right">
                  <input
                    aria-label={`${row.displayCode} value`}
                    value={row.value}
                    onChange={(e) => updateRow(row.key, { value: e.target.value })}
                    className="w-28 rounded-md border border-border px-2 py-1 text-right text-sm text-text"
                  />
                </td>
                <td className="p-2 text-right">
                  <input
                    aria-label={`${row.displayCode} contribution %`}
                    value={row.contributionPct}
                    onChange={(e) => updateRow(row.key, { contributionPct: e.target.value })}
                    className="w-16 rounded-md border border-border px-2 py-1 text-right text-sm text-text"
                  />
                </td>
                <td className="p-2">
                  <input
                    aria-label={`${row.displayCode} currency`}
                    value={row.currency}
                    onChange={(e) => updateRow(row.key, { currency: e.target.value.toUpperCase() })}
                    className="w-16 rounded-md border border-border px-2 py-1 text-sm text-text"
                  />
                </td>
                <td className="p-2 text-right tabular-nums text-muted">
                  {impliedPrice(row.units, row.value) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border border-border px-4 py-2 text-sm text-text"
        >
          Back
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={confirmPending || rows.length === 0}
          className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {confirmPending ? "Saving…" : "Confirm & save"}
        </button>
        {confirmError && <p className="text-sm text-loss">{confirmError}</p>}
      </div>
    </section>
  );
}
