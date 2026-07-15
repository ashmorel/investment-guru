import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function mockApi(me: { id: number; email: string; is_admin: boolean }) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/auth/me")) return jsonResponse(me);
    if (url.includes("/api/guru/take/latest")) return jsonResponse({ detail: "not found" }, 404);
    if (url.includes("/dashboard/attention")) return jsonResponse({ signals: [] });
    if (url.includes("/api/dashboard")) return jsonResponse({ portfolios: [], as_of: "2026-07-11T09:00:00Z" });
    throw new Error(`Unexpected fetch: ${url}`);
  });
}

// App.tsx owns a module-scoped QueryClient singleton, so each test must get
// a fresh module instance (vi.resetModules + dynamic import) to avoid the
// ["me"] cache leaking is_admin state from one test into the next.
describe("App nav", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/");
  });

  it("hides the Admin nav item for a non-admin user", async () => {
    vi.resetModules();
    const { default: App } = await import("./App");
    mockApi({ id: 1, email: "lee@test.dev", is_admin: false });
    render(<App />);

    await screen.findByText("Dashboard");
    expect(screen.queryByRole("link", { name: /^admin$/i })).not.toBeInTheDocument();
  });

  it("shows the Admin nav item for an admin user", async () => {
    vi.resetModules();
    const { default: App } = await import("./App");
    mockApi({ id: 1, email: "lee@test.dev", is_admin: true });
    render(<App />);

    expect(await screen.findByRole("link", { name: /^admin$/i })).toBeInTheDocument();
  });

  it("uses a mobile column shell and restores the fixed sidebar at md", async () => {
    vi.resetModules();
    const { default: App } = await import("./App");
    mockApi({ id: 1, email: "lee@test.dev", is_admin: false });
    const { container } = render(<App />);

    await screen.findByText("Dashboard");
    const shell = container.querySelector("[data-testid='app-shell']");
    const nav = screen.getByRole("navigation");
    const main = screen.getByRole("main");
    expect(shell).toHaveClass("flex-col", "md:flex-row");
    expect(nav).toHaveClass("w-full", "md:w-56", "md:min-h-screen");
    expect(main).toHaveClass("min-w-0", "p-4", "md:p-8");
  });
});
