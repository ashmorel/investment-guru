import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import AdminPage from "./AdminPage";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
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
  it("renders the admin landing when the ping succeeds", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/admin/ping")) return jsonResponse({ ok: true });
      throw new Error(`Unexpected fetch: ${url}`);
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: /admin area/i })).toBeInTheDocument();
    expect(screen.getByText(/this account is an administrator/i)).toBeInTheDocument();
    expect(
      screen.getByText(/llm provider & model configuration — coming in the next update/i),
    ).toBeInTheDocument();
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

  it("has no detectable accessibility violations", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/api/admin/ping")) return jsonResponse({ ok: true });
      throw new Error(`Unexpected fetch: ${url}`);
    });
    const { container } = renderPage();
    await screen.findByRole("heading", { name: /admin area/i });
    expect(await axe(container)).toHaveNoViolations();
  });
});
