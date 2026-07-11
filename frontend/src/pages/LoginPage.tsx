import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, ApiError } from "../lib/api";

type Mode = "login" | "register";

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  function toggleMode() {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError(null);
    setConfirmPassword("");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (mode === "register") {
      if (password.length < 8) {
        setError("Password must be at least 8 characters.");
        return;
      }
      if (password !== confirmPassword) {
        setError("Passwords don't match.");
        return;
      }
      try {
        await apiFetch<void>("/api/auth/register", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        });
        await qc.invalidateQueries({ queryKey: ["me"] });
        navigate("/");
      } catch (err) {
        setError(
          err instanceof ApiError && err.status === 409
            ? "That email is already registered — log in instead"
            : "Registration failed",
        );
      }
      return;
    }

    try {
      await apiFetch<void>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      await qc.invalidateQueries({ queryKey: ["me"] });
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Invalid credentials" : "Login failed");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded-xl bg-surface p-8 shadow border border-border">
        <h1 className="text-xl font-semibold text-text">Investment Guru</h1>
        <label className="block text-sm font-medium text-text">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-border px-3 py-2 text-text focus:outline-none focus:ring-2 focus:ring-accent"
            required
          />
        </label>
        <label className="block text-sm font-medium text-text">
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby={mode === "register" ? "password-hint" : undefined}
            className="mt-1 w-full rounded-md border border-border px-3 py-2 text-text focus:outline-none focus:ring-2 focus:ring-accent"
            required
          />
        </label>
        {mode === "register" && (
          <>
            <p id="password-hint" className="-mt-2 text-xs text-muted">
              At least 8 characters
            </p>
            <label className="block text-sm font-medium text-text">
              Confirm password
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-border px-3 py-2 text-text focus:outline-none focus:ring-2 focus:ring-accent"
                required
              />
            </label>
          </>
        )}
        {error && <p className="text-sm text-loss">{error}</p>}
        <button type="submit" className="w-full rounded-md bg-accent px-4 py-2 text-white hover:bg-accent/90">
          {mode === "register" ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          onClick={toggleMode}
          className="w-full text-center text-sm text-accent underline"
        >
          {mode === "login" ? "Don't have an account? Create account" : "Already have an account? Log in"}
        </button>
      </form>
    </div>
  );
}
