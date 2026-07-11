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
