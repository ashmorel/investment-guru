import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import GuruTakePanel from "./GuruTakePanel";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const TAKE = {
  id: 1,
  kind: "take",
  portfolio_id: null,
  payload: {
    commentary: "Markets are choppy but your core holdings look resilient.",
    risks: [{ kind: "concentration", note: "Tech is 40% of the book." }],
    ideas: [
      {
        symbol: "NVDA",
        action: "reduce",
        conviction: "med",
        rationale: "Valuation is stretched after the run-up.",
      },
    ],
    disclaimer: "Not financial advice.",
  },
  model: "gpt-5",
  created_at: "2026-07-09T08:00:00Z",
};

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GuruTakePanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GuruTakePanel", () => {
  it("renders commentary, risks and ideas from the latest take", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    expect(await screen.findByText(/markets are choppy/i)).toBeInTheDocument();
    expect(screen.getByText(/tech is 40% of the book/i)).toBeInTheDocument();
    expect(screen.getByText(/REDUCE · NVDA · MED/)).toBeInTheDocument();
    expect(screen.getByText(/not financial advice/i)).toBeInTheDocument();
  });

  it("shows an empty state when there is no take yet", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse({ detail: "not found" }, 404);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    expect(await screen.findByText(/no take yet — refresh to generate/i)).toBeInTheDocument();
  });

  it("shows an unconfigured banner when refresh returns 503", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take") && init?.method === "POST") {
        return jsonResponse({ detail: "Guru is not configured" }, 503);
      }
      if (url.includes("/api/guru/take/latest")) return jsonResponse({ detail: "not found" }, 404);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/no take yet/i);
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    expect(await screen.findByText(/guru isn't configured yet/i)).toBeInTheDocument();
  });

  it("shows an already-generating message when refresh returns 409", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take") && init?.method === "POST") {
        return jsonResponse({ detail: "A take is already generating" }, 409);
      }
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/markets are choppy/i);
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    expect(await screen.findByText(/already generating — check back shortly/i)).toBeInTheDocument();
  });

  it("shows a daily-limit message when refresh returns 429 budget_exhausted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take") && init?.method === "POST") {
        return jsonResponse({ detail: "budget_exhausted" }, 429);
      }
      if (url.includes("/api/guru/take/latest")) return jsonResponse({ detail: "not found" }, 404);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/no take yet/i);
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    expect(await screen.findByText(/daily ai limit reached — resets tomorrow/i)).toBeInTheDocument();
  });

  it("fires a refresh POST then refetches the latest take", async () => {
    let posted = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/guru/take") && init?.method === "POST") {
        posted = true;
        return jsonResponse(TAKE, 201);
      }
      if (url.includes("/api/guru/take/latest")) {
        return posted ? jsonResponse(TAKE) : jsonResponse({ detail: "not found" }, 404);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/no take yet/i);
    await user.click(screen.getByRole("button", { name: /refresh/i }));

    expect(await screen.findByText(/markets are choppy/i)).toBeInTheDocument();
    expect(posted).toBe(true);
  });

  it("renders a Discuss link for each idea", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/guru/take/latest")) return jsonResponse(TAKE);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPanel();

    expect(await screen.findByRole("link", { name: /discuss any idea in chat/i })).toBeInTheDocument();
  });
});
