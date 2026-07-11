import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { searchFunds } from "../../lib/api";
import type { OrsoFundOut } from "../../lib/types";

/** Typeahead over the user's scheme funds (including archived). Selecting a
 * result hands the fund back to the caller via `onSelect` — this component
 * has no opinion on what happens next (IngestWizard turns it into a draft
 * row). */
export default function FundSearch({ onSelect }: { onSelect: (fund: OrsoFundOut) => void }) {
  const [query, setQuery] = useState("");

  const results = useQuery({
    queryKey: ["orso", "fund-search", query],
    queryFn: () => searchFunds(query),
  });

  return (
    <div>
      <label className="block text-sm font-medium text-text" htmlFor="orso-fund-search">
        Add fund to allocation
      </label>
      <input
        id="orso-fund-search"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search funds by code or name…"
        className="mt-2 block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
      />

      {results.isPending && <p className="mt-2 text-sm text-muted">Loading…</p>}
      {results.isError && <p className="mt-2 text-sm text-loss">Could not search funds.</p>}

      {results.isSuccess && results.data.length === 0 && (
        <p className="mt-2 text-sm text-muted">No funds match "{query}".</p>
      )}

      {results.isSuccess && results.data.length > 0 && (
        <ul className="mt-2 divide-y divide-border rounded-md border border-border">
          {results.data.map((fund) => (
            <li key={fund.id} className="flex items-center justify-between gap-3 p-3">
              <div className="flex items-center gap-3">
                <span className="rounded-md bg-bg px-2 py-1 text-xs font-semibold text-muted">
                  {fund.code}
                </span>
                <div>
                  <p className="text-sm font-medium text-text">
                    {fund.name}
                    {fund.archived && <span className="ml-2 text-xs text-muted">(archived)</span>}
                  </p>
                  <p className="text-xs text-muted">{fund.asset_class}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onSelect(fund)}
                className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
              >
                + Add to allocation
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-2 text-xs text-muted">Searches your scheme's funds, including archived.</p>
    </div>
  );
}
