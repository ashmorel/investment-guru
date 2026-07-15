import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import DecisionCockpit from "./DecisionCockpit";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const REPORT = {
  id: 9,
  kind: "decision",
  portfolio_id: null,
  model: "claude-advice",
  created_at: "2026-07-14T02:32:00Z",
  payload: {
    summary: "Concentration needs attention while defensive exposure is light.",
    holdings: [
      {
        symbol: "MSFT",
        action: "hold",
        conviction: "high",
        rationale: "Operating evidence remains strong.",
        evidence_refs: ["signal:MSFT"],
        change_conditions: ["Margins weaken"],
      },
      {
        symbol: "TSLA",
        action: "reduce",
        conviction: "high",
        rationale: "Valuation pressure outweighs the long-term theme.",
        evidence_refs: ["news:tesla-review", "signal:TSLA"],
        change_conditions: ["Improving trend", "Resolved regulatory uncertainty"],
      },
      {
        symbol: "LLOY.L",
        action: "data_incomplete",
        conviction: null,
        rationale: "Current fundamentals unavailable; no verdict inferred.",
        evidence_refs: [],
        change_conditions: [],
      },
    ],
    material_news: [
      {
        evidence_ref: "news:tesla-review",
        symbol: "TSLA",
        importance: "material",
        headline: "Regulator expands review of driver-assistance claims",
        source: "Reuters",
        url: "https://example.com/tesla-review",
        impact: "Changes the downside case behind REDUCE.",
      },
    ],
    portfolio_observations: ["Technology is 46% of priced holdings."],
    candidates: [
      {
        symbol: "VIG",
        name: "Vanguard Dividend Appreciation ETF",
        instrument_type: "etf",
        market: "US",
        action: "consider",
        conviction: "high",
        why_surfaced: "Quality dividend screen",
        portfolio_fit: "Diversifies concentrated growth.",
        principal_risk: "Rate-sensitive.",
        watch_next: ["Earnings breadth"],
        evidence_refs: ["screen:quality"],
      },
    ],
    unavailable_inputs: ["LLOY.L fundamentals"],
    data_as_of: "2026-07-14T02:18:00Z",
    disclaimer: "The Guru is not regulated financial advice.",
  },
};

const PORTFOLIOS = [
  { id: 1, name: "Core", kind: "real", base_currency: "GBP", position_count: 3 },
  { id: 2, name: "Ideas", kind: "watchlist", base_currency: "GBP", position_count: 1 },
];

function mockFetch(latest: unknown = REPORT) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/api/guru/decision-brief/latest") return jsonResponse(latest);
    if (url === "/api/portfolios") return jsonResponse(PORTFOLIOS);
    throw new Error(`Unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

function renderCockpit() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DecisionCockpit />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("DecisionCockpit", () => {
  it("uses a level-two heading when embedded beneath the dashboard title", async () => {
    mockFetch(null);
    renderCockpit();

    expect(await screen.findByRole("heading", { name: "Decision Cockpit", level: 2 })).toBeInTheDocument();
  });

  it("renders the empty state when there is no saved brief", async () => {
    mockFetch(null);
    renderCockpit();

    expect(await screen.findByText("No Decision Brief yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate first brief" })).toBeInTheDocument();
  });

  it("renders actionable holdings before a collapsed Hold group and labels incomplete data", async () => {
    mockFetch();
    renderCockpit();

    const act = await screen.findByText(/Act · 1 holding needs a decision/);
    const hold = screen.getByText("Hold · 1 holding");
    expect(act.compareDocumentPosition(hold) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("REDUCE")).toBeInTheDocument();
    expect(screen.getByText("DATA INCOMPLETE")).toBeInTheDocument();
    expect(hold.closest("details")).not.toHaveAttribute("open");
  });

  it("expands semantic evidence and renders the matching safe source link", async () => {
    mockFetch();
    const user = userEvent.setup();
    renderCockpit();

    const teslaRow = (await screen.findByRole("heading", { name: "TSLA" })).closest("article");
    if (!teslaRow) throw new Error("TSLA decision missing");
    await user.click(within(teslaRow).getByText("View evidence"));
    expect(within(teslaRow).getByText(/Would change:/)).toBeInTheDocument();
    const link = within(teslaRow).getByRole("link", { name: /Regulator expands review/ });
    expect(link).toHaveAttribute("href", "https://example.com/tesla-review");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("renders candidate details and adds the symbol to a selected watchlist", async () => {
    const fetchSpy = mockFetch();
    fetchSpy.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") return jsonResponse(REPORT);
      if (url === "/api/portfolios") return jsonResponse(PORTFOLIOS);
      if (url === "/api/portfolios/2/positions" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({ symbol: "VIG", quantity: null });
        return jsonResponse({ id: 44, symbol: "VIG" }, 201);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderCockpit();

    const card = (await screen.findByText("VIG")).closest("article");
    if (!card) throw new Error("candidate card missing");
    expect(within(card).getByText("Vanguard Dividend Appreciation ETF")).toBeInTheDocument();
    expect(within(card).getByText("Quality dividend screen")).toBeInTheDocument();
    for (const item of REPORT.payload.candidates[0].watch_next) {
      expect(within(card).getByText(item)).toBeInTheDocument();
    }
    await user.selectOptions(within(card).getByRole("combobox", { name: /watchlist/i }), "2");
    await user.click(within(card).getByRole("button", { name: /add VIG to watchlist/i }));
    expect(await within(card).findByText("Added to Ideas")).toBeInTheDocument();
  });

  it("generates a brief and updates the latest cache without another GET", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/guru/decision-brief/latest") return jsonResponse(null);
      if (url === "/api/portfolios") return jsonResponse(PORTFOLIOS);
      if (url === "/api/guru/decision-brief" && init?.method === "POST") {
        return jsonResponse(REPORT, 201);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderCockpit();

    await user.click(await screen.findByRole("button", { name: "Generate first brief" }));
    expect(await screen.findByText(REPORT.payload.summary)).toBeInTheDocument();
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith("/latest"))).toHaveLength(1);
  });

  it.each([
    [429, "Daily Guru budget reached"],
    [409, "Already generating — check back shortly."],
    [502, "Brief generation failed"],
    [503, "Guru is not configured"],
  ])("shows the approved %s generation error copy", async (status, copy) => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/latest")) return jsonResponse(null);
      if (url === "/api/portfolios") return jsonResponse(PORTFOLIOS);
      if (url === "/api/guru/decision-brief" && init?.method === "POST") {
        return jsonResponse({ detail: "failure" }, status);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderCockpit();

    await user.click(await screen.findByRole("button", { name: "Generate first brief" }));
    expect(await screen.findByText(copy)).toBeInTheDocument();
  });

  it("has no obvious accessibility violations", async () => {
    mockFetch();
    const { container } = renderCockpit();
    await screen.findByText(REPORT.payload.summary);
    expect(await axe(container)).toHaveNoViolations();
  });
});
