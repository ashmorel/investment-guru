import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { streamSSE } from "./sse";

function streamResponse(chunks: string[], status = 200) {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status });
}

describe("streamSSE", () => {
  it("parses delta and done frames in order", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse([
        'event: delta\ndata: {"text":"Hi"}\n\n',
        'event: delta\ndata: {"text":" there"}\n\n',
        'event: done\ndata: {"message_id":42}\n\n',
      ]),
    );

    const calls: string[] = [];
    const onDelta = vi.fn((text: string) => calls.push(`delta:${text}`));
    const onDone = vi.fn((d: { message_id: number }) => calls.push(`done:${d.message_id}`));
    const onError = vi.fn((detail: string) => calls.push(`error:${detail}`));

    await streamSSE("/api/guru/chat/threads/1/messages", { content: "hi" }, {
      onDelta,
      onDone,
      onError,
    });

    expect(calls).toEqual(["delta:Hi", "delta: there", "done:42"]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("parses a frame split across two chunks", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse(['event: delta\ndata: {"te', 'xt":"Hi"}\n\n']),
    );

    const onDelta = vi.fn();
    await streamSSE("/api/guru/chat/threads/1/messages", { content: "hi" }, {
      onDelta,
      onDone: vi.fn(),
      onError: vi.fn(),
    });

    expect(onDelta).toHaveBeenCalledWith("Hi");
  });

  it("calls onError for an error frame", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      streamResponse(['event: error\ndata: {"detail":"llm_error"}\n\n']),
    );

    const onError = vi.fn();
    await streamSSE("/api/guru/chat/threads/1/messages", { content: "hi" }, {
      onDelta: vi.fn(),
      onDone: vi.fn(),
      onError,
    });

    expect(onError).toHaveBeenCalledWith("llm_error");
  });

  it("throws ApiError for a non-2xx response before streaming", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "Guru is not configured" }), { status: 503 }),
    );

    let caught: unknown;
    try {
      await streamSSE("/api/guru/chat/threads/1/messages", { content: "hi" }, {
        onDelta: vi.fn(),
        onDone: vi.fn(),
        onError: vi.fn(),
      });
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as InstanceType<typeof ApiError>).status).toBe(503);
  });
});
