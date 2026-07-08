import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DashboardPage from "./DashboardPage";

describe("DashboardPage", () => {
  it("renders portfolio cards with values", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/dashboard/attention")) {
        return new Response(JSON.stringify({ signals: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/dashboard")) {
        return new Response(
          JSON.stringify({
            portfolios: [
              {
                id: 1, name: "Growth", kind: "real", base_currency: "GBP",
                total_value: "2600.00", day_change: "31.20", total_pnl_pct: "23.81",
              },
            ],
            as_of: "2026-07-07T09:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Growth")).toBeInTheDocument();
    expect(screen.getByText(/2,600\.00/)).toBeInTheDocument();
    expect(screen.getByText(/Guru's take/)).toBeInTheDocument();
  });

  it("surfaces an unavailable-inputs notice after Run analysis reports a down feed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/analyze") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            signals: [],
            as_of: "2026-07-07T09:00:00Z",
            unavailable_inputs: ["news"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/dashboard/attention")) {
        return new Response(JSON.stringify({ signals: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/dashboard")) {
        return new Response(
          JSON.stringify({
            portfolios: [
              {
                id: 1, name: "Growth", kind: "real", base_currency: "GBP",
                total_value: "2600.00", day_change: "31.20", total_pnl_pct: "23.81",
              },
            ],
            as_of: "2026-07-07T09:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /run analysis/i }));
    expect(await screen.findByText(/some data was unavailable: news/i)).toBeInTheDocument();
  });
});
