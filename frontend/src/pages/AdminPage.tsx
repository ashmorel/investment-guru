import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, apiFetch, getLlmConfig, llmConfigErrorMessage, putLlmConfig, testLlmConfig } from "../lib/api";
import type { LlmConfigInput } from "../lib/types";

const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
];

interface LlmForm {
  provider: string;
  advice_model: string;
  scan_model: string;
  advice_input_price: string;
  advice_output_price: string;
  scan_input_price: string;
  scan_output_price: string;
}

export default function AdminPage() {
  const qc = useQueryClient();
  const ping = useQuery({
    queryKey: ["admin", "ping"],
    queryFn: () => apiFetch<{ ok: boolean }>("/api/admin/ping"),
    retry: false,
  });

  const config = useQuery({
    queryKey: ["admin", "llm-config"],
    queryFn: getLlmConfig,
    enabled: ping.isSuccess,
    retry: false,
  });

  const [form, setForm] = useState<LlmForm | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");

  useEffect(() => {
    if (config.data && form === null) {
      setForm({
        provider: config.data.provider,
        advice_model: config.data.advice_model,
        scan_model: config.data.scan_model,
        advice_input_price: config.data.advice_input_price ?? "",
        advice_output_price: config.data.advice_output_price ?? "",
        scan_input_price: config.data.scan_input_price ?? "",
        scan_output_price: config.data.scan_output_price ?? "",
      });
    }
  }, [config.data, form]);

  const save = useMutation({
    mutationFn: (body: LlmConfigInput) => putLlmConfig(body),
    onSuccess: (data) => {
      setForm({
        provider: data.provider,
        advice_model: data.advice_model,
        scan_model: data.scan_model,
        advice_input_price: data.advice_input_price ?? "",
        advice_output_price: data.advice_output_price ?? "",
        scan_input_price: data.scan_input_price ?? "",
        scan_output_price: data.scan_output_price ?? "",
      });
      setApiKeyInput("");
      qc.setQueryData(["admin", "llm-config"], data);
      qc.invalidateQueries({ queryKey: ["admin", "llm-config"] });
    },
  });

  const test = useMutation({
    mutationFn: (body: LlmConfigInput) => testLlmConfig(body),
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

  if (config.isError) {
    return <p className="text-loss">Failed to load the AI provider configuration.</p>;
  }

  if (!form) return <p className="text-muted">Loading…</p>;

  const keySet = config.data?.key_set ?? false;
  const f = form;

  function buildBody(): LlmConfigInput {
    const trimmedKey = apiKeyInput.trim();
    return {
      provider: f.provider,
      advice_model: f.advice_model,
      scan_model: f.scan_model,
      ...(trimmedKey ? { api_key: trimmedKey } : {}),
      advice_input_price: f.advice_input_price,
      advice_output_price: f.advice_output_price,
      scan_input_price: f.scan_input_price,
      scan_output_price: f.scan_output_price,
    };
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-text">Admin area</h1>
      <section className="rounded-xl border border-border bg-surface p-5 shadow">
        <h2 className="font-medium text-text">AI provider</h2>
        <p className="mt-1 text-sm text-muted">
          Configure which LLM powers Guru advice and portfolio scans.
        </p>

        <form
          className="mt-5 space-y-5"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate(buildBody());
          }}
        >
          <label className="block" htmlFor="provider">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">Provider</span>
            <select
              id="provider"
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              className="mt-2 block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
            >
              {PROVIDER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div>
              <label className="block" htmlFor="advice_model">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Advice model
                </span>
                <input
                  id="advice_model"
                  value={form.advice_model}
                  onChange={(e) => setForm({ ...form, advice_model: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
                />
              </label>
              <p className="mt-1 text-xs text-muted">model id</p>
            </div>
            <div>
              <label className="block" htmlFor="scan_model">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Scan model
                </span>
                <input
                  id="scan_model"
                  value={form.scan_model}
                  onChange={(e) => setForm({ ...form, scan_model: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
                />
              </label>
              <p className="mt-1 text-xs text-muted">model id</p>
            </div>
          </div>

          <div>
            <label className="block" htmlFor="api_key">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">API key</span>
            </label>
            <div className="mt-2 flex items-center gap-3">
              <input
                id="api_key"
                type="password"
                autoComplete="new-password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder={keySet ? "•••• configured — leave blank to keep" : "Enter API key"}
                className="block w-full rounded-md border border-border px-3 py-2 text-sm text-text"
              />
              {keySet && (
                <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-gain/10 px-2.5 py-0.5 text-xs font-medium text-gain">
                  <span aria-hidden="true">●</span> Configured
                </span>
              )}
            </div>
          </div>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted">Budget (optional)</p>
            <div className="mt-2 grid grid-cols-1 gap-4 rounded-lg border border-border bg-bg p-4 sm:grid-cols-2">
              <label className="block" htmlFor="advice_input_price">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Advice input $/1M
                </span>
                <input
                  id="advice_input_price"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={form.advice_input_price}
                  onChange={(e) => setForm({ ...form, advice_input_price: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                />
              </label>
              <label className="block" htmlFor="advice_output_price">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Advice output $/1M
                </span>
                <input
                  id="advice_output_price"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={form.advice_output_price}
                  onChange={(e) => setForm({ ...form, advice_output_price: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                />
              </label>
              <label className="block" htmlFor="scan_input_price">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Scan input $/1M
                </span>
                <input
                  id="scan_input_price"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={form.scan_input_price}
                  onChange={(e) => setForm({ ...form, scan_input_price: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                />
              </label>
              <label className="block" htmlFor="scan_output_price">
                <span className="text-xs font-medium uppercase tracking-wide text-muted">
                  Scan output $/1M
                </span>
                <input
                  id="scan_output_price"
                  inputMode="decimal"
                  placeholder="0.00"
                  value={form.scan_output_price}
                  onChange={(e) => setForm({ ...form, scan_output_price: e.target.value })}
                  className="mt-2 block w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text"
                />
              </label>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-4 pt-1">
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={test.isPending}
                onClick={() => test.mutate(buildBody())}
                className="text-sm font-medium text-accent disabled:opacity-50"
              >
                {test.isPending ? "Testing…" : "Test connection"}
              </button>
              {test.data && (
                <p
                  className={`flex items-center gap-1 text-sm ${
                    test.data.ok ? "text-gain" : "text-loss"
                  }`}
                >
                  <span aria-hidden="true">{test.data.ok ? "✓" : "✗"}</span>
                  {test.data.ok ? "Connection ok" : test.data.detail}
                </p>
              )}
              {test.isError && (
                <p className="text-sm text-loss">Could not reach the test endpoint — try again.</p>
              )}
            </div>

            <div className="flex items-center gap-3">
              {save.isSuccess && <p className="text-sm text-gain">Saved just now</p>}
              {save.isError && (
                <p className="text-sm text-loss">
                  {llmConfigErrorMessage(save.error) ?? "Could not save — please try again."}
                </p>
              )}
              <button
                type="submit"
                disabled={save.isPending}
                className="rounded-md bg-accent px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {save.isPending ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </form>
      </section>

      <p className="text-xs text-muted">
        Only the admin can change this. Switching provider takes effect immediately — no redeploy.
      </p>
    </div>
  );
}
