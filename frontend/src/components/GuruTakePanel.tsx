import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError, apiFetch, isBudgetExhausted } from "../lib/api";
import type { GuruReport, TakePayload } from "../lib/types";
import VerdictChip from "./VerdictChip";

export default function GuruTakePanel() {
  const qc = useQueryClient();
  const take = useQuery({
    queryKey: ["guru", "take"],
    queryFn: () => apiFetch<GuruReport<TakePayload>>("/api/guru/take/latest"),
    retry: false,
  });

  const refresh = useMutation({
    mutationFn: () => apiFetch<GuruReport<TakePayload>>("/api/guru/take", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guru", "take"] }),
  });

  const notFound = take.isError && take.error instanceof ApiError && take.error.status === 404;
  const unconfigured =
    refresh.isError && refresh.error instanceof ApiError && refresh.error.status === 503;
  const alreadyGenerating =
    refresh.isError && refresh.error instanceof ApiError && refresh.error.status === 409;
  const budgetExhausted = refresh.isError && isBudgetExhausted(refresh.error);

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-sm text-accent">
            ✦
          </span>
          <h2 className="font-medium text-text">Guru's take</h2>
        </div>
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="text-sm text-accent underline disabled:opacity-50"
        >
          {refresh.isPending ? "Refreshing…" : "Refresh"}
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

      {take.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {take.isError && !notFound && (
        <p className="mt-3 text-sm text-loss">Failed to load Guru's take.</p>
      )}
      {notFound && (
        <p className="mt-3 text-sm text-muted">No take yet — refresh to generate.</p>
      )}

      {take.data && (
        <>
          <p className="mt-3 text-sm text-text">{take.data.payload.commentary}</p>

          {take.data.payload.risks.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-muted">
              {take.data.payload.risks.map((r, i) => (
                <li key={i}>
                  <span className="font-medium text-text">{r.kind}</span>: {r.note}
                </li>
              ))}
            </ul>
          )}

          {take.data.payload.ideas.length > 0 && (
            <ul className="mt-4 space-y-2">
              {take.data.payload.ideas.map((idea, i) => (
                <li key={i} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <div className="flex items-center gap-2">
                    <VerdictChip
                      action={idea.action}
                      conviction={idea.conviction}
                      symbol={idea.symbol ?? undefined}
                    />
                    <span className="text-muted">{idea.rationale}</span>
                  </div>
                  <Link
                    to={`/guru?discuss=${encodeURIComponent(JSON.stringify(idea))}`}
                    className="shrink-0 text-xs text-accent underline"
                  >
                    Discuss any idea in chat →
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <p className="mt-4 text-xs text-muted">
            Generated {new Date(take.data.created_at).toLocaleString()}
          </p>
          <p className="mt-1 text-xs text-muted">{take.data.payload.disclaimer}</p>
        </>
      )}
    </section>
  );
}
