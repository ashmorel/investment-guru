import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  apiFetch,
  decisionBriefErrorMessage,
  generateDecisionBrief,
  getLatestDecisionBrief,
} from "../lib/api";
import type {
  CandidateIdea,
  DecisionBriefPayload,
  DecisionNewsItem,
  HoldingDecision,
  Portfolio,
} from "../lib/types";

const ACTION_TONE: Record<string, string> = {
  increase: "bg-gain/10 text-gain",
  reduce: "bg-loss/10 text-loss",
  exit: "bg-loss/10 text-loss",
  hold: "bg-bg text-muted",
};

function countLabel(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function Evidence({ decision, news }: { decision: HoldingDecision; news: DecisionNewsItem[] }) {
  const sources = decision.evidence_refs
    .map((ref) => news.find((item) => item.evidence_ref === ref))
    .filter((item): item is DecisionNewsItem => item !== undefined);

  return (
    <details className="mt-3 border-t border-border pt-3 text-sm">
      <summary className="cursor-pointer font-medium text-accent">View evidence</summary>
      <div className="mt-3 space-y-2 text-muted">
        {sources.map((item) => (
          <p key={item.evidence_ref}>
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="font-medium text-accent hover:underline"
            >
              {item.headline} ↗
            </a>{" "}
            · {item.source} · {item.impact}
          </p>
        ))}
        {decision.evidence_refs.filter((ref) => !sources.some((item) => item.evidence_ref === ref)).map((ref) => (
          <p key={ref}>Evidence: {ref}</p>
        ))}
        {decision.change_conditions.length > 0 && (
          <p>
            <span className="font-medium text-text">Would change:</span>{" "}
            {decision.change_conditions.join(" · ")}.
          </p>
        )}
      </div>
    </details>
  );
}

function DecisionRow({ decision, news }: { decision: HoldingDecision; news: DecisionNewsItem[] }) {
  return (
    <article className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="min-w-20 font-bold text-text">{decision.symbol}</h3>
        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${ACTION_TONE[decision.action]}`}>
          {decision.action.toUpperCase()}
        </span>
        {decision.conviction && (
          <span className="rounded-full bg-bg px-2.5 py-1 text-xs font-semibold text-muted">
            {decision.conviction} conviction
          </span>
        )}
        <p className="min-w-64 flex-1 text-sm text-text">{decision.rationale}</p>
      </div>
      <Evidence decision={decision} news={news} />
    </article>
  );
}

function CandidateCard({ candidate, watchlists }: { candidate: CandidateIdea; watchlists: Portfolio[] }) {
  const [watchlistId, setWatchlistId] = useState(watchlists[0]?.id ? String(watchlists[0].id) : "");
  const add = useMutation({
    mutationFn: () =>
      apiFetch(`/api/portfolios/${watchlistId}/positions`, {
        method: "POST",
        body: JSON.stringify({ symbol: candidate.symbol, quantity: null }),
      }),
  });
  const selectedName = watchlists.find((portfolio) => String(portfolio.id) === watchlistId)?.name;

  return (
    <article className="rounded-xl border border-border bg-surface p-4">
      <h3 className="text-lg font-bold text-text">{candidate.symbol}</h3>
      <p className="mt-1 text-xs font-medium text-muted">{candidate.name}</p>
      <p className="text-xs text-muted">
        {candidate.instrument_type.toUpperCase()} · {candidate.market}
      </p>
      <div className="mt-3 flex gap-2 text-xs font-semibold">
        <span className="rounded-full bg-accent-subtle px-2.5 py-1 text-accent">CONSIDER</span>
        <span className="rounded-full bg-bg px-2.5 py-1 text-muted">{candidate.conviction} conviction</span>
      </div>
      <p className="mt-3 text-xs text-muted">
        <span className="font-medium text-text">Why surfaced</span> ·{" "}
        <span>{candidate.why_surfaced}</span>
      </p>
      <p className="mt-3 text-sm text-text">{candidate.portfolio_fit}</p>
      <p className="mt-2 text-xs text-muted">Principal risk · {candidate.principal_risk}</p>
      {candidate.watch_next.length > 0 && (
        <div className="mt-3 text-xs text-muted">
          <p className="font-medium text-text">Watch next</p>
          <ul
            aria-label={`What to watch for ${candidate.symbol}`}
            className="mt-1 list-disc space-y-1 pl-4"
          >
            {candidate.watch_next.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {add.isSuccess ? (
        <p className="mt-4 text-sm font-medium text-gain">Added to {selectedName}</p>
      ) : (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <label className="text-xs text-muted">
            Watchlist
            <select
              value={watchlistId}
              onChange={(event) => setWatchlistId(event.target.value)}
              className="mt-1 block rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text"
            >
              <option value="">Select a watchlist</option>
              {watchlists.map((portfolio) => (
                <option key={portfolio.id} value={portfolio.id}>{portfolio.name}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => add.mutate()}
            disabled={!watchlistId || add.isPending}
            className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
            aria-label={`Add ${candidate.symbol} to watchlist`}
          >
            {add.isPending ? "Adding…" : "Add to watchlist →"}
          </button>
        </div>
      )}
      {add.isError && <p className="mt-2 text-sm text-loss">Could not add to watchlist.</p>}
    </article>
  );
}

function Brief({ payload, watchlists }: { payload: DecisionBriefPayload; watchlists: Portfolio[] }) {
  const actionable = payload.holdings.filter((item) => !["hold", "data_incomplete"].includes(item.action));
  const holds = payload.holdings.filter((item) => item.action === "hold");
  const incomplete = payload.holdings.filter((item) => item.action === "data_incomplete");

  return (
    <div className="space-y-6">
      <p className="rounded-xl bg-accent-subtle p-4 text-sm text-accent">{payload.summary}</p>
      <section aria-labelledby="decision-actions" className="rounded-xl border border-border bg-surface p-5">
        <h2 id="decision-actions" className="text-xl font-semibold text-text">Actions</h2>
        <p className="mt-1 text-xs text-muted">Act · {countLabel(actionable.length, "holding")} need{actionable.length === 1 ? "s" : ""} a decision</p>
        <div className="mt-3 space-y-3">
          {actionable.map((decision) => <DecisionRow key={decision.symbol} decision={decision} news={payload.material_news} />)}
          {holds.length > 0 && (
            <details className="rounded-xl bg-bg p-4">
              <summary className="cursor-pointer text-sm font-semibold text-text">Hold · {countLabel(holds.length, "holding")}</summary>
              <div className="mt-3 space-y-3">
                {holds.map((decision) => <DecisionRow key={decision.symbol} decision={decision} news={payload.material_news} />)}
              </div>
            </details>
          )}
          {incomplete.map((decision) => (
            <article key={decision.symbol} className="rounded-xl border border-amber-200 bg-amber-50 p-4">
              <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-700">DATA INCOMPLETE</span>
              <p className="mt-3 text-sm text-text">{decision.symbol} · {decision.rationale}</p>
              <p className="mt-2 text-xs font-medium text-accent">Review missing inputs</p>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="decision-news" className="rounded-xl border border-border bg-surface p-5">
        <h2 id="decision-news" className="text-xl font-semibold text-text">News that matters</h2>
        <p className="mt-1 text-xs text-muted">Only evidence that affects or may affect a holding verdict.</p>
        <ul className="mt-3 space-y-3">
          {payload.material_news.map((item) => (
            <li key={item.evidence_ref} className="rounded-xl border border-border p-4 text-sm">
              <span className="font-semibold uppercase text-accent">{item.importance}</span>{" "}
              <strong className="ml-2 text-text">{item.symbol}</strong>
              <a href={item.url} target="_blank" rel="noreferrer" className="ml-2 text-text hover:underline">{item.headline}</a>
              <p className="mt-1 text-muted">{item.source} · {item.impact}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="portfolio-context" className="rounded-xl border border-border bg-surface p-5">
        <h2 id="portfolio-context" className="text-xl font-semibold text-text">Portfolio context</h2>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-text">
          {payload.portfolio_observations.map((observation) => <li key={observation}>{observation}</li>)}
        </ul>
      </section>

      <section aria-labelledby="candidate-ideas" className="rounded-xl border border-border bg-surface p-5">
        <h2 id="candidate-ideas" className="text-xl font-semibold text-text">Ideas to consider holding</h2>
        <p className="mt-1 text-xs text-muted">Grounded shortlist · not currently held · no trade execution</p>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {payload.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} watchlists={watchlists} />)}
        </div>
      </section>
      <p className="text-xs text-muted">{payload.disclaimer}</p>
    </div>
  );
}

export default function DecisionCockpit() {
  const queryClient = useQueryClient();
  const latest = useQuery({ queryKey: ["decision-brief"], queryFn: getLatestDecisionBrief });
  const portfolios = useQuery({ queryKey: ["portfolios"], queryFn: () => apiFetch<Portfolio[]>("/api/portfolios") });
  const generate = useMutation({
    mutationFn: generateDecisionBrief,
    onSuccess: (report) => queryClient.setQueryData(["decision-brief"], report),
  });
  const report = latest.data ?? null;
  const watchlists = (portfolios.data ?? []).filter((portfolio) => portfolio.kind === "watchlist");
  const generationError = generate.isError ? decisionBriefErrorMessage(generate.error) : null;

  return (
    <section aria-labelledby="decision-cockpit-title" className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 id="decision-cockpit-title" className="text-2xl font-bold text-text">Decision Cockpit</h2>
          {report && <p className="mt-1 text-xs text-muted">Latest brief generated {new Date(report.created_at).toLocaleString()} · Data as of {new Date(report.payload.data_as_of).toLocaleString()}</p>}
        </div>
        {report && (
          <button type="button" onClick={() => generate.mutate()} disabled={generate.isPending} className="rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white disabled:opacity-50">
            {generate.isPending ? "Generating…" : "Generate new brief"}
          </button>
        )}
      </header>

      {generationError && <p role="alert" className="rounded-xl border border-border bg-surface p-4 text-sm font-semibold text-loss">{generationError}</p>}
      {latest.isPending && <p className="text-sm text-muted">Loading latest brief…</p>}
      {latest.isError && <p role="alert" className="text-sm text-loss">Could not load the latest brief.</p>}
      {latest.isSuccess && !report && (
        <div className="rounded-xl border border-border bg-surface p-8 text-center">
          <h2 className="text-xl font-semibold text-text">No Decision Brief yet</h2>
          <p className="mt-2 text-sm text-muted">Generate one coherent snapshot of holding actions, material evidence and grounded ideas.</p>
          <button type="button" onClick={() => generate.mutate()} disabled={generate.isPending} className="mt-4 rounded-lg bg-accent px-4 py-3 text-sm font-semibold text-white disabled:opacity-50">
            {generate.isPending ? "Generating…" : "Generate first brief"}
          </button>
        </div>
      )}
      {report && <Brief payload={report.payload} watchlists={watchlists} />}
    </section>
  );
}
