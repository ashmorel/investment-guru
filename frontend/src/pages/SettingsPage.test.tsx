import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import type { InvestorProfile, UsageSummary } from "../lib/types";
import SettingsPage from "./SettingsPage";

const PROFILE: InvestorProfile = {
  risk_appetite: "balanced",
  horizon: "medium",
  sector_interests: ["Tech", "Healthcare"],
  free_text: "Prefer dividend payers.",
  digest_enabled: false,
};

const USAGE: UsageSummary = {
  by_mode: [
    { mode: "review", calls: 12, input_tokens: 4000, output_tokens: 900, est_cost_usd: "3.10" },
    { mode: "digest", calls: 5, input_tokens: 1200, output_tokens: 300, est_cost_usd: "1.62" },
  ],
  total_cost_30d: "4.72",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(opts?: { profile?: InvestorProfile; usage?: UsageSummary }) {
  const profile = opts?.profile ?? PROFILE;
  const usage = opts?.usage ?? USAGE;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/guru/profile") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as InvestorProfile;
      return jsonResponse(body);
    }
    if (url.includes("/api/guru/profile")) {
      return jsonResponse(profile);
    }
    if (url.includes("/api/guru/usage/summary")) {
      return jsonResponse(usage);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SettingsPage", () => {
  it("renders the fetched investor profile", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText(/calibrates every recommendation/i)).toBeInTheDocument();

    const radiogroup = await screen.findByRole("radiogroup", { name: /risk appetite/i });
    const balanced = within(radiogroup).getByRole("radio", { name: /balanced/i });
    expect(balanced).toBeChecked();

    expect(screen.getByRole("combobox", { name: /horizon/i })).toHaveValue("medium");
    expect(screen.getByText("Tech")).toBeInTheDocument();
    expect(screen.getByText("Healthcare")).toBeInTheDocument();
    expect(screen.getByLabelText(/anything else the guru should know/i)).toHaveValue(
      "Prefer dividend payers.",
    );
  });

  it("PUTs the edited profile on submit and shows a saved confirmation", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    const radiogroup = await screen.findByRole("radiogroup", { name: /risk appetite/i });
    await user.click(within(radiogroup).getByRole("radio", { name: /adventurous/i }));

    await user.type(screen.getByLabelText(/add sector interest/i), "Energy");
    await user.click(screen.getByRole("button", { name: /\+ add/i }));

    await user.click(screen.getByRole("button", { name: /save profile/i }));

    expect(await screen.findByText(/saved just now/i)).toBeInTheDocument();

    const putCall = vi
      .mocked(globalThis.fetch)
      .mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(putCall).toBeDefined();
    const putInit = putCall![1] as RequestInit;
    const body = JSON.parse(String(putInit.body)) as InvestorProfile;
    expect(body.risk_appetite).toBe("adventurous");
    expect(body.sector_interests).toContain("Energy");
  });

  it("removes a sector chip when its remove control is clicked", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Tech");
    await user.click(screen.getByRole("button", { name: /remove tech/i }));
    expect(screen.queryByText("Tech")).not.toBeInTheDocument();
  });

  it("renders the usage table by_mode rows and the 30-day total", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByText("$4.72")).toBeInTheDocument();
    const reviewRow = (await screen.findByText("review")).closest("tr")!;
    expect(within(reviewRow).getByText("12")).toBeInTheDocument();
    expect(within(reviewRow).getByText("4000")).toBeInTheDocument();
    expect(within(reviewRow).getByText("900")).toBeInTheDocument();
    expect(within(reviewRow).getByText("$3.10")).toBeInTheDocument();
    expect(screen.getByText(/no hard caps/i)).toBeInTheDocument();
  });

  it("renders the daily-budget note near the digest toggle", async () => {
    mockApi();
    renderPage();

    const toggle = await screen.findByRole("checkbox", { name: /daily digest/i });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(/daily ai spend limit: resets each day/i)).toBeInTheDocument();
  });

  it("PUTs digest_enabled when the daily digest toggle is switched on and saved", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    const toggle = await screen.findByRole("checkbox", { name: /daily digest/i });
    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.click(screen.getByRole("button", { name: /save profile/i }));
    expect(await screen.findByText(/saved just now/i)).toBeInTheDocument();

    // Use the most recent PUT: the fetch spy's call history accumulates
    // across every `it` in this file (no restoreMocks between tests), and an
    // earlier test already issued its own PUT.
    const putCalls = vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(putCalls.length).toBeGreaterThan(0);
    const putCall = putCalls[putCalls.length - 1];
    const body = JSON.parse(String((putCall[1] as RequestInit).body)) as InvestorProfile;
    expect(body.digest_enabled).toBe(true);
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const { container } = renderPage();
    await screen.findByText(/calibrates every recommendation/i);
    await screen.findByText("$4.72");
    expect(await axe(container)).toHaveNoViolations();
  });
});
