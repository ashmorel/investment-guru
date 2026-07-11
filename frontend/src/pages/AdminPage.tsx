import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../lib/api";

export default function AdminPage() {
  const ping = useQuery({
    queryKey: ["admin", "ping"],
    queryFn: () => apiFetch<{ ok: boolean }>("/api/admin/ping"),
    retry: false,
  });

  if (ping.isPending) return <p className="text-muted">Loading…</p>;

  const forbidden = ping.isError && ping.error instanceof ApiError && ping.error.status === 403;

  if (forbidden) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-text">Not authorized</h1>
        <section className="rounded-xl border border-border bg-surface p-5 shadow">
          <p className="text-sm text-text">This account doesn't have admin access.</p>
        </section>
      </div>
    );
  }

  if (ping.isError) {
    return <p className="text-loss">Failed to load the admin area.</p>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Admin area</h1>
      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <p className="text-sm text-text">This account is an administrator.</p>
        <p className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">Coming soon</p>
        <ul className="mt-2 space-y-1 text-sm text-muted">
          <li>LLM provider &amp; model configuration — coming in the next update.</li>
        </ul>
      </section>
    </div>
  );
}
