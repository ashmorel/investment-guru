import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  generateNewsSummary,
  getNews,
  getNewsSummary,
  getStockNews,
  isBudgetExhausted,
  newsSummaryErrorMessage,
  refreshNews,
} from "../lib/api";
import type { GuruReport, NewsItem, NewsSummary } from "../lib/types";

// Small self-contained relative-time formatter — the app has no shared date
// util yet and these timestamps (headline published_at, news as_of, summary
// created_at) are all display-only, so a dedicated shared lib felt premature.
export function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

const SENTIMENT_STYLE: Record<NewsSummary["sentiment"], { label: string; cls: string }> = {
  positive: { label: "Positive", cls: "bg-gain/10 text-gain" },
  negative: { label: "Negative", cls: "bg-loss/10 text-loss" },
  watch: { label: "Watch", cls: "bg-flag/10 text-flag" },
  neutral: { label: "Neutral", cls: "bg-muted/10 text-muted" },
};

function SentimentPill({ sentiment }: { sentiment: NewsSummary["sentiment"] }) {
  const s = SENTIMENT_STYLE[sentiment];
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.label}
    </span>
  );
}

function HeadlineList({ items }: { items: NewsItem[] }) {
  return (
    <ul className="divide-y divide-border">
      {items.map((item) => (
        <li key={item.url} className="py-2">
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-start justify-between gap-2 text-sm text-text hover:underline"
          >
            <span>
              {item.title}
              <span className="mt-0.5 block text-xs text-muted">
                {item.source} · {relativeTime(item.published_at)}
              </span>
            </span>
            <span aria-hidden="true" className="mt-0.5 shrink-0 text-muted">
              ↗
            </span>
            <span className="sr-only">(opens {item.source} article in a new tab)</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

// Shared "Guru summary" block used both on the dashboard News panel (one per
// ranked holding) and on the per-position news list. `summaryAvailable`
// mirrors NewsGroup.summary_available: when it's explicitly false the query
// is skipped (a fresh GET would just 404 — no point spending a round trip),
// when true or omitted (per-position callers don't have the flag) it lazily
// tries to load the latest stored summary, falling back to a Summarize
// button when none exists yet (404).
export function NewsSummarySection({
  symbol,
  summaryAvailable,
}: {
  symbol: string;
  summaryAvailable?: boolean;
}) {
  const qc = useQueryClient();
  const summaryQuery = useQuery({
    queryKey: ["news", "summary", symbol],
    queryFn: () => getNewsSummary(symbol),
    enabled: summaryAvailable !== false,
    retry: false,
  });

  const generate = useMutation({
    mutationFn: () => generateNewsSummary(symbol),
    onSuccess: (report: GuruReport<NewsSummary>) => {
      qc.setQueryData(["news", "summary", symbol], report);
    },
  });

  const notFound =
    summaryQuery.isError && summaryQuery.error instanceof ApiError && summaryQuery.error.status === 404;
  const summaryLoadFailed = summaryQuery.isError && !notFound;
  const budgetExhausted = generate.isError && isBudgetExhausted(generate.error);
  const genErrorMessage = generate.isError ? newsSummaryErrorMessage(generate.error) : null;

  const report = summaryQuery.data;

  return (
    <div className="mt-3 rounded-lg border border-border bg-bg/50 p-4">
      {summaryQuery.isPending && summaryAvailable !== false ? (
        <p className="text-sm text-muted">Loading summary…</p>
      ) : report ? (
        <>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="text-accent">
                ✦
              </span>
              <h3 className="text-sm font-medium text-text">Guru summary</h3>
            </div>
            <SentimentPill sentiment={report.payload.sentiment} />
          </div>
          <p className="mt-2 text-sm text-text">{report.payload.summary}</p>
          {report.payload.key_points.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
              {report.payload.key_points.map((point, i) => (
                <li key={i}>{point}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted">
            Generated {relativeTime(report.created_at)} ·{" "}
            <button
              type="button"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
              className="text-accent underline disabled:opacity-50"
            >
              {generate.isPending ? "Regenerating…" : "Regenerate"}
            </button>
          </p>
          <p className="mt-1 text-xs text-muted">{report.payload.disclaimer}</p>
        </>
      ) : (
        <>
          {summaryLoadFailed && (
            <p className="mb-2 text-xs text-loss">Couldn't load the saved summary.</p>
          )}
          <button
            type="button"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            className="rounded-md border border-accent px-3 py-1.5 text-sm text-accent disabled:opacity-50"
          >
            {generate.isPending ? "Summarizing…" : "Summarize"}
          </button>
        </>
      )}
      {budgetExhausted && (
        <p className="mt-2 text-sm text-flag">Daily AI limit reached — resets tomorrow.</p>
      )}
      {!budgetExhausted && genErrorMessage && (
        <p className="mt-2 text-sm text-loss">{genErrorMessage}</p>
      )}
    </div>
  );
}

// Per-position "Recent news" block (Task 5 step 4) — mounted lazily by
// PortfolioDetailPage only once a position's news row is expanded, so it
// doesn't fetch anything until the user asks for it.
export function PositionNews({ symbol }: { symbol: string }) {
  const stockNews = useQuery({
    queryKey: ["stockNews", symbol],
    queryFn: () => getStockNews(symbol),
  });

  if (stockNews.isPending) return <p className="text-sm text-muted">Loading news…</p>;
  if (stockNews.isError) {
    return <p className="text-sm text-loss">Failed to load news for {symbol}.</p>;
  }
  const { items, as_of } = stockNews.data;

  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-text">Recent news</h3>
        <p className="text-xs text-muted">
          {items.length} article{items.length === 1 ? "" : "s"} · fetched {relativeTime(as_of)}
        </p>
      </div>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted">No recent headlines for {symbol}.</p>
      ) : (
        <div className="mt-2">
          <HeadlineList items={items} />
        </div>
      )}
      <NewsSummarySection symbol={symbol} />
    </div>
  );
}

export default function NewsPanel() {
  const qc = useQueryClient();
  const news = useQuery({
    queryKey: ["news"],
    queryFn: () => getNews(),
  });

  const refresh = useMutation({
    mutationFn: () => refreshNews(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["news"] }),
  });

  return (
    <section className="rounded-xl border border-border bg-surface p-5 shadow">
      <div className="flex items-center justify-between">
        <h2 className="font-medium text-text">News</h2>
        <div className="flex items-center gap-3">
          {news.data && (
            <p className="text-xs text-muted">Fetched {relativeTime(news.data.as_of)}</p>
          )}
          <button
            type="button"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="text-sm text-accent underline disabled:opacity-50"
          >
            {refresh.isPending ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {news.isPending && <p className="mt-3 text-sm text-muted">Loading news…</p>}
      {news.isError && <p className="mt-3 text-sm text-loss">Failed to load news.</p>}

      {news.data && news.data.groups.length === 0 && (
        <p className="mt-3 text-sm text-muted">No recent headlines for your holdings yet.</p>
      )}

      {news.data && news.data.groups.length > 0 && (
        <ul className="mt-4 space-y-4">
          {news.data.groups.map((g) => (
            <li key={g.symbol} className="rounded-lg border border-border p-4">
              <div className="flex items-baseline gap-2">
                <span className="font-medium text-text">{g.symbol}</span>
                <span className="text-sm text-muted">{g.name}</span>
              </div>
              <div className="mt-2">
                <HeadlineList items={g.items} />
              </div>
              <NewsSummarySection symbol={g.symbol} summaryAvailable={g.summary_available} />
            </li>
          ))}
        </ul>
      )}

      {news.data && news.data.unavailable.length > 0 && (
        <p className="mt-4 text-xs text-muted">
          Couldn't fetch news for: {news.data.unavailable.join(", ")}.
        </p>
      )}
    </section>
  );
}
