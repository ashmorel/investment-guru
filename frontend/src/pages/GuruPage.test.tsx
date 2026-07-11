import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import GuruPage from "./GuruPage";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DASHBOARD = {
  portfolios: [
    { id: 1, name: "Core Growth", kind: "real", base_currency: "GBP", total_value: "2600.00", day_change: "31.20", total_pnl_pct: "23.81" },
  ],
  as_of: "2026-07-09T08:00:00Z",
};

const TAKE = {
  id: 1,
  kind: "take",
  portfolio_id: null,
  payload: {
    commentary: "Steady week for the core book.",
    risks: [],
    ideas: [],
    disclaimer: "Not financial advice.",
  },
  model: "gpt-5",
  created_at: "2026-07-09T08:00:00Z",
};

const DIGEST = {
  id: 2,
  kind: "digest",
  portfolio_id: null,
  payload: {
    earnings_this_week: [{ symbol: "NVDA", date: "2026-07-10", note: "Reports Thursday." }],
    movers: [{ symbol: "TSLA", note: "Up 6% on delivery beat." }],
    news_flags: [{ symbol: null, headline: "Fed holds rates", comment: "No change expected next meeting." }],
    summary: "Quiet week overall.",
    disclaimer: "Digest is informational only.",
  },
  model: "gpt-5",
  created_at: "2026-07-09T07:00:00Z",
};

const REVIEW = {
  id: 5,
  kind: "review",
  portfolio_id: 1,
  payload: {
    positions: [
      { symbol: "NVDA", action: "reduce", conviction: "med", rationale: "Valuation is stretched." },
      { symbol: "AAPL", action: "hold", conviction: "high", rationale: "Steady compounder." },
    ],
    observations: ["Tech concentration remains elevated."],
    watch_next: ["NVDA earnings on Thursday."],
    disclaimer: "Review is not financial advice.",
  },
  model: "gpt-5",
  created_at: "2026-07-07T18:22:00Z",
};

function mockApi(opts?: { reviews?: unknown[]; onReviewPost?: () => void }) {
  const reviews = opts?.reviews ?? [REVIEW];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
    if (url.includes("/api/guru/digest/latest")) return jsonResponse(DIGEST);
    if (url.includes("/api/dashboard")) return jsonResponse(DASHBOARD);
    if (url.includes("/api/guru/reviews") && init?.method === "POST") {
      opts?.onReviewPost?.();
      return jsonResponse(REVIEW, 201);
    }
    if (url.includes("/api/guru/reviews")) return jsonResponse({ reviews });
    if (url.includes("/api/guru/chat/threads")) return jsonResponse({ threads: [] });
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GuruPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GuruPage", () => {
  it("renders digest sections from the latest digest", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText(/reports thursday/i)).toBeInTheDocument();
    expect(screen.getByText(/up 6% on delivery beat/i)).toBeInTheDocument();
    expect(screen.getByText(/fed holds rates/i)).toBeInTheDocument();
    expect(screen.getByText("EARNINGS")).toBeInTheDocument();
    expect(screen.getByText("MOVER")).toBeInTheDocument();
    expect(screen.getByText("NEWS")).toBeInTheDocument();
  });

  it("lists review history and reveals verdict chips as text on click", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    const historyItem = await screen.findByRole("button", { name: /core growth/i });
    expect(screen.queryByText(/REDUCE · MED/)).not.toBeInTheDocument();

    await user.click(historyItem);

    expect(await screen.findByText(/REDUCE · MED/)).toBeInTheDocument();
    expect(screen.getByText(/HOLD · HIGH/)).toBeInTheDocument();
    expect(screen.getByText(/tech concentration remains elevated/i)).toBeInTheDocument();
    expect(screen.getByText(/nvda earnings on thursday/i)).toBeInTheDocument();
  });

  it("POSTs a review run for the selected portfolio", async () => {
    let posted = false;
    mockApi({ onReviewPost: () => (posted = true) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/core growth/i);
    await user.click(screen.getByRole("button", { name: /run review/i }));

    expect(posted).toBe(true);
  });

  it("shows an already-generating message when running a review returns 409", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      if (url.includes("/api/guru/digest/latest")) return jsonResponse(DIGEST);
      if (url.includes("/api/dashboard")) return jsonResponse(DASHBOARD);
      if (url.includes("/api/guru/reviews") && init?.method === "POST") {
        return jsonResponse({ detail: "A review is already generating" }, 409);
      }
      if (url.includes("/api/guru/reviews")) return jsonResponse({ reviews: [REVIEW] });
      if (url.includes("/api/guru/chat/threads")) return jsonResponse({ threads: [] });
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/core growth/i);
    await user.click(screen.getByRole("button", { name: /run review/i }));

    expect(
      await screen.findByText(/already generating — check back shortly/i),
    ).toBeInTheDocument();
  });

  it("shows the daily-limit message when running a review returns 429 budget_exhausted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      if (url.includes("/api/guru/digest/latest")) return jsonResponse(DIGEST);
      if (url.includes("/api/dashboard")) return jsonResponse(DASHBOARD);
      if (url.includes("/api/guru/reviews") && init?.method === "POST") {
        return jsonResponse({ detail: "budget_exhausted" }, 429);
      }
      if (url.includes("/api/guru/reviews")) return jsonResponse({ reviews: [REVIEW] });
      if (url.includes("/api/guru/chat/threads")) return jsonResponse({ threads: [] });
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/core growth/i);
    await user.click(screen.getByRole("button", { name: /run review/i }));

    expect(
      await screen.findByText(/daily ai limit reached — resets tomorrow/i),
    ).toBeInTheDocument();
  });

  it("shows the daily-limit message when generating a digest returns 429 budget_exhausted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      if (url.includes("/api/guru/digest/latest")) return jsonResponse({ detail: "Not Found" }, 404);
      if (url.includes("/api/dashboard")) return jsonResponse(DASHBOARD);
      if (url.includes("/api/guru/digest") && init?.method === "POST") {
        return jsonResponse({ detail: "budget_exhausted" }, 429);
      }
      if (url.includes("/api/guru/reviews")) return jsonResponse({ reviews: [REVIEW] });
      if (url.includes("/api/guru/chat/threads")) return jsonResponse({ threads: [] });
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/no digest yet/i);
    await user.click(screen.getByRole("button", { name: /generate now/i }));

    expect(
      await screen.findByText(/daily ai limit reached — resets tomorrow/i),
    ).toBeInTheDocument();
  });

  it("shows the disclaimer on every rendered report", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/not financial advice\./i)).toBeInTheDocument();
    expect(screen.getByText(/digest is informational only/i)).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /core growth/i }));
    expect(await screen.findByText(/review is not financial advice/i)).toBeInTheDocument();
  });

  it("renders the Guru chat panel", async () => {
    mockApi();
    renderPage();
    expect(await screen.findByText(/chat with the guru/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new thread/i })).toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const user = userEvent.setup();
    const { container } = renderPage();

    await screen.findByText(/reports thursday/i);
    await user.click(await screen.findByRole("button", { name: /core growth/i }));
    await screen.findByText(/REDUCE · MED/);

    expect(await axe(container)).toHaveNoViolations();
  });
});
