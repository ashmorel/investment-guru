import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import ChatPanel from "../components/ChatPanel";
import GuruTakePanel from "../components/GuruTakePanel";
import VerdictChip from "../components/VerdictChip";
import { ApiError, apiFetch, isBudgetExhausted } from "../lib/api";
import type { DashboardData, DigestPayload, GuruReport, ReviewPayload } from "../lib/types";

interface ReviewsResponse {
  reviews: GuruReport<ReviewPayload>[];
}

function DigestCard() {
  const qc = useQueryClient();
  const digest = useQuery({
    queryKey: ["guru", "digest"],
    queryFn: () => apiFetch<GuruReport<DigestPayload>>("/api/guru/digest/latest"),
    retry: false,
  });

  const generate = useMutation({
    mutationFn: () => apiFetch<GuruReport<DigestPayload>>("/api/guru/digest", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guru", "digest"] }),
  });

  const notFound = digest.isError && digest.error instanceof ApiError && digest.error.status === 404;
  const unconfigured =
    generate.isError && generate.error instanceof ApiError && generate.error.status === 503;
  const budgetExhausted = generate.isError && isBudgetExhausted(generate.error);

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-center justify-between">
        <h2 className="font-medium text-text">Daily digest</h2>
        <button
          type="button"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          className="text-sm text-accent underline disabled:opacity-50"
        >
          {generate.isPending ? "Generating…" : "Generate now"}
        </button>
      </div>

      {unconfigured && (
        <p className="mt-3 rounded-md bg-accent-subtle p-3 text-sm text-accent">
          The Guru isn't configured yet — an administrator needs to add an LLM API key before
          reports can be generated.
        </p>
      )}
      {budgetExhausted && (
        <p className="mt-3 rounded-md bg-accent-subtle p-3 text-sm text-accent">
          Daily AI limit reached — resets tomorrow.
        </p>
      )}

      {digest.isPending && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {digest.isError && !notFound && (
        <p className="mt-3 text-sm text-loss">Failed to load digest.</p>
      )}
      {notFound && <p className="mt-3 text-sm text-muted">No digest yet — generate one.</p>}

      {digest.data && (
        <div className="mt-3 space-y-2 text-sm">
          {digest.data.payload.earnings_this_week.map((e, i) => (
            <p key={`earnings-${i}`}>
              <span className="mr-2 rounded-full bg-accent-subtle px-2 py-0.5 text-xs font-medium text-accent">
                EARNINGS
              </span>
              {e.symbol}
              {e.date ? ` · ${e.date}` : ""} — {e.note}
            </p>
          ))}
          {digest.data.payload.movers.map((m, i) => (
            <p key={`mover-${i}`}>
              <span className="mr-2 rounded-full bg-accent-subtle px-2 py-0.5 text-xs font-medium text-accent">
                MOVER
              </span>
              {m.symbol} — {m.note}
            </p>
          ))}
          {digest.data.payload.news_flags.map((n, i) => (
            <p key={`news-${i}`}>
              <span className="mr-2 rounded-full bg-accent-subtle px-2 py-0.5 text-xs font-medium text-accent">
                NEWS
              </span>
              {n.symbol ? `${n.symbol} — ` : ""}
              {n.headline}: {n.comment}
            </p>
          ))}
          <p className="text-muted">{digest.data.payload.summary}</p>
          <p className="text-xs text-muted">
            Generated {new Date(digest.data.created_at).toLocaleString()}
          </p>
          <p className="text-xs text-muted">{digest.data.payload.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

function ReviewsCard() {
  const qc = useQueryClient();
  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardData>("/api/dashboard"),
  });
  const [portfolioId, setPortfolioId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const portfolios = dash.data?.portfolios ?? [];
  const activeId = portfolioId ?? portfolios[0]?.id ?? null;

  const reviews = useQuery({
    queryKey: ["guru", "reviews", activeId],
    queryFn: () =>
      apiFetch<ReviewsResponse>(`/api/guru/reviews?portfolio_id=${activeId}&limit=10`),
    enabled: activeId !== null,
  });

  const runReview = useMutation({
    mutationFn: () =>
      apiFetch<GuruReport<ReviewPayload>>("/api/guru/reviews", {
        method: "POST",
        body: JSON.stringify({ portfolio_id: activeId }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guru", "reviews", activeId] }),
  });

  const unconfigured =
    runReview.isError && runReview.error instanceof ApiError && runReview.error.status === 503;
  const alreadyGenerating =
    runReview.isError && runReview.error instanceof ApiError && runReview.error.status === 409;
  const budgetExhausted = runReview.isError && isBudgetExhausted(runReview.error);

  const list = reviews.data?.reviews ?? [];
  const expanded = list.find((r) => r.id === expandedId) ?? null;

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-medium text-text">Portfolio reviews</h2>
        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor="review-portfolio">
            Portfolio
          </label>
          <select
            id="review-portfolio"
            value={activeId ?? ""}
            onChange={(e) => {
              setPortfolioId(Number(e.target.value));
              setExpandedId(null);
            }}
            className="rounded-md border border-border px-2 py-1 text-sm text-text"
          >
            {portfolios.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => runReview.mutate()}
            disabled={runReview.isPending || activeId === null}
            className="rounded-md bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {runReview.isPending ? "Running…" : "Run review"}
          </button>
        </div>
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

      {list.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No reviews yet — run one.</p>
      ) : (
        <ul className="mt-3 space-y-1 text-sm">
          {list.map((r) => {
            const portfolioName = portfolios.find((p) => p.id === r.portfolio_id)?.name ?? "Portfolio";
            return (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setExpandedId(r.id === expandedId ? null : r.id)}
                  className="text-left text-accent underline"
                >
                  {portfolioName} · {new Date(r.created_at).toLocaleString()}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {expanded && (
        <div className="mt-4 space-y-3 rounded-lg bg-bg p-4">
          <ul className="space-y-2">
            {expanded.payload.positions.map((pos) => (
              <li key={pos.symbol} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium text-text">{pos.symbol}</span>
                <VerdictChip action={pos.action} conviction={pos.conviction} />
                <span className="text-muted">{pos.rationale}</span>
              </li>
            ))}
          </ul>
          {expanded.payload.observations.length > 0 && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted">
                Observations
              </p>
              <ul className="mt-1 space-y-1 text-sm text-muted">
                {expanded.payload.observations.map((o, i) => (
                  <li key={i}>{o}</li>
                ))}
              </ul>
            </div>
          )}
          {expanded.payload.watch_next.length > 0 && (
            <p className="text-sm text-muted">
              <span className="font-medium text-text">Watch next: </span>
              {expanded.payload.watch_next.join(", ")}
            </p>
          )}
          <p className="text-xs text-muted">{expanded.payload.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

export default function GuruPage() {
  const [searchParams] = useSearchParams();
  const discuss = searchParams.get("discuss");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Guru</h1>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <GuruTakePanel />
          <DigestCard />
          <ReviewsCard />
        </div>
        <ChatPanel discuss={discuss} />
      </div>
    </div>
  );
}
