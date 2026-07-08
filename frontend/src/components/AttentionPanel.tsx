import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import type { AttentionResponse, AttentionSignal } from "../lib/types";

const DOT: Record<AttentionSignal["severity"], string> = {
  high: "bg-loss",
  watch: "bg-flag",
  info: "bg-muted",
};

export default function AttentionPanel() {
  const q = useQuery({
    queryKey: ["attention"],
    queryFn: () => apiFetch<AttentionResponse>("/api/dashboard/attention"),
  });

  if (q.isPending) return <p className="text-muted">Loading signals…</p>;
  if (q.isError) return <p className="text-loss">Failed to load signals.</p>;
  const signals = q.data.signals;

  return (
    <section className="rounded-xl border border-border bg-surface p-5">
      <h2 className="mb-3 font-medium text-text">Needs your attention</h2>
      {signals.length === 0 ? (
        <p className="text-sm text-muted">No flags right now — run analysis to refresh.</p>
      ) : (
        <ul className="space-y-2">
          {signals.map((s) => (
            <li key={s.id} className="flex items-start gap-3 text-sm">
              <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${DOT[s.severity]}`} />
              <span className="sr-only">{s.severity} severity</span>
              <div>
                <p className="text-text">{s.title}</p>
                <p className="text-xs text-muted">
                  {s.portfolio_name}
                  {s.symbol ? ` · ${s.symbol}` : ""} · {new Date(s.computed_at).toLocaleString()}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
