import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import type { LlmConfig } from "../lib/types";
import AdminPage from "./AdminPage";

const CONFIG: LlmConfig = {
  provider: "openai",
  advice_model: "gpt-4o",
  scan_model: "gpt-4o-mini",
  advice_input_price: "2.50",
  advice_output_price: "10.00",
  scan_input_price: "0.15",
  scan_output_price: "0.60",
  key_set: true,
  updated_at: "2026-07-01T00:00:00",
  updated_by: "lee@test.dev",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(opts?: { config?: LlmConfig; test?: { ok: boolean; detail: string } }) {
  const config = opts?.config ?? CONFIG;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/api/admin/ping")) return jsonResponse({ ok: true });
    if (url.includes("/api/admin/llm-config/test") && method === "POST") {
      return jsonResponse(opts?.test ?? { ok: true, detail: "connection ok" });
    }
    if (url.includes("/api/admin/llm-config") && method === "PUT") {
      const body = JSON.parse(String(init?.body)) as Partial<LlmConfig>;
      return jsonResponse({ ...config, ...body, key_set: "api_key" in body ? true : config.key_set });
    }
    if (url.includes("/api/admin/llm-config")) return jsonResponse(config);
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AdminPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AdminPage", () => {
  it("renders the AI provider panel prefilled from the fetched config", async () => {
    mockApi();
    renderPage();

    expect(await screen.findByRole("heading", { name: /admin area/i })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /ai provider/i })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /provider/i })).toHaveValue("openai");
    expect(screen.getByLabelText(/advice model/i)).toHaveValue("gpt-4o");
    expect(screen.getByLabelText(/scan model/i)).toHaveValue("gpt-4o-mini");
  });

  it("shows the key field empty with a Configured pill when a key is already set", async () => {
    mockApi();
    renderPage();

    const keyField = await screen.findByLabelText(/api key/i);
    expect(keyField).toHaveValue("");
    expect(keyField).toHaveAttribute("placeholder", expect.stringMatching(/configured — leave blank to keep/i));
    expect(screen.getByText(/^configured$/i)).toBeInTheDocument();
  });

  it("saves an edited model without sending api_key, preserving the stored key", async () => {
    mockApi();
    const user = userEvent.setup();
    renderPage();

    const adviceModel = await screen.findByLabelText(/advice model/i);
    await user.clear(adviceModel);
    await user.type(adviceModel, "gpt-4.1");

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText(/saved just now/i)).toBeInTheDocument();

    const putCall = vi
      .mocked(globalThis.fetch)
      .mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(putCall).toBeDefined();
    const body = JSON.parse(String((putCall![1] as RequestInit).body)) as Record<string, unknown>;
    expect(body.advice_model).toBe("gpt-4.1");
    expect("api_key" in body).toBe(false);
  });

  it("shows a success result when Test connection succeeds", async () => {
    mockApi({ test: { ok: true, detail: "connection ok" } });
    const user = userEvent.setup();
    renderPage();

    await screen.findByLabelText(/advice model/i);
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    expect(await screen.findByText(/connection ok/i)).toBeInTheDocument();
  });

  it("shows a failure result and detail when Test connection fails", async () => {
    mockApi({ test: { ok: false, detail: "invalid api key" } });
    const user = userEvent.setup();
    renderPage();

    await screen.findByLabelText(/advice model/i);
    await user.click(screen.getByRole("button", { name: /test connection/i }));

    expect(await screen.findByText(/invalid api key/i)).toBeInTheDocument();
  });

  it("shows a not-authorized state when the ping is forbidden", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/admin/ping")) return jsonResponse({ detail: "admin_only" }, 403);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: /not authorized/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /admin area/i })).not.toBeInTheDocument();
  });

  it("has no detectable accessibility violations on the populated form", async () => {
    mockApi();
    const { container } = renderPage();
    await screen.findByLabelText(/advice model/i);
    expect(await axe(container)).toHaveNoViolations();
  });
});
