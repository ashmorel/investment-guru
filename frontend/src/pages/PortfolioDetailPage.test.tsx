import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import PortfolioDetailPage from "./PortfolioDetailPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/portfolios/1"]}>
        <Routes>
          <Route path="/portfolios/:id" element={<PortfolioDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const VALUATION = {
  portfolio_id: 1,
  base_currency: "GBP",
  total_value: "100.00",
  total_cost: "90.00",
  total_pnl: "10.00",
  total_pnl_pct: "11.11",
  day_change: "1.00",
  currency_exposure: {},
  priced_positions: 1,
  unpriced_positions: 0,
  positions: [
    {
      position_id: 1,
      symbol: "AAPL",
      name: "Apple Inc.",
      market: "US",
      quantity: "10",
      avg_cost: "9",
      native_currency: "USD",
      price: "10",
      market_value_base: "100.00",
      cost_basis_base: "90.00",
      unrealized_pnl_base: "10.00",
      unrealized_pnl_pct: "11.11",
      day_change_base: "1.00",
      quote_as_of: null,
    },
  ],
};

describe("PortfolioDetailPage", () => {
  it("surfaces the server's 409 detail when adding a duplicate position", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/instruments/lookup")) {
        return new Response(JSON.stringify({ known: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/positions") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            detail: "AAPL is already in this portfolio — edit the existing position",
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/AAPL/i), "AAPL");
    await userEvent.click(screen.getByRole("button", { name: /add position/i }));

    expect(
      await screen.findByText(/already in this portfolio — edit the existing position/i),
    ).toBeInTheDocument();
  });

  it("falls back to generic copy when the lookup itself fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/instruments/lookup")) {
        return new Response(JSON.stringify({ detail: "Symbol NOPE not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    await userEvent.type(await screen.findByPlaceholderText(/AAPL/i), "NOPE");
    await userEvent.click(screen.getByRole("button", { name: /add position/i }));

    expect(await screen.findByText(/symbol not recognised/i)).toBeInTheDocument();
  });

  it("surfaces an unavailable-inputs notice after Run analysis reports a down feed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/analyze") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            signals: [],
            as_of: "2026-07-08T00:00:00Z",
            unavailable_inputs: ["news"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /run analysis/i }));

    expect(await screen.findByText(/some data was unavailable: news/i)).toBeInTheDocument();
  });

  it("renders a badge for a position-scoped signal and excludes portfolio-level signals", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/signals")) {
        return new Response(
          JSON.stringify({
            signals: [
              {
                id: 1,
                instrument_id: 1,
                symbol: "AAPL",
                kind: "price_move_day",
                severity: "watch",
                title: "AAPL moved today",
                detail: "AAPL is up 3.2% today",
                data: { pct: "3.2" },
                computed_at: "2026-07-08T00:00:00Z",
              },
              {
                id: 2,
                instrument_id: null,
                symbol: null,
                kind: "news_recent",
                severity: "info",
                title: "Portfolio-wide news",
                detail: "General market news affecting the portfolio",
                data: {},
                computed_at: "2026-07-08T00:00:00Z",
              },
            ],
            computed_at: "2026-07-08T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/instruments/lookup")) {
        return new Response(JSON.stringify({ known: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();

    expect(await screen.findByText("3.2% today")).toBeInTheDocument();
    expect(screen.queryByTitle("Portfolio-wide news")).not.toBeInTheDocument();
    expect(screen.queryByText("news")).not.toBeInTheDocument();
  });

  it("shows the Guru's take chip and rationale when a review covers the position", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(
          JSON.stringify({
            reviews: [
              {
                id: 9,
                kind: "review",
                portfolio_id: 1,
                payload: {
                  positions: [
                    { symbol: "AAPL", action: "hold", conviction: "high", rationale: "Steady compounder." },
                  ],
                  observations: [],
                  watch_next: [],
                  disclaimer: "Not financial advice.",
                },
                model: "gpt-5",
                created_at: "2026-07-08T09:00:00Z",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();

    expect(await screen.findByText(/HOLD · HIGH/)).toBeInTheDocument();
    expect(screen.getByText(/steady compounder/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /ask in chat/i })).toBeInTheDocument();
  });

  it("shows a distinct unavailable message when the reviews fetch fails, without blocking the table", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ detail: "internal error" }), {
          status: 500, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();

    expect(await screen.findByText(/guru take unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/no take yet — run a review/i)).not.toBeInTheDocument();
    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("Apple Inc.")).toBeInTheDocument();
  });

  it("shows a fallback message when no review covers the position yet", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();

    expect(await screen.findByText(/no take yet — run a review/i)).toBeInTheDocument();
  });

  it("lazily loads per-position news only once the row is expanded", async () => {
    let newsRequested = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/news/AAPL/summary")) {
        return new Response(JSON.stringify({ detail: "no_summary" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/news/AAPL")) {
        newsRequested = true;
        return new Response(
          JSON.stringify({
            symbol: "AAPL",
            name: "Apple Inc.",
            items: [
              { title: "Apple beats Q3 estimates", source: "Reuters", url: "https://example.com/a",
                published_at: "2026-07-08T00:00:00Z" },
            ],
            as_of: "2026-07-08T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/signals")) {
        return new Response(JSON.stringify({ signals: [], computed_at: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/reviews")) {
        return new Response(JSON.stringify({ reviews: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    await screen.findByText("AAPL");
    expect(newsRequested).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: /^news$/i }));

    expect(await screen.findByText(/apple beats q3 estimates/i)).toBeInTheDocument();
    expect(newsRequested).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /hide news/i }));
    expect(screen.queryByText(/apple beats q3 estimates/i)).not.toBeInTheDocument();
  });
});
