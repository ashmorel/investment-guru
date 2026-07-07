import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ImportWizardPage from "./ImportWizardPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ImportWizardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ImportWizardPage", () => {
  it("uploads a CSV and shows the preview rows", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/portfolios")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/api/imports/preview")) {
        return new Response(
          JSON.stringify({
            rows: [
              { symbol: "AAPL", quantity: "10", purchase_price: "150.25", comment: null, known: true },
              { symbol: "BADX", quantity: "1", purchase_price: null, comment: null, known: false },
            ],
            errors: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    renderPage();
    const file = new File(["Symbol,Quantity\nAAPL,10\n"], "pf.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText(/csv file/i), file);
    await userEvent.click(screen.getByRole("button", { name: /upload/i }));

    expect(await screen.findByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText(/not recognised/i)).toBeInTheDocument();
  });
});
