import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import LoginPage from "./LoginPage";

function jsonResponse(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  it("submits credentials to the login endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    renderPage();
    await userEvent.type(screen.getByLabelText(/email/i), "lee@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "pw123456");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("shows an invalid-credentials error on 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "Invalid credentials" }, 401));
    renderPage();
    await userEvent.type(screen.getByLabelText(/email/i), "lee@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "wrongpass");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument();
  });

  it("switches to the register form when Create account is clicked", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(screen.getByRole("button", { name: /^create account$/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/confirm password/i)).toBeInTheDocument();
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /already have an account\? log in/i })).toBeInTheDocument();
  });

  it("registers a new account and routes into the app on success", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await userEvent.type(screen.getByLabelText(/email/i), "new@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "pw123456");
    await userEvent.type(screen.getByLabelText(/confirm password/i), "pw123456");
    await userEvent.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/register",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    const registerCall = fetchMock.mock.calls.find(([url]) => url === "/api/auth/register");
    const body = JSON.parse(String(registerCall![1]!.body)) as { email: string; password: string };
    expect(body).toEqual({ email: "new@test.dev", password: "pw123456" });
  });

  it("shows an inline error when the email is already registered", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "email_taken" }, 409));
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await userEvent.type(screen.getByLabelText(/email/i), "taken@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "pw123456");
    await userEvent.type(screen.getByLabelText(/confirm password/i), "pw123456");
    await userEvent.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(
      await screen.findByText(/that email is already registered — log in instead/i),
    ).toBeInTheDocument();
  });

  it("blocks submit with a client-side error when the password is too short", async () => {
    // mockClear resets call history left over from earlier tests in this
    // file — the fetch spy is shared across every `it` (no restoreMocks).
    const fetchMock = vi.spyOn(globalThis, "fetch").mockClear();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await userEvent.type(screen.getByLabelText(/email/i), "new@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "short1");
    await userEvent.type(screen.getByLabelText(/confirm password/i), "short1");
    await userEvent.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(await screen.findByText(/password must be at least 8 characters/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("blocks submit with a client-side error when the passwords don't match", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockClear();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));

    await userEvent.type(screen.getByLabelText(/email/i), "new@test.dev");
    await userEvent.type(screen.getByLabelText(/^password$/i), "pw123456");
    await userEvent.type(screen.getByLabelText(/confirm password/i), "pw999999");
    await userEvent.click(screen.getByRole("button", { name: /^create account$/i }));

    expect(await screen.findByText(/passwords don't match/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("has no detectable accessibility violations on the register form", async () => {
    const { container } = renderPage();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    await screen.findByLabelText(/confirm password/i);
    expect(await axe(container)).toHaveNoViolations();
  });
});
