import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import ChatPanel from "./ChatPanel";
import { streamSSE } from "../lib/sse";
import { ApiError } from "../lib/api";

vi.mock("../lib/sse", () => ({ streamSSE: vi.fn() }));

const mockStreamSSE = vi.mocked(streamSSE);

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const THREADS = [
  { id: 2, title: "REDUCE NVDA", portfolio_id: null, created_at: "2026-07-09T09:00:00Z" },
  { id: 1, title: "General", portfolio_id: null, created_at: "2026-07-08T09:00:00Z" },
];

function threadDetail(id: number, messages: unknown[] = []) {
  const thread = THREADS.find((t) => t.id === id);
  return { ...(thread ?? { id, title: "New chat", portfolio_id: null, created_at: "2026-07-09T10:00:00Z" }), messages };
}

function mockApi(opts?: { onThreadPost?: (body: { title: string; seed_context?: unknown }) => void }) {
  let nextId = 3;
  const created = new Map<number, ReturnType<typeof threadDetail>>();

  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url.endsWith("/api/guru/chat/threads") && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { title: string; seed_context?: unknown };
      opts?.onThreadPost?.(body);
      const id = nextId++;
      const thread = { id, title: body.title, portfolio_id: null, created_at: "2026-07-09T10:00:00Z" };
      created.set(id, { ...thread, messages: [] });
      return jsonResponse(thread, 201);
    }

    if (url.endsWith("/api/guru/chat/threads")) {
      return jsonResponse({ threads: THREADS });
    }

    const match = /\/api\/guru\/chat\/threads\/(\d+)$/.exec(url);
    if (match) {
      const id = Number(match[1]);
      if (created.has(id)) return jsonResponse(created.get(id));
      return jsonResponse(threadDetail(id));
    }

    throw new Error(`Unexpected fetch: ${method} ${url}`);
  });
}

function renderPanel(props?: { discuss?: string | null }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatPanel discuss={props?.discuss ?? null} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ChatPanel", () => {
  it("renders the thread list", async () => {
    mockApi();
    renderPanel();

    expect(await screen.findByRole("button", { name: /reduce nvda/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^general$/i })).toBeInTheDocument();
  });

  it("appends the user bubble immediately and grows the assistant reply as deltas arrive", async () => {
    let sent = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/guru/chat/threads")) return jsonResponse({ threads: THREADS });
      const match = /\/api\/guru\/chat\/threads\/(\d+)$/.exec(url);
      if (match && Number(match[1]) === 2) {
        const messages = sent
          ? [
              { id: 501, role: "user", content: "What about risk?", created_at: "2026-07-09T10:00:00Z" },
              { id: 502, role: "assistant", content: "Hello there", created_at: "2026-07-09T10:00:01Z" },
            ]
          : [];
        return jsonResponse(threadDetail(2, messages));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    let resolveDone: () => void = () => {};
    mockStreamSSE.mockImplementation(
      (_path, _body, handlers) =>
        new Promise<void>((resolve) => {
          handlers.onDelta("Hel");
          resolveDone = () => {
            handlers.onDelta("lo there");
            sent = true;
            handlers.onDone({ message_id: 99 });
            resolve();
          };
        }),
    );

    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });
    const input = screen.getByLabelText(/message/i);
    await user.type(input, "What about risk?");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("What about risk?")).toBeInTheDocument();
    expect(await screen.findByText(/^Hel$/)).toBeInTheDocument();
    expect(screen.getByText(/streaming…/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^send$/i })).toBeDisabled();

    await act(async () => {
      resolveDone();
    });

    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(screen.queryByText(/streaming…/i)).not.toBeInTheDocument();
  });

  it("shows a retry affordance on an error frame, keeping the user message", async () => {
    mockApi();
    mockStreamSSE.mockImplementation(async (_path, _body, handlers) => {
      handlers.onError("llm_error");
    });

    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });
    await user.type(screen.getByLabelText(/message/i), "Ping");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Ping")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText(/llm_error/i)).toBeInTheDocument();
  });

  it("POSTs a new thread when + New thread is clicked", async () => {
    let posted = false;
    mockApi({ onThreadPost: () => (posted = true) });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });
    await user.click(screen.getByRole("button", { name: /new thread/i }));

    expect(posted).toBe(true);
  });

  it("auto-creates a seeded thread from the discuss param and shows the context note", async () => {
    let postedBody: { title: string; seed_context?: unknown } | null = null;
    mockApi({ onThreadPost: (body) => (postedBody = body) });
    const idea = { symbol: "NVDA", action: "reduce", conviction: "med", rationale: "Valuation stretched." };

    renderPanel({ discuss: JSON.stringify(idea) });

    expect(await screen.findByText(/discussing:/i)).toBeInTheDocument();
    expect(screen.getByText(/from guru's take/i)).toBeInTheDocument();
    expect(postedBody).toEqual({ title: "REDUCE NVDA", seed_context: idea });
  });

  it("guards stale streaming callbacks after the user switches threads mid-send", async () => {
    mockApi();

    // `selectThread` itself resets `streaming`/`streamingText` to
    // false/"" on every thread switch, which would mask a missing
    // per-callback guard if thread B stayed idle (the streaming bubble
    // wouldn't render regardless of what A's late callbacks write into
    // shared state). So this test starts a SECOND, genuine send in thread
    // B — its streaming bubble is what A's leaked callbacks would corrupt
    // if `sentThreadId !== activeThreadIdRef.current` were removed from
    // onDelta/onDone.
    type Handlers = {
      onDelta: (text: string) => void;
      onDone: (d: { message_id: number }) => void;
      onError: (detail: string) => void;
    };
    const captured: Handlers[] = [];
    const resolvers: Array<() => void> = [];
    mockStreamSSE.mockImplementation(
      (_path, _body, handlers) =>
        new Promise<void>((resolve) => {
          captured.push(handlers);
          resolvers.push(resolve);
        }),
    );

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ChatPanel discuss={null} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Thread 2 ("REDUCE NVDA") is the newest thread and becomes active by default.
    await screen.findByRole("button", { name: /reduce nvda/i });
    await user.type(screen.getByLabelText(/message/i), "Thread A message");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Thread A message")).toBeInTheDocument();
    expect(captured).toHaveLength(1);

    // Switch to thread B ("General") before thread A's stream settles, then
    // start a genuine send in B so B has its own live streaming bubble.
    await user.click(screen.getByRole("button", { name: /^general$/i }));
    expect(await screen.findByText(/ask the guru anything/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/message/i), "Thread B message");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Thread B message")).toBeInTheDocument();
    expect(captured).toHaveLength(2);
    const [threadA, threadB] = captured;

    // Fire thread A's late delta frame ALONE first, while B's own stream is
    // live. If the onDelta guard were removed, this alone would splice
    // "leaked from thread A" into B's (shared) streamingText state, and
    // B's visible streaming bubble would render it — so this assertion
    // must fail without the guard.
    await act(async () => {
      threadA.onDelta("leaked from thread A");
    });

    expect(screen.queryByText(/leaked from thread A/i)).not.toBeInTheDocument();
    // B's own stream is still legitimately live.
    expect(screen.getByText(/streaming…/i)).toBeInTheDocument();

    // Now let thread A's done frame arrive. Its own invalidation must still
    // fire, but the reset (`streaming`/`streamingText`/`pendingUser`) must
    // not stomp thread B's still-in-flight send.
    await act(async () => {
      threadA.onDone({ message_id: 99 });
      resolvers[0]();
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["guru", "chat", "thread", 2] });
    expect(screen.queryByText(/leaked from thread A/i)).not.toBeInTheDocument();
    // B's send must still be visibly live — a removed onDone guard would
    // have reset `streaming` to false and cleared B's pending user bubble.
    expect(screen.getByText(/streaming…/i)).toBeInTheDocument();
    expect(screen.getByText("Thread B message")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^send$/i })).toBeDisabled();

    // Settle B's own send normally.
    await act(async () => {
      threadB.onDelta("Hi from B");
      threadB.onDone({ message_id: 100 });
      resolvers[1]();
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["guru", "chat", "thread", 1] });
    expect(screen.queryByText(/streaming…/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/message/i), "one more");
    expect(screen.getByRole("button", { name: /^send$/i })).not.toBeDisabled();
  });

  it("guards the catch block after streamSSE throws following a thread switch", async () => {
    mockApi();

    // streamSSE (mocked at the module level) throws AFTER the user has
    // already switched threads — simulating an ApiError from a late-settling
    // fetch (e.g. a 503 from `_require_provider` before any streaming
    // begins). The catch block in handleSend must guard its state writes
    // the same way the onDelta/onDone/onError handlers do.
    let rejectThreadA: (e: unknown) => void = () => {};
    mockStreamSSE.mockImplementation(
      (_path, _body, _handlers) =>
        new Promise<void>((_resolve, reject) => {
          rejectThreadA = reject;
        }),
    );

    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });
    await user.type(screen.getByLabelText(/message/i), "Thread A message");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Thread A message")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^general$/i }));
    expect(await screen.findByText(/ask the guru anything/i)).toBeInTheDocument();

    await act(async () => {
      rejectThreadA(new ApiError(503, "guru unavailable"));
    });

    expect(screen.queryByText(/failed to send/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/guru unavailable/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/message/i), "Thread B message");
    expect(screen.getByRole("button", { name: /^send$/i })).not.toBeDisabled();
  });

  it("guards a stale error frame after a thread switch, avoiding a misattributed retry banner", async () => {
    mockApi();

    let captured: { onError: (detail: string) => void } | null = null;
    let resolveThreadA: () => void = () => {};
    mockStreamSSE.mockImplementation(
      (_path, _body, handlers) =>
        new Promise<void>((resolve) => {
          captured = handlers;
          resolveThreadA = resolve;
        }),
    );

    const user = userEvent.setup();
    renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });
    await user.type(screen.getByLabelText(/message/i), "Thread A message");
    await user.click(screen.getByRole("button", { name: /^send$/i }));

    expect(await screen.findByText("Thread A message")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^general$/i }));
    expect(await screen.findByText(/ask the guru anything/i)).toBeInTheDocument();

    await act(async () => {
      captured?.onError("llm_error");
      resolveThreadA();
    });

    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/llm_error/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/message/i), "Thread B message");
    expect(screen.getByRole("button", { name: /^send$/i })).not.toBeDisabled();
  });

  it("has no detectable accessibility violations", async () => {
    mockApi();
    const { container } = renderPanel();

    await screen.findByRole("button", { name: /reduce nvda/i });

    expect(await axe(container)).toHaveNoViolations();
  });
});
