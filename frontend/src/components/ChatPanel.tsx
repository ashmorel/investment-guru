import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch } from "../lib/api";
import { streamSSE } from "../lib/sse";
import type { ChatMessage, ChatThread } from "../lib/types";

interface ThreadsResponse {
  threads: ChatThread[];
}

interface ThreadDetail extends ChatThread {
  messages: ChatMessage[];
}

interface SeedIdea {
  symbol?: string | null;
  action?: string;
  conviction?: string;
  rationale?: string;
}

function ideaLabel(idea: SeedIdea): string {
  const parts = [idea.action?.toUpperCase(), idea.symbol ?? undefined, idea.conviction?.toUpperCase()].filter(
    (p): p is string => Boolean(p),
  );
  return parts.length > 0 ? `${parts.join(" · ")} (from Guru's take)` : "Guru's take";
}

function ideaTitle(idea: SeedIdea): string {
  const action = idea.action?.toUpperCase();
  if (action && idea.symbol) return `${action} ${idea.symbol}`;
  return idea.symbol ?? "New chat";
}

function Bubble({ role, content }: { role: "user" | "assistant"; content: string }) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <p
        className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
          isUser ? "bg-accent text-white" : "border border-border bg-surface text-text"
        }`}
      >
        {content}
      </p>
    </div>
  );
}

export default function ChatPanel({ discuss }: { discuss?: string | null }) {
  const qc = useQueryClient();
  const [activeThreadId, setActiveThreadId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [seedNote, setSeedNote] = useState<{ threadId: number; label: string } | null>(null);
  const seededRef = useRef<string | null>(null);

  const threadsQuery = useQuery({
    queryKey: ["guru", "chat", "threads"],
    queryFn: () => apiFetch<ThreadsResponse>("/api/guru/chat/threads"),
  });

  const threadQuery = useQuery({
    queryKey: ["guru", "chat", "thread", activeThreadId],
    queryFn: () => apiFetch<ThreadDetail>(`/api/guru/chat/threads/${activeThreadId}`),
    enabled: activeThreadId !== null,
  });

  const createThread = useMutation({
    mutationFn: (input: { title: string; seed_context?: unknown }) =>
      apiFetch<ChatThread>("/api/guru/chat/threads", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: (thread) => {
      qc.invalidateQueries({ queryKey: ["guru", "chat", "threads"] });
      setActiveThreadId(thread.id);
      setPendingUser(null);
      setStreamingText("");
      setSendError(null);
    },
  });

  const threads = threadsQuery.data?.threads ?? [];

  // Auto-create a seeded thread the first time a `discuss` payload arrives.
  useEffect(() => {
    if (!discuss || seededRef.current === discuss) return;
    seededRef.current = discuss;
    let idea: SeedIdea;
    try {
      idea = JSON.parse(discuss) as SeedIdea;
    } catch {
      return;
    }
    const label = ideaLabel(idea);
    createThread.mutate(
      { title: ideaTitle(idea), seed_context: idea },
      { onSuccess: (thread) => setSeedNote({ threadId: thread.id, label }) },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discuss]);

  // Default to the newest thread once loaded, unless a seeded thread is being created.
  useEffect(() => {
    if (discuss || activeThreadId !== null) return;
    const newest = threadsQuery.data?.threads[0];
    if (newest) setActiveThreadId(newest.id);
  }, [discuss, activeThreadId, threadsQuery.data]);

  function selectThread(id: number) {
    setActiveThreadId(id);
    setPendingUser(null);
    setStreamingText("");
    setSendError(null);
    setStreaming(false);
  }

  function handleNewThread() {
    createThread.mutate({ title: "New chat" });
  }

  async function handleSend(content: string) {
    const trimmed = content.trim();
    if (!trimmed || activeThreadId === null || streaming) return;
    setStreaming(true);
    setStreamingText("");
    setPendingUser(trimmed);
    setSendError(null);
    setDraft("");
    try {
      await streamSSE(
        `/api/guru/chat/threads/${activeThreadId}/messages`,
        { content: trimmed },
        {
          onDelta: (text) => setStreamingText((prev) => prev + text),
          onDone: () => {
            qc.invalidateQueries({ queryKey: ["guru", "chat", "thread", activeThreadId] });
            setStreaming(false);
            setStreamingText("");
            setPendingUser(null);
          },
          onError: (detail) => {
            setStreaming(false);
            setSendError(detail);
          },
        },
      );
    } catch (e) {
      setStreaming(false);
      setSendError(e instanceof ApiError ? e.message : "Something went wrong.");
    }
  }

  const persisted = threadQuery.data?.messages ?? [];
  const showSeedNote = seedNote !== null && seedNote.threadId === activeThreadId;
  const showEmptyState = persisted.length === 0 && pendingUser === null && !streaming;

  return (
    <section className="flex flex-col rounded-xl border border-border bg-surface p-5 shadow lg:min-h-[32rem]">
      <div className="flex items-center justify-between">
        <h2 className="font-medium text-text">Chat with the Guru</h2>
        <button
          type="button"
          onClick={handleNewThread}
          disabled={createThread.isPending}
          className="text-sm text-accent underline disabled:opacity-50"
        >
          + New thread
        </button>
      </div>

      {threads.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {threads.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => selectThread(t.id)}
              aria-pressed={t.id === activeThreadId}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                t.id === activeThreadId
                  ? "bg-accent-subtle text-accent"
                  : "border border-border text-muted"
              }`}
            >
              {t.title}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex-1 space-y-3 overflow-y-auto rounded-lg bg-bg p-3">
        {showSeedNote && (
          <p className="rounded-md bg-accent-subtle px-3 py-2 text-xs text-accent">
            Discussing: {seedNote?.label}
          </p>
        )}

        {showEmptyState && (
          <p className="text-sm text-muted">Ask the Guru anything about your portfolio.</p>
        )}

        {persisted.map((m) => (
          <Bubble key={m.id} role={m.role} content={m.content} />
        ))}

        {pendingUser !== null && <Bubble role="user" content={pendingUser} />}

        {streaming && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text">
              <p className="whitespace-pre-wrap">{streamingText}</p>
              <p className="mt-1 text-xs text-muted">streaming…</p>
            </div>
          </div>
        )}
      </div>

      {sendError && (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md bg-loss/10 px-3 py-2 text-sm text-loss">
          <span>Failed to send: {sendError}</span>
          <button
            type="button"
            onClick={() => handleSend(pendingUser ?? "")}
            className="shrink-0 text-xs font-medium underline"
          >
            Retry
          </button>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend(draft);
        }}
        className="mt-3 flex items-center gap-2"
      >
        <label htmlFor="chat-message" className="sr-only">
          Message
        </label>
        <input
          id="chat-message"
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={activeThreadId === null}
          placeholder="Ask the Guru…"
          className="flex-1 rounded-md border border-border px-3 py-2 text-sm text-text disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={streaming || !draft.trim() || activeThreadId === null}
          className="rounded-md bg-accent px-3 py-2 text-sm text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>

      <p className="mt-3 text-center text-xs text-muted">
        The Guru is not regulated financial advice.
      </p>
    </section>
  );
}
