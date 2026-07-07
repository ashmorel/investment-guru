import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import PortfoliosPage from "./PortfoliosPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PortfoliosPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PortfoliosPage", () => {
  it("lists portfolios from the API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          { id: 1, name: "Growth", kind: "real", base_currency: "GBP", position_count: 3 },
          { id: 2, name: "Watch", kind: "watchlist", base_currency: "HKD", position_count: 5 },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderPage();
    expect(await screen.findByText("Growth")).toBeInTheDocument();
    expect(screen.getByText("Watch")).toBeInTheDocument();
    expect(screen.getByText(/watchlist/i)).toBeInTheDocument();
  });
});
