import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import type { OrsoFundOut } from "../../lib/types";
import FundSearch from "./FundSearch";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const FUNDS: OrsoFundOut[] = [
  { id: 10, code: "HKEF", name: "Hong Kong Equity Fund", asset_class: "equity", risk_rating: 5, archived: false, currency: "HKD" },
  { id: 11, code: "NAEF", name: "North American Equity", asset_class: "equity", risk_rating: 5, archived: false, currency: "USD" },
  { id: 12, code: "WBF", name: "World Bond Fund", asset_class: "bond", risk_rating: 3, archived: true, currency: "HKD" },
];

function mockApi() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/orso/funds/search") {
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const filtered = q
        ? FUNDS.filter((f) => f.code.toLowerCase().includes(q) || f.name.toLowerCase().includes(q))
        : FUNDS;
      return jsonResponse(filtered);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

function renderSearch(onSelect: (fund: OrsoFundOut) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FundSearch onSelect={onSelect} />
    </QueryClientProvider>,
  );
}

describe("FundSearch", () => {
  it("lists the scheme's funds (including archived) and calls onSelect when one is added", async () => {
    mockApi();
    const onSelect = vi.fn();
    const user = userEvent.setup();
    renderSearch(onSelect);

    expect(await screen.findByText("Hong Kong Equity Fund")).toBeInTheDocument();
    expect(screen.getByText("World Bond Fund")).toBeInTheDocument();
    expect(screen.getByText("(archived)")).toBeInTheDocument();

    const addButtons = screen.getAllByRole("button", { name: /add to allocation/i });
    await user.click(addButtons[0]);

    expect(onSelect).toHaveBeenCalledWith(FUNDS[0]);
  });

  it("filters results as the query changes", async () => {
    mockApi();
    const user = userEvent.setup();
    renderSearch(vi.fn());

    await screen.findByText("Hong Kong Equity Fund");

    await user.type(screen.getByLabelText(/add fund to allocation/i), "bond");

    await waitFor(() => expect(screen.queryByText("Hong Kong Equity Fund")).not.toBeInTheDocument());
    expect(screen.getByText("World Bond Fund")).toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const { container } = renderSearch(vi.fn());
    await screen.findByText("Hong Kong Equity Fund");
    expect(await axe(container)).toHaveNoViolations();
  });
});
