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
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
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
      if (url.includes("/valuation")) {
        return new Response(JSON.stringify(VALUATION), {
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
});
