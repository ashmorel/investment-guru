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
      if (url === "/api/guru/decision-brief/latest") {
        return new Response(JSON.stringify(null), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/portfolios") {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/take/latest")) {
        return new Response(JSON.stringify({ detail: "not found" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
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
      if (url.includes("/api/news")) {
        return new Response(
          JSON.stringify({ groups: [], unavailable: [], as_of: "2026-07-07T09:00:00Z" }),
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
    expect(screen.getAllByRole("heading", { level: 1 })).toEqual([
      screen.getByRole("heading", { name: "Dashboard", level: 1 }),
    ]);
    expect(screen.getByRole("heading", { name: "Decision Cockpit", level: 2 })).toBeInTheDocument();
    expect(screen.getByText(/2,600\.00/)).toBeInTheDocument();
    expect(screen.getByText(/Guru's take/)).toBeInTheDocument();
  });

  it("surfaces an unavailable-inputs notice after Run analysis reports a down feed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") {
        return new Response(JSON.stringify(null), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/portfolios") {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
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
      if (url.includes("/api/guru/take/latest")) {
        return new Response(JSON.stringify({ detail: "not found" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
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
      if (url.includes("/api/news")) {
        return new Response(
          JSON.stringify({ groups: [], unavailable: [], as_of: "2026-07-07T09:00:00Z" }),
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

  it("shows an error message when Run analysis fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") {
        return new Response(JSON.stringify(null), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/portfolios") {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/analyze") && init?.method === "POST") {
        throw new Error("network down");
      }
      if (url.includes("/api/guru/take/latest")) {
        return new Response(JSON.stringify({ detail: "not found" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
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
      if (url.includes("/api/news")) {
        return new Response(
          JSON.stringify({ groups: [], unavailable: [], as_of: "2026-07-07T09:00:00Z" }),
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
    expect(await screen.findByText(/analysis failed/i)).toBeInTheDocument();
  });

  it("unions unavailable inputs across multiple portfolios", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") {
        return new Response(JSON.stringify(null), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/portfolios") {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/portfolios/1/analyze") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ signals: [], as_of: "2026-07-07T09:00:00Z", unavailable_inputs: ["news"] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/portfolios/2/analyze") && init?.method === "POST") {
        return new Response(
          JSON.stringify({ signals: [], as_of: "2026-07-07T09:00:00Z", unavailable_inputs: ["history"] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/api/guru/take/latest")) {
        return new Response(JSON.stringify({ detail: "not found" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
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
              {
                id: 2, name: "Income", kind: "real", base_currency: "GBP",
                total_value: "1200.00", day_change: "5.00", total_pnl_pct: "2.10",
              },
            ],
            as_of: "2026-07-07T09:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/api/news")) {
        return new Response(
          JSON.stringify({ groups: [], unavailable: [], as_of: "2026-07-07T09:00:00Z" }),
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
    const notice = await screen.findByText(/some data was unavailable/i);
    expect(notice).toHaveTextContent("news");
    expect(notice).toHaveTextContent("history");
  });

  it("puts holding actions before portfolio cards and detailed news", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") {
        return new Response(JSON.stringify({
          id: 9,
          kind: "decision",
          portfolio_id: null,
          model: "claude-advice",
          created_at: "2026-07-14T02:32:00Z",
          payload: {
            summary: "Concentration needs attention.",
            holdings: [{
              symbol: "TSLA", action: "reduce", conviction: "high",
              rationale: "Valuation pressure is elevated.", evidence_refs: [], change_conditions: [],
            }],
            material_news: [],
            portfolio_observations: [],
            candidates: [],
            unavailable_inputs: [],
            data_as_of: "2026-07-14T02:18:00Z",
            disclaimer: "Not regulated financial advice.",
          },
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === "/api/portfolios") {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/analyze") && init?.method === "POST") {
        return new Response(JSON.stringify({ signals: [], unavailable_inputs: ["fundamentals"], as_of: "2026-07-14T02:18:00Z" }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/guru/take/latest")) {
        return new Response(JSON.stringify({ detail: "not found" }), {
          status: 404, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/dashboard/attention")) {
        return new Response(JSON.stringify({ signals: [] }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/dashboard")) {
        return new Response(JSON.stringify({
          portfolios: [{
            id: 1, name: "Growth", kind: "real", base_currency: "GBP",
            total_value: "2600.00", day_change: "31.20", total_pnl_pct: "23.81",
          }],
          as_of: "2026-07-07T09:00:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/api/news")) {
        return new Response(JSON.stringify({
          groups: [{
            symbol: "TSLA", name: "Tesla", activity_score: 1, summary_available: false,
            items: [{
              title: "Tesla delivery outlook changes", url: "https://example.com/tesla",
              source: "Reuters", published_at: "2026-07-14T01:00:00Z",
            }],
          }],
          unavailable: [], as_of: "2026-07-14T02:18:00Z",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><DashboardPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    const actions = await screen.findByRole("heading", { name: "Actions across your holdings" });
    const portfolio = screen.getByRole("heading", { name: "Growth" });
    const news = await screen.findByRole("heading", { name: "News" });
    expect(actions.compareDocumentPosition(portfolio) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(portfolio.compareDocumentPosition(news) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Tesla delivery outlook changes")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(await screen.findByText(/some data was unavailable: fundamentals/i)).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/portfolios/1/analyze",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
