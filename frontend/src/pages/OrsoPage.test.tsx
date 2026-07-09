import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import OrsoPage from "./OrsoPage";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const TODAY = "2026-07-09";
const STALE_DATE = "2026-06-20";

const OVERVIEW = {
  funds: [
    {
      id: 1,
      code: "HKEF",
      name: "Hong Kong Equity Fund",
      asset_class: "equity",
      risk_rating: 5,
      archived: false,
      units: "1000.0000",
      contribution_pct: "61.00",
      price: "477.0400",
      price_as_of: STALE_DATE,
      price_source: "hsbc",
      value_hkd: "477040.00",
    },
    {
      id: 2,
      code: "MMF",
      name: "Money Market Fund",
      asset_class: "cash",
      risk_rating: 1,
      archived: false,
      units: "500.0000",
      contribution_pct: "39.00",
      price: "130.9700",
      price_as_of: TODAY,
      price_source: "hsbc",
      value_hkd: "65485.00",
    },
  ],
  total_hkd: "542525.00",
  total_base: { currency: "GBP", value: "54252.50" },
  projection: [
    { rate: "0.02", projected_pot: "600000.00", on_track: false, gap: "-100000.00" },
    { rate: "0.05", projected_pot: "800000.00", on_track: true, gap: "50000.00" },
    { rate: "0.08", projected_pot: "1000000.00", on_track: true, gap: "200000.00" },
  ],
  flags: { stale: ["HKEF"], unpriced: [], split_sum_off: true, goals_incomplete: false },
  as_of: "2026-07-09T08:00:00Z",
};

const GOALS = {
  birth_year: 1985,
  retirement_target_age: 60,
  retirement_target_pot: "1000000.00",
  orso_monthly_contribution: "5000.00",
};

const ADVICE_REPORT = {
  id: 9,
  kind: "orso",
  portfolio_id: null,
  payload: {
    fund_verdicts: [
      { code: "HKEF", action: "reduce", conviction: "med", rationale: "Concentration risk." },
      { code: "MMF", action: "keep", conviction: "high", rationale: "Stable cash buffer." },
    ],
    switch_plan: [{ from_code: "HKEF", to_code: "ISF", note: "De-risk into a stable fund." }],
    projection_comment: "Stay the course on contributions.",
    watch: ["HKEF concentration"],
    disclaimer: "The Guru is not regulated financial advice.",
  },
  model: "gpt-5",
  created_at: "2026-07-09T08:05:00Z",
};

const SWITCH_LOG = {
  entries: [{ id: 3, changed_at: "2026-07-08T10:00:00Z", note: "Rebalanced into MMF" }],
};

function mockApi(overrides?: {
  onAllocationPut?: (body: unknown) => void;
  onManualPricePut?: (body: unknown) => void;
  onRefresh?: () => Response | Promise<Response>;
  onAdvicePost?: () => Response | Promise<Response>;
  onThreadPost?: (body: unknown) => void;
  adviceReports?: unknown[];
}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url.includes("/api/orso/overview")) return jsonResponse(OVERVIEW);
    if (url.includes("/api/orso/goals") && method === "GET") return jsonResponse(GOALS);
    if (url.includes("/api/orso/goals") && method === "PUT") {
      return jsonResponse({ ...GOALS, ...JSON.parse(String(init?.body)) });
    }
    if (url.includes("/api/orso/switchlog")) return jsonResponse(SWITCH_LOG);
    if (url.includes("/api/orso/allocation") && method === "PUT") {
      overrides?.onAllocationPut?.(JSON.parse(String(init?.body)));
      return jsonResponse({ allocations: [], switched: true });
    }
    if (url.includes("/api/orso/prices/manual") && method === "PUT") {
      overrides?.onManualPricePut?.(JSON.parse(String(init?.body)));
      return jsonResponse({ fund_id: 1, price: "480.00", as_of: TODAY, source: "manual", fetched_at: TODAY });
    }
    if (url.includes("/api/orso/prices/refresh") && method === "POST") {
      return overrides?.onRefresh
        ? overrides.onRefresh()
        : jsonResponse({ refreshed: [1, 2], unavailable: false });
    }
    if (url.includes("/api/orso/advice") && method === "POST") {
      return overrides?.onAdvicePost ? overrides.onAdvicePost() : jsonResponse(ADVICE_REPORT, 201);
    }
    if (url.includes("/api/orso/advice")) {
      return jsonResponse({ reports: overrides?.adviceReports ?? [ADVICE_REPORT] });
    }
    if (url.includes("/api/guru/chat/threads") && method === "POST") {
      overrides?.onThreadPost?.(JSON.parse(String(init?.body)));
      return jsonResponse({ id: 42, title: "ORSO switching advice", portfolio_id: null, scope: "orso", created_at: TODAY }, 201);
    }
    throw new Error(`Unexpected fetch: ${url} ${method}`);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/orso"]}>
        <Routes>
          <Route path="/orso" element={<OrsoPage />} />
          <Route path="/guru" element={<div>Guru home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OrsoPage", () => {
  it("renders the overview table with fund values, a stale badge, and the split-sum warning", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText("HKEF")).toBeInTheDocument();
    expect(screen.getByText("MMF")).toBeInTheDocument();
    expect(screen.getByText(/477,040\.00/)).toBeInTheDocument();
    expect(screen.getByText(/⚠ stale/)).toBeInTheDocument();
    expect(screen.getByText(/does not add up to 100/i)).toBeInTheDocument();
    expect(screen.getByText(/542,525\.00/)).toBeInTheDocument();
  });

  it("posts a full-replace allocation PUT body when saving an edit", async () => {
    let posted: unknown = null;
    mockApi({ onAllocationPut: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("HKEF");
    await user.click(screen.getByRole("button", { name: /edit allocation/i }));

    const unitsInput = screen.getByLabelText(/HKEF units/i);
    await user.clear(unitsInput);
    await user.type(unitsInput, "1200");

    await user.click(screen.getByRole("button", { name: /save allocation/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted as { allocations: { fund_id: number; units: string; contribution_pct: string }[] };
    expect(body.allocations).toHaveLength(2);
    const hkef = body.allocations.find((a) => a.fund_id === 1);
    expect(hkef?.units).toBe("1200");
    const mmf = body.allocations.find((a) => a.fund_id === 2);
    expect(mmf?.units).toBe("500.0000");
  });

  it("PUTs a manual price entry with fund_id, price, and as_of", async () => {
    let posted: unknown = null;
    mockApi({ onManualPricePut: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("HKEF");
    await user.click(screen.getByRole("button", { name: /477\.04/i }));

    const priceInput = screen.getByLabelText(/^price$/i);
    await user.clear(priceInput);
    await user.type(priceInput, "480.50");
    await user.click(screen.getByRole("button", { name: /save price/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ fund_id: 1, price: "480.50" });
    expect((posted as { as_of: string }).as_of).toBeTruthy();
  });

  it("disables the refresh button and shows a manual-prices note after an unavailable response", async () => {
    mockApi({ onRefresh: () => jsonResponse({ refreshed: [], unavailable: true }) });
    const user = userEvent.setup();
    renderPage();

    const refreshButton = await screen.findByRole("button", { name: /refresh prices/i });
    expect(refreshButton).toBeEnabled();

    await user.click(refreshButton);

    await waitFor(() => expect(refreshButton).toBeDisabled());
    expect(screen.getByText(/manual prices only/i)).toBeInTheDocument();
  });

  it("saves goals via PUT and renders on/off-track projection bars", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("HKEF");
    expect(screen.getByText(/short/i)).toBeInTheDocument();
    expect(screen.getAllByText(/on track/i).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /edit goals/i }));
    const targetPotInput = screen.getByLabelText(/target pot/i);
    await user.clear(targetPotInput);
    await user.type(targetPotInput, "1200000");
    await user.click(screen.getByRole("button", { name: /save goals/i }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /save goals/i })).not.toBeInTheDocument());
  });

  it("renders switching advice verdict chips (including keep), switch plan, and disclaimer", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText("HKEF", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/KEEP · HIGH/)).toBeInTheDocument();
    expect(screen.getByText(/REDUCE · MED/)).toBeInTheDocument();
    expect(screen.getByText(/de-risk into a stable fund/i)).toBeInTheDocument();
    expect(screen.getByText(/not regulated financial advice/i)).toBeInTheDocument();
  });

  it("shows the unconfigured banner when generating advice returns 503", async () => {
    mockApi({
      adviceReports: [],
      onAdvicePost: () => jsonResponse({ detail: "llm_unconfigured" }, 503),
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/no advice yet/i);
    await user.click(screen.getByRole("button", { name: /get switching advice/i }));

    expect(await screen.findByText(/isn't configured yet/i)).toBeInTheDocument();
  });

  it("shows an already-generating message when generating advice returns 409", async () => {
    mockApi({
      adviceReports: [],
      onAdvicePost: () => jsonResponse({ detail: "generation_in_progress" }, 409),
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/no advice yet/i);
    await user.click(screen.getByRole("button", { name: /get switching advice/i }));

    expect(await screen.findByText(/already generating/i)).toBeInTheDocument();
  });

  it("creates a scope=orso chat thread and navigates to /guru on discuss", async () => {
    let posted: unknown = null;
    mockApi({ onThreadPost: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/de-risk into a stable fund/i);
    await user.click(screen.getByRole("button", { name: /discuss in chat/i }));

    await screen.findByText(/guru home/i);
    expect(posted).toMatchObject({ scope: "orso" });
  });

  it("renders the switch log", async () => {
    mockApi();
    renderPage();
    expect(await screen.findByText(/rebalanced into mmf/i)).toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const { container } = renderPage();
    await screen.findByText("HKEF");
    await screen.findByText(/de-risk into a stable fund/i);
    expect(await axe(container)).toHaveNoViolations();
  });
});
