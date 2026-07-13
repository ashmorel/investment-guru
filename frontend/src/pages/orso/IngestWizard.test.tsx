import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import IngestWizard from "./IngestWizard";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DRAFT = {
  rows: [
    {
      parsed_code: "HKEF",
      parsed_name: "Hong Kong Equity Fund",
      matched_fund_id: 1,
      proposed_fund: null,
      units: "1000.0000",
      value: "477040.00",
      currency: "HKD",
      contribution_pct: "61.00",
      implied_price: "477.0400",
      flags: [],
    },
    {
      parsed_code: "USBD",
      parsed_name: "US Bond",
      matched_fund_id: null,
      proposed_fund: { code: "USBD", name: "US Bond", currency: "USD", asset_class: "unknown", risk_rating: 4 },
      units: "128.7500",
      value: "18400.00",
      currency: "USD",
      contribution_pct: "36.00",
      implied_price: "142.9200",
      flags: [],
    },
  ],
  warnings: ["pct_sum=97 (not 100)"],
  source: "csv",
};

function mockApi(overrides?: {
  onIngest?: () => Response | Promise<Response>;
  onApply?: (body: unknown) => void;
  applyStatus?: number;
}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url.includes("/api/orso/ingest/csv") && method === "POST") {
      return overrides?.onIngest ? overrides.onIngest() : jsonResponse(DRAFT);
    }
    if (url.includes("/api/orso/allocation/apply") && method === "POST") {
      overrides?.onApply?.(JSON.parse(String(init?.body)));
      return jsonResponse({ created_funds: ["USBD"], switched: true }, overrides?.applyStatus ?? 200);
    }
    throw new Error(`Unexpected fetch: ${url} ${method}`);
  });
}

function renderWizard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/orso/import"]}>
        <IngestWizard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function csvFile() {
  return new File(
    ["fund_code,units,contribution_pct\nHKEF,1000,61\nUSBD,128.75,36\n"],
    "statement.csv",
    { type: "text/csv" },
  );
}

async function uploadDraft(user: ReturnType<typeof userEvent.setup>) {
  const input = document.getElementById("orso-ingest-file") as HTMLInputElement;
  await user.upload(input, csvFile());
}

describe("IngestWizard", () => {
  it("uses scheme-neutral copy and the 2MB size hint (not the WMFS / 10MB mockup text)", async () => {
    mockApi();
    renderWizard();

    expect(screen.getByText("HSBC ORSO · review before saving")).toBeInTheDocument();
    expect(screen.queryByText(/WMFS/)).not.toBeInTheDocument();
    expect(screen.getByText(/max 2MB/)).toBeInTheDocument();
    expect(screen.queryByText(/max 10MB/)).not.toBeInTheDocument();
  });

  it("renders the review table with both draft rows, matched/new-fund badges, and the pct-sum warning", async () => {
    mockApi();
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);

    expect(await screen.findByText("Hong Kong Equity Fund")).toBeInTheDocument();
    expect(screen.getByText("US Bond")).toBeInTheDocument();
    expect(screen.getByText("Matched")).toBeInTheDocument();
    expect(screen.getByText("New fund")).toBeInTheDocument();
    // 61 + 36 = 97, not 100
    expect(screen.getByText(/add up to 97% \(not 100%\)/i)).toBeInTheDocument();
  });

  it("edits a row and Confirm calls applyAllocation with the reviewed payload", async () => {
    let posted: {
      new_funds: { code: string; name: string; currency: string; asset_class?: string; risk_rating?: number }[];
      allocations: { fund_id: number | null; new_fund_code: string | null; units: string; contribution_pct: string; price: { market_value: string; as_of: string } | null }[];
    } | null = null;
    mockApi({ onApply: (body) => (posted = body as typeof posted) });
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);
    await screen.findByText("Hong Kong Equity Fund");

    // Edit HKEF's contribution % from 61 to 64 so the split sums to 100.
    const pctInput = screen.getByLabelText(/HKEF contribution %/i);
    await user.clear(pctInput);
    await user.type(pctInput, "64");
    expect(screen.queryByText(/not 100%/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /confirm & save/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted!;
    expect(body.allocations).toHaveLength(2);

    const hkef = body.allocations.find((a) => a.fund_id === 1);
    expect(hkef).toMatchObject({ contribution_pct: "64", units: "1000.0000" });
    expect(hkef?.price).toEqual({ market_value: "477040.00", as_of: expect.any(String) });

    const usbd = body.allocations.find((a) => a.new_fund_code === "USBD");
    expect(usbd).toMatchObject({ fund_id: null, contribution_pct: "36.00" });
    expect(body.new_funds).toEqual([
      { code: "USBD", name: "US Bond", currency: "USD", asset_class: "unknown", risk_rating: 4 },
    ]);

    expect(await screen.findByText(/allocation updated/i)).toBeInTheDocument();
  });

  it("editing a new-fund row's code and value clears the stale flag and Confirm sends the edited code", async () => {
    let posted: {
      new_funds: { code: string; name: string; currency: string; asset_class?: string; risk_rating?: number }[];
      allocations: { fund_id: number | null; new_fund_code: string | null; units: string; contribution_pct: string; price: { market_value: string; as_of: string } | null }[];
    } | null = null;
    const longNameDraft = {
      rows: [
        {
          parsed_code: "HANG SENG INDEX TRACKING FUND CLASS A ACCUMULATION",
          parsed_name: "Hang Seng Index Tracking Fund Class A Accumulation",
          matched_fund_id: null,
          proposed_fund: {
            code: "HSITFCAA",
            name: "Hang Seng Index Tracking Fund Class A Accumulation",
            currency: "HKD",
            asset_class: "unknown",
            risk_rating: 4,
          },
          units: "10",
          value: "not-a-number",
          currency: "HKD",
          contribution_pct: "100",
          implied_price: null,
          flags: ["unparseable_value"],
        },
      ],
      warnings: [],
      source: "csv",
    };
    mockApi({ onIngest: () => jsonResponse(longNameDraft), onApply: (body) => (posted = body as typeof posted) });
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);
    await screen.findByText("Hang Seng Index Tracking Fund Class A Accumulation");

    // The stale server flag is shown initially.
    expect(screen.getByText(/unparseable value/i)).toBeInTheDocument();

    // Fix the value — the flag should clear because it's recomputed live.
    const valueInput = screen.getByLabelText(/HSITFCAA value/i);
    await user.clear(valueInput);
    await user.type(valueInput, "1000");
    expect(screen.queryByText(/unparseable value/i)).not.toBeInTheDocument();

    // Edit the auto-prefilled code to something shorter/custom.
    const codeInput = screen.getByLabelText(/Hang Seng Index Tracking Fund Class A Accumulation code/i);
    await user.clear(codeInput);
    await user.type(codeInput, "hsitf");

    await user.click(screen.getByRole("button", { name: /confirm & save/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted!;
    expect(body.new_funds).toEqual([
      { code: "HSITF", name: "Hang Seng Index Tracking Fund Class A Accumulation", currency: "HKD", asset_class: "unknown", risk_rating: 4 },
    ]);
    expect(body.allocations[0]).toMatchObject({ new_fund_code: "HSITF", fund_id: null });
  });

  it("entering native units + price computes value = units × price and sends it, keeping a matched USD fund's currency", async () => {
    let posted: {
      new_funds: unknown[];
      allocations: { fund_id: number | null; units: string; contribution_pct: string; price: { market_value: string; as_of: string } | null }[];
    } | null = null;
    const usdMatchedDraft = {
      rows: [
        {
          parsed_code: "IUSI",
          parsed_name: "iShares US Index Fund (IE) USD Institutional Accumulating Class",
          matched_fund_id: 7,
          proposed_fund: null,
          units: null,
          value: null,
          currency: "USD",
          contribution_pct: "100",
          implied_price: null,
          flags: [],
        },
      ],
      warnings: [],
      source: "csv",
    };
    mockApi({ onIngest: () => jsonResponse(usdMatchedDraft), onApply: (body) => (posted = body as typeof posted) });
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);
    await screen.findByText("iShares US Index Fund (IE) USD Institutional Accumulating Class");

    const unitsInput = screen.getByLabelText(/IUSI units/i);
    await user.type(unitsInput, "500");
    const priceInput = screen.getByLabelText(/IUSI price/i);
    await user.type(priceInput, "150.5");

    const valueInput = screen.getByLabelText(/IUSI value/i) as HTMLInputElement;
    expect(valueInput.value).toBe("75250.00"); // 500 x 150.5

    // Currency stays USD — the matched fund's native currency, untouched by
    // the price/units edit.
    expect((screen.getByLabelText(/IUSI currency/i) as HTMLInputElement).value).toBe("USD");

    await user.click(screen.getByRole("button", { name: /confirm & save/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted!;
    const iusi = body.allocations.find((a) => a.fund_id === 7);
    expect(iusi?.units).toBe("500");
    expect(iusi?.price).toEqual({ market_value: "75250.00", as_of: expect.any(String) });
  });

  it("renders a specific message when Confirm fails with a FastAPI validation (array detail) 422", async () => {
    mockApi({
      onApply: () => {
        throw new Error("unused");
      },
    });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/api/orso/ingest/csv") && method === "POST") return jsonResponse(DRAFT);
      if (url.includes("/api/orso/allocation/apply") && method === "POST") {
        return jsonResponse(
          { detail: [{ loc: ["body", "new_funds", 0, "code"], msg: "String should have at most 32 characters", type: "string_too_long" }] },
          422,
        );
      }
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);
    await screen.findByText("Hong Kong Equity Fund");

    await user.click(screen.getByRole("button", { name: /confirm & save/i }));

    expect(await screen.findByText(/could not save: code string should have at most 32 characters/i)).toBeInTheDocument();
  });

  it("surfaces a friendly message when the CSV is missing required headers (422)", async () => {
    mockApi({
      onIngest: () => jsonResponse({ detail: "missing_headers:['units']" }, 422),
    });
    const user = userEvent.setup();
    renderWizard();

    await uploadDraft(user);

    expect(await screen.findByText(/missing required columns/i)).toBeInTheDocument();
  });

  it("has no detectable accessibility violations in the review step", async () => {
    mockApi();
    const user = userEvent.setup();
    const { container } = renderWizard();

    await uploadDraft(user);
    await screen.findByText("Hong Kong Equity Fund");

    expect(await axe(container)).toHaveNoViolations();
  });
});
