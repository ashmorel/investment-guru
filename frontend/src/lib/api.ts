import type {
  AllocationDraft,
  ApplyRequest,
  ApplyResult,
  AssignResult,
  GroupExposure,
  GroupTrend,
  GuruReport,
  HoldingGroup,
  LlmConfig,
  LlmConfigInput,
  NewsResponse,
  NewsSummary,
  OrsoFundOut,
  RefreshNewsResult,
  SeedGroupsResult,
  StockNews,
  TrendRange,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Guru actions (take/digest/review/chat/orso advice) all raise the same
// 429 {detail: "budget_exhausted"} once a user's daily LLM spend cap is hit.
// apiFetch stores the raw error body text as ApiError.message, so this
// checks for the detail string rather than parsing JSON.
export function isBudgetExhausted(error: unknown): boolean {
  return error instanceof ApiError && error.status === 429 && error.message.includes("budget_exhausted");
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// --- ORSO ingest (Task 9) ---------------------------------------------------
// File uploads use FormData, so they bypass apiFetch's JSON Content-Type
// header (the browser sets the multipart boundary itself).
async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  return (await resp.json()) as T;
}

export function ingestCsv(file: File): Promise<AllocationDraft> {
  const form = new FormData();
  form.append("file", file);
  return apiUpload<AllocationDraft>("/api/orso/ingest/csv", form);
}

export function ingestScreenshot(file: File): Promise<AllocationDraft> {
  const form = new FormData();
  form.append("file", file);
  return apiUpload<AllocationDraft>("/api/orso/ingest/screenshot", form);
}

export function applyAllocation(body: ApplyRequest): Promise<ApplyResult> {
  return apiFetch<ApplyResult>("/api/orso/allocation/apply", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function searchFunds(q: string): Promise<OrsoFundOut[]> {
  const params = new URLSearchParams({ q });
  return apiFetch<OrsoFundOut[]>(`/api/orso/funds/search?${params.toString()}`);
}

export function setDisplayCurrency(currency: string): Promise<{ currency: string }> {
  return apiFetch<{ currency: string }>("/api/orso/display-currency", {
    method: "PUT",
    body: JSON.stringify({ currency }),
  });
}

// --- Admin LLM config (Task 8) -----------------------------------------------

export function getLlmConfig(): Promise<LlmConfig> {
  return apiFetch<LlmConfig>("/api/admin/llm-config");
}

export function putLlmConfig(body: LlmConfigInput): Promise<LlmConfig> {
  return apiFetch<LlmConfig>("/api/admin/llm-config", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testLlmConfig(body: LlmConfigInput): Promise<{ ok: boolean; detail: string }> {
  return apiFetch<{ ok: boolean; detail: string }>("/api/admin/llm-config/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// /llm-config PUT raises 422 for an unknown provider (plain string detail) or
// an unparsable budget price (pydantic validation-error array via
// field_validator) — surface both as a readable message instead of raw JSON.
export function llmConfigErrorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  try {
    const body = JSON.parse(error.message) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail === "unknown_provider"
        ? "Unknown provider — choose Anthropic, OpenAI, or Google."
        : body.detail;
    }
    if (Array.isArray(body.detail)) {
      const msgs = body.detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null))
        .filter((m): m is string => !!m);
      if (msgs.some((m) => m.includes("invalid_price"))) {
        return "One of the budget prices isn't a valid number.";
      }
      if (msgs.length) return msgs.join("; ");
    }
  } catch {
    /* fall through to the generic message below */
  }
  return "Invalid configuration — check the values and try again.";
}

// Ingest-specific error mapping: the ingest endpoints raise 413 (upload too
// large), 415 (bad screenshot mime type), 422 (missing CSV headers / bad
// encoding), 502/503 (LLM extraction unavailable) and the shared 429 budget
// error. Returns null for anything else so callers can fall back to a
// generic message.
export function ingestErrorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (isBudgetExhausted(error)) return "Daily AI limit reached — resets tomorrow.";
  switch (error.status) {
    case 413:
      return "That file is too large — max 2MB.";
    case 415:
      return "Unsupported image type — upload a PNG, JPEG, WEBP, or GIF screenshot.";
    case 422: {
      try {
        const body = JSON.parse(error.message) as { detail?: string };
        if (body.detail?.startsWith("missing_headers:")) {
          return `That CSV is missing required columns: ${body.detail.slice("missing_headers:".length)}`;
        }
        if (body.detail === "not_utf8_csv") {
          return "That file isn't valid UTF-8 text — re-export the CSV and try again.";
        }
      } catch {
        /* fall through to the generic message below */
      }
      return "Could not read that file — check the format and try again.";
    }
    case 502:
    case 503:
      return "The Guru can't read screenshots right now — try again shortly, or use the CSV upload instead.";
    default:
      return null;
  }
}

// --- News (Task 5) -----------------------------------------------------------

export function getNews(): Promise<NewsResponse> {
  return apiFetch<NewsResponse>("/api/news");
}

export function getStockNews(symbol: string): Promise<StockNews> {
  return apiFetch<StockNews>(`/api/news/${encodeURIComponent(symbol)}`);
}

export function refreshNews(): Promise<RefreshNewsResult> {
  return apiFetch<RefreshNewsResult>("/api/news/refresh", { method: "POST" });
}

export function getNewsSummary(symbol: string): Promise<GuruReport<NewsSummary>> {
  return apiFetch<GuruReport<NewsSummary>>(`/api/news/${encodeURIComponent(symbol)}/summary`);
}

export function generateNewsSummary(symbol: string): Promise<GuruReport<NewsSummary>> {
  return apiFetch<GuruReport<NewsSummary>>(`/api/news/${encodeURIComponent(symbol)}/summary`, {
    method: "POST",
  });
}

// The summary endpoints raise 422 (no headlines fetched yet for this symbol),
// 404 (symbol not held in any portfolio) and the shared 429 budget error.
export function newsSummaryErrorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (isBudgetExhausted(error)) return "Daily AI limit reached — resets tomorrow.";
  switch (error.status) {
    case 422:
      return "No recent headlines to summarize yet.";
    case 404:
      return "This symbol isn't held in a portfolio.";
    default:
      return null;
  }
}

// --- Sector/theme groups (Task 6) --------------------------------------------
// Mirrors backend/app/api/groups.py (GroupOut/GroupIn/GroupPatch/AssignIn/
// SeedOut) and the exposure/trend dict shapes in
// backend/app/services/groups/exposure.py + backend/app/api/groups.py.

export function getGroups(): Promise<HoldingGroup[]> {
  return apiFetch<HoldingGroup[]>("/api/groups");
}

export function createGroup(body: { name: string; color?: string }): Promise<HoldingGroup> {
  return apiFetch<HoldingGroup>("/api/groups", { method: "POST", body: JSON.stringify(body) });
}

export function updateGroup(
  id: number,
  body: { name?: string; color?: string; sort_order?: number },
): Promise<HoldingGroup> {
  return apiFetch<HoldingGroup>(`/api/groups/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteGroup(id: number): Promise<void> {
  return apiFetch<void>(`/api/groups/${id}`, { method: "DELETE" });
}

export function assignGroup(body: { symbol: string; group_id: number | null }): Promise<AssignResult> {
  return apiFetch<AssignResult>("/api/groups/assign", { method: "PUT", body: JSON.stringify(body) });
}

export function seedGroups(): Promise<SeedGroupsResult> {
  return apiFetch<SeedGroupsResult>("/api/groups/seed-from-sectors", { method: "POST" });
}

export function getGroupExposure(portfolioId?: number | null): Promise<GroupExposure> {
  const qs = portfolioId != null ? `?portfolio_id=${portfolioId}` : "";
  return apiFetch<GroupExposure>(`/api/groups/exposure${qs}`);
}

export function getGroupTrend(range: TrendRange = "30d"): Promise<GroupTrend> {
  return apiFetch<GroupTrend>(`/api/groups/trend?range=${range}`);
}
