import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttentionPanel from "./AttentionPanel";

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AttentionPanel />
    </QueryClientProvider>,
  );
}

describe("AttentionPanel", () => {
  it("renders severity-ranked signals", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          signals: [
            { id: 1, instrument_id: 5, symbol: "NVDA", kind: "earnings_upcoming",
              severity: "high", title: "NVDA reports in 2 days", detail: "Earnings on 2026-07-09",
              data: {}, computed_at: "2026-07-07T09:00:00Z",
              portfolio_id: 1, portfolio_name: "Core Growth" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderPanel();
    expect(await screen.findByText("NVDA reports in 2 days")).toBeInTheDocument();
    expect(screen.getByText(/Core Growth/)).toBeInTheDocument();
  });

  it("shows empty state when no signals", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ signals: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    );
    renderPanel();
    expect(await screen.findByText(/No flags right now/i)).toBeInTheDocument();
  });
});
