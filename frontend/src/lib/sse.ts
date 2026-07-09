import { ApiError } from "./api";

export async function streamSSE(
  path: string,
  body: unknown,
  handlers: {
    onDelta: (text: string) => void;
    onDone: (d: { message_id: number }) => void;
    onError: (detail: string) => void;
  },
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    throw new ApiError(resp.status, await resp.text().catch(() => resp.statusText));
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const event = /^event: (.*)$/m.exec(frame)?.[1];
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      if (!event || data === undefined) continue;
      const parsed = JSON.parse(data);
      if (event === "delta") handlers.onDelta(parsed.text);
      else if (event === "done") handlers.onDone(parsed);
      else if (event === "error") handlers.onError(parsed.detail);
    }
  }
}
