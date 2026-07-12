import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import SectorsPage from "./SectorsPage";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const TECH_GROUP = { id: 1, name: "Tech", color: "#4f46e5", sort_order: 0, holding_count: 1 };
const ENERGY_GROUP = { id: 2, name: "Energy", color: "", sort_order: 1, holding_count: 1 };

const PORTFOLIOS = [
  { id: 1, name: "Growth", kind: "real", base_currency: "GBP", position_count: 2 },
];

const POSITIONS_P1 = [
  { id: 1, symbol: "AAPL", name: "Apple", market: "US", currency: "USD", quantity: "10", avg_cost: "100", notes: null },
  { id: 2, symbol: "XOM", name: "Exxon Mobil", market: "US", currency: "USD", quantity: "5", avg_cost: "80", notes: null },
];

const EXPOSURE = {
  groups: [
    { group_id: 1, name: "Tech", color: "#4f46e5", value_base: "700.00", pct: "70.00", day_change_base: "12.00" },
    { group_id: null, name: "Ungrouped", color: "", value_base: "300.00", pct: "30.00", day_change_base: "-5.00" },
  ],
  total_base: "1000.00",
  unpriced: [],
  as_of: "2026-07-12T08:00:00Z",
};

const TREND = {
  series: [
    {
      group_id: 1,
      name: "Tech",
      color: "#4f46e5",
      points: [{ as_of: "2026-07-11", value_base: "690.00", pct: "69.00" }],
    },
  ],
  as_of: "2026-07-12T08:00:00Z",
};

function mockApi(overrides?: {
  groups?: unknown[];
  groupsAfterSeed?: unknown[];
  exposure?: unknown;
  onSeedPost?: () => Response | Promise<Response>;
  onAssignPut?: (body: unknown) => void;
  onCreatePost?: (body: unknown) => void;
  onPatch?: (id: string, body: unknown) => void;
  onDelete?: (id: string) => void;
}) {
  let seeded = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url.includes("/api/groups/seed-from-sectors") && method === "POST") {
      seeded = true;
      return overrides?.onSeedPost ? overrides.onSeedPost() : jsonResponse({ created: ["Energy"], assigned: 1 }, 200);
    }
    if (url.includes("/api/groups/assign") && method === "PUT") {
      const body = JSON.parse(String(init?.body));
      overrides?.onAssignPut?.(body);
      return jsonResponse({ symbol: body.symbol, group_id: body.group_id });
    }
    if (url.includes("/api/groups/exposure")) {
      return jsonResponse(overrides?.exposure ?? EXPOSURE);
    }
    if (url.includes("/api/groups/trend")) {
      return jsonResponse(TREND);
    }
    if (url.match(/\/api\/groups\/\d+$/) && method === "PATCH") {
      const id = url.match(/\/api\/groups\/(\d+)$/)?.[1] ?? "";
      const body = JSON.parse(String(init?.body));
      overrides?.onPatch?.(id, body);
      return jsonResponse({ ...TECH_GROUP, ...body });
    }
    if (url.match(/\/api\/groups\/\d+$/) && method === "DELETE") {
      const id = url.match(/\/api\/groups\/(\d+)$/)?.[1] ?? "";
      overrides?.onDelete?.(id);
      return new Response(null, { status: 204 });
    }
    if (url.endsWith("/api/groups") && method === "POST") {
      const body = JSON.parse(String(init?.body));
      overrides?.onCreatePost?.(body);
      return jsonResponse({ id: 3, name: body.name, color: body.color ?? "", sort_order: 2, holding_count: 0 }, 201);
    }
    if (url.endsWith("/api/groups") && method === "GET") {
      if (seeded && overrides?.groupsAfterSeed) return jsonResponse(overrides.groupsAfterSeed);
      return jsonResponse(overrides?.groups ?? [TECH_GROUP]);
    }
    if (url.includes("/api/portfolios/1/positions")) return jsonResponse(POSITIONS_P1);
    if (url.endsWith("/api/portfolios")) return jsonResponse(PORTFOLIOS);
    throw new Error(`Unexpected fetch: ${url} ${method}`);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SectorsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SectorsPage", () => {
  it("renders groups, holdings, and exposure bars with value/pct/total", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText("Tech", { selector: "span.flex-1" })).toBeInTheDocument();
    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("XOM")).toBeInTheDocument();
    expect(screen.getByText(/700\.00/)).toBeInTheDocument();
    expect(screen.getByText(/70\.00%/)).toBeInTheDocument();
    expect(screen.getByText("Ungrouped", { selector: "span.text-text" })).toBeInTheDocument();
    expect(screen.getByText(/1,000\.00/)).toBeInTheDocument();
  });

  it("clicking Seed from sectors calls the seed endpoint and refetches groups", async () => {
    mockApi({ groups: [TECH_GROUP], groupsAfterSeed: [TECH_GROUP, ENERGY_GROUP] });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    expect(screen.queryByText("Energy", { selector: "span.flex-1" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /seed from sectors/i }));

    expect(await screen.findByText("Energy", { selector: "span.flex-1" })).toBeInTheDocument();
  });

  it("changing a holding's group select calls assignGroup with the symbol and group id", async () => {
    let posted: unknown = null;
    mockApi({ onAssignPut: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("AAPL");
    const select = screen.getByLabelText(/AAPL group/i);
    await user.selectOptions(select, "Tech");

    await waitFor(() => expect(posted).toEqual({ symbol: "AAPL", group_id: 1 }));
  });

  it("clearing a holding's group select assigns it back to Ungrouped (null)", async () => {
    let posted: unknown = null;
    mockApi({ onAssignPut: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("AAPL");
    const select = screen.getByLabelText(/AAPL group/i);
    await user.selectOptions(select, "Tech");
    await waitFor(() => expect(posted).toEqual({ symbol: "AAPL", group_id: 1 }));
    await user.selectOptions(select, "Ungrouped");
    await waitFor(() => expect(posted).toEqual({ symbol: "AAPL", group_id: null }));
  });

  it("creating a new group calls createGroup with the entered name", async () => {
    let posted: unknown = null;
    mockApi({ onCreatePost: (body) => (posted = body) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    await user.type(screen.getByLabelText(/^new group$/i), "Space");
    await user.click(screen.getByRole("button", { name: /add group/i }));

    await waitFor(() => expect(posted).toMatchObject({ name: "Space" }));
  });

  it("renames a group via the inline rename form", async () => {
    let patched: { id: string; body: unknown } | null = null;
    mockApi({ onPatch: (id, body) => (patched = { id, body }) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    await user.click(screen.getByRole("button", { name: /rename tech/i }));
    const input = screen.getByLabelText(/rename tech/i);
    await user.clear(input);
    await user.type(input, "Technology");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(patched).toMatchObject({ id: "1", body: { name: "Technology" } }));
  });

  it("deletes a group", async () => {
    let deletedId: string | null = null;
    mockApi({ onDelete: (id) => (deletedId = id) });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    await user.click(screen.getByRole("button", { name: /delete tech/i }));

    await waitFor(() => expect(deletedId).toBe("1"));
  });

  it("renders the trend chart from getGroupTrend", async () => {
    mockApi();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    const svg = await screen.findByRole("img");
    expect(svg.querySelector("title")).toHaveTextContent(/trend/i);
    expect(svg.querySelectorAll("polyline")).toHaveLength(1);
  });

  it("shows the unpriced note when the exposure response has unpriced holdings", async () => {
    mockApi({ exposure: { ...EXPOSURE, unpriced: ["XOM"] } });
    renderPage();

    expect(await screen.findByText(/1 holding unpriced/i)).toBeInTheDocument();
  });

  it("re-fetches exposure scoped to the selected portfolio", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech", { selector: "span.flex-1" });
    const select = screen.getByLabelText(/^portfolio$/i);
    await user.selectOptions(select, "Growth");

    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
      expect(calls.some((c) => String(c[0]).includes("/api/groups/exposure?portfolio_id=1"))).toBe(true);
    });
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const { container } = renderPage();
    await screen.findByText("Tech", { selector: "span.flex-1" });
    await screen.findByRole("img");
    expect(await axe(container)).toHaveNoViolations();
  });
});
