import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import NewsPanel from "./NewsPanel";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 60 * 60 * 1000).toISOString();
}

const AAPL_ITEM = {
  title: "Apple beats Q3 estimates on services growth",
  source: "Reuters",
  url: "https://example.com/aapl-q3",
  published_at: hoursAgo(2),
};

const MSFT_ITEM = {
  title: "Microsoft Azure revenue growth accelerates on AI demand",
  source: "CNBC",
  url: "https://example.com/msft-azure",
  published_at: hoursAgo(3),
};

const NEWS_RESPONSE = {
  groups: [
    {
      symbol: "AAPL",
      name: "Apple Inc.",
      latest_published_at: AAPL_ITEM.published_at,
      items: [AAPL_ITEM],
      summary_available: false,
    },
    {
      symbol: "MSFT",
      name: "Microsoft Corp.",
      latest_published_at: MSFT_ITEM.published_at,
      items: [MSFT_ITEM],
      summary_available: false,
    },
  ],
  unavailable: [],
  as_of: hoursAgo(0),
};

const SUMMARY_REPORT = {
  id: 1,
  kind: "news",
  portfolio_id: null,
  payload: {
    summary: "Apple's fiscal Q3 print beat consensus on services growth.",
    sentiment: "positive",
    key_points: ["Services revenue grew double digits", "China iPhone units stabilized"],
    disclaimer: "AI-generated summary, not investment advice.",
  },
  model: "gpt-5-scan",
  created_at: hoursAgo(0),
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NewsPanel />
    </QueryClientProvider>,
  );
}

describe("NewsPanel", () => {
  it("renders ranked groups with deduped headlines, source and an external link", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/news")) return jsonResponse(NEWS_RESPONSE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    const aaplHeadline = await screen.findByText(/apple beats q3 estimates/i);
    expect(aaplHeadline).toBeInTheDocument();
    expect(screen.getByText(/microsoft azure revenue growth/i)).toBeInTheDocument();
    expect(screen.getByText(/reuters ·/i)).toBeInTheDocument();
    expect(screen.getByText(/cnbc ·/i)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /apple beats q3 estimates/i });
    expect(link).toHaveAttribute("href", AAPL_ITEM.url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("summarizes a group on demand and renders the summary + sentiment pill", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary") && init?.method === "POST") {
        return jsonResponse(SUMMARY_REPORT, 201);
      }
      if (url.includes("/api/news/") && url.includes("/summary")) {
        return jsonResponse({ detail: "no_summary" }, 404);
      }
      if (url.includes("/api/news")) return jsonResponse(NEWS_RESPONSE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    const groups = screen.getAllByRole("listitem");
    const aaplCard = groups.find((li) => within(li).queryByText("AAPL"));
    if (!aaplCard) throw new Error("AAPL card not found");

    await user.click(within(aaplCard).getByRole("button", { name: /summarize/i }));

    expect(
      await within(aaplCard).findByText(/apple's fiscal q3 print beat consensus/i),
    ).toBeInTheDocument();
    expect(within(aaplCard).getByText("Positive")).toBeInTheDocument();
    expect(within(aaplCard).getByText(/services revenue grew double digits/i)).toBeInTheDocument();
    expect(within(aaplCard).getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  });

  it("shows the budget-exhausted message on a 429 from summarize", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary") && init?.method === "POST") {
        return jsonResponse({ detail: "budget_exhausted" }, 429);
      }
      if (url.includes("/api/news/") && url.includes("/summary")) {
        return jsonResponse({ detail: "no_summary" }, 404);
      }
      if (url.includes("/api/news")) return jsonResponse(NEWS_RESPONSE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    const groups = screen.getAllByRole("listitem");
    const aaplCard = groups.find((li) => within(li).queryByText("AAPL"));
    if (!aaplCard) throw new Error("AAPL card not found");

    await user.click(within(aaplCard).getByRole("button", { name: /summarize/i }));

    expect(
      await within(aaplCard).findByText(/daily ai limit reached — resets tomorrow/i),
    ).toBeInTheDocument();
  });

  it("has no axe violations once populated with a summary", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary") && init?.method === "POST") {
        return jsonResponse(SUMMARY_REPORT, 201);
      }
      if (url.includes("/api/news/") && url.includes("/summary")) {
        return jsonResponse({ detail: "no_summary" }, 404);
      }
      if (url.includes("/api/news")) return jsonResponse(NEWS_RESPONSE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    const { container } = renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    const groups = screen.getAllByRole("listitem");
    const aaplCard = groups.find((li) => within(li).queryByText("AAPL"));
    if (!aaplCard) throw new Error("AAPL card not found");
    await user.click(within(aaplCard).getByRole("button", { name: /summarize/i }));
    await within(aaplCard).findByText(/apple's fiscal q3 print beat consensus/i);

    expect(await axe(container)).toHaveNoViolations();
  });

  it("auto-loads a stored summary for a summary_available group without clicking Summarize", async () => {
    let summaryGetHits = 0;
    const availableResponse = {
      ...NEWS_RESPONSE,
      groups: [{ ...NEWS_RESPONSE.groups[0], summary_available: true }],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary")) {
        if (init?.method === "POST") throw new Error("Summarize POST should not fire on auto-load");
        summaryGetHits += 1;
        return jsonResponse(SUMMARY_REPORT);
      }
      if (url.includes("/api/news")) return jsonResponse(availableResponse);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    // The eager GET branch fires (summary_available !== false) and renders the
    // stored summary + sentiment pill with no Summarize interaction.
    expect(
      await screen.findByText(/apple's fiscal q3 print beat consensus/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Positive")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^summarize$/i })).not.toBeInTheDocument();
    expect(summaryGetHits).toBe(1);
  });

  it("skips the summary GET for a summary_available=false group and shows Summarize", async () => {
    let summaryGetHits = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary")) {
        if (!init?.method || init.method === "GET") summaryGetHits += 1;
        return jsonResponse({ detail: "no_summary" }, 404);
      }
      if (url.includes("/api/news")) return jsonResponse(NEWS_RESPONSE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    const groups = screen.getAllByRole("listitem");
    const aaplCard = groups.find((li) => within(li).queryByText("AAPL"));
    if (!aaplCard) throw new Error("AAPL card not found");

    // The disabled GET means the card shows Summarize immediately — no
    // spurious "Loading summary…" and no summary GET round-trip.
    expect(within(aaplCard).getByRole("button", { name: /^summarize$/i })).toBeInTheDocument();
    expect(within(aaplCard).queryByText(/loading summary/i)).not.toBeInTheDocument();
    expect(summaryGetHits).toBe(0);
  });

  it("renders an unavailable footnote when a symbol's news couldn't be fetched", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/news")) {
        return jsonResponse({ ...NEWS_RESPONSE, unavailable: ["XYZ"] });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    expect(screen.getByText(/couldn't fetch news for: XYZ/i)).toBeInTheDocument();
  });

  it("shows an empty state when there are no recent headlines", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/news")) {
        return jsonResponse({ groups: [], unavailable: [], as_of: hoursAgo(0) });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    expect(await screen.findByText(/no recent headlines for your holdings/i)).toBeInTheDocument();
  });

  it("fires a refresh POST then refetches the news query", async () => {
    let posted = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/news/refresh") && init?.method === "POST") {
        posted = true;
        return jsonResponse({ refreshed: ["AAPL", "MSFT"], unavailable: [] });
      }
      if (url.includes("/api/news")) {
        return jsonResponse(posted ? { ...NEWS_RESPONSE, as_of: hoursAgo(0) } : NEWS_RESPONSE);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/apple beats q3 estimates/i);
    await user.click(screen.getByRole("button", { name: /^refresh$/i }));

    expect(posted).toBe(true);
  });
});
