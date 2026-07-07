import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, ApiError } from "../lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await apiFetch<void>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
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
            className="mt-1 w-full rounded-md border border-border px-3 py-2 text-text focus:outline-none focus:ring-2 focus:ring-accent"
            required
          />
        </label>
        {error && <p className="text-sm text-loss">{error}</p>}
        <button type="submit" className="w-full rounded-md bg-accent px-4 py-2 text-white hover:bg-accent/90">
          Sign in
        </button>
      </form>
    </div>
  );
}
