import { Fragment, useEffect, useRef, useState } from "react";
import { streamChat, type ChatEvent } from "../api/sse";
import { ComponentRenderer } from "./ComponentRenderer";

type Part =
  | { kind: "text"; text: string }
  | { kind: "component"; component: string; props: Record<string, unknown> }
  | { kind: "guard"; action: string; count: number }
  | { kind: "error"; text: string };

interface Message {
  id: number;
  role: "user" | "assistant";
  text?: string;
  parts: Part[];
  status?: string | null;
  done?: boolean;
}

const SUGGESTIONS = [
  "Which of my merchants are losing the most this month?",
  "Draft a pitch, consultative tone",
  "Show me why",
  "Which merchants already have Optimizer but still leak?",
];
// Components that sit side by side when they arrive together.
const PAIRABLE = new Set(["RootCauseCard", "TrendChart"]);

export function ChatPane() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversation = useRef<string | null>(null);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Block body on purpose: newer browsers return a Promise from smooth scrollIntoView,
    // which React would otherwise treat as an effect cleanup.
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  async function send(text: string) {
    const msg = text.trim();
    if (!msg || busy) return;
    setInput("");
    setBusy(true);
    const aid = nextId.current + 1;
    nextId.current += 2;
    setMessages((m) => [...m, { id: aid - 1, role: "user", text: msg, parts: [] },
      { id: aid, role: "assistant", parts: [], status: "Thinking…" }]);
    const update = (fn: (m: Message) => Message) => setMessages((all) => all.map((m) => (m.id === aid ? fn(m) : m)));

    const onEvent = (e: ChatEvent) => {
      switch (e.type) {
        case "start":
          conversation.current = e.conversation_id;
          break;
        case "status":
          update((m) => ({ ...m, status: e.text }));
          break;
        case "text":
          update((m) => ({ ...m, parts: [...m.parts, { kind: "text", text: e.text }] }));
          break;
        case "component":
          update((m) => ({ ...m, parts: [...m.parts, { kind: "component", component: e.component, props: e.props }] }));
          break;
        case "guard":
          update((m) => ({ ...m, parts: [...m.parts, { kind: "guard", action: e.action, count: e.violations.length }] }));
          break;
        case "error":
          update((m) => ({ ...m, parts: [...m.parts, { kind: "error", text: e.text }] }));
          break;
        case "done":
          update((m) => ({ ...m, status: null, done: true }));
          break;
      }
    };
    try {
      await streamChat(msg, conversation.current, onEvent);
    } catch (err) {
      update((m) => ({ ...m, parts: [...m.parts, { kind: "error", text: `Connection problem: ${(err as Error).message}` }] }));
    } finally {
      update((m) => ({ ...m, status: null, done: true }));
      setBusy(false);
    }
  }

  return (
    <main className="chat">
      <div className="messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty">
            <h1>Where are your merchants losing money?</h1>
            <p>Ask about failed payments across your book. Every figure comes from the detectors, with evidence one click away.</p>
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="msg-user">{m.text}</div>
          ) : (
            <div key={m.id} className="msg-assistant">
              {groupParts(m.parts).map((g, i) =>
                Array.isArray(g) ? (
                  <div key={i} className="card-row">
                    {g.map((p, j) => <ComponentRenderer key={j} raw={p} onAsk={send} />)}
                  </div>
                ) : (
                  <Fragment key={i}>{renderPart(g, send)}</Fragment>
                ),
              )}
              {m.status && (
                <div className="msg-status"><span className="dot-pulse" aria-hidden />{m.status}</div>
              )}
            </div>
          ),
        )}
        <div ref={bottom} />
      </div>
      <div className="composer">
        <div className="suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="chip" disabled={busy} onClick={() => send(s)}>{s}</button>
          ))}
        </div>
        <form className="input-row" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about your merchants…"
            aria-label="Message" disabled={busy} />
          <button className="btn btn-primary" type="submit" disabled={busy || !input.trim()}>Send</button>
        </form>
      </div>
    </main>
  );
}

type ComponentPart = Extract<Part, { kind: "component" }>;

function groupParts(parts: Part[]): (Part | ComponentPart[])[] {
  const out: (Part | ComponentPart[])[] = [];
  for (const p of parts) {
    const last = out[out.length - 1];
    if (p.kind === "component" && PAIRABLE.has(p.component)) {
      if (Array.isArray(last)) last.push(p);
      else out.push([p]);
    } else out.push(p);
  }
  return out;
}

function renderPart(p: Part, onAsk: (m: string) => void) {
  switch (p.kind) {
    case "text":
      return <div className="msg-text">{p.text}</div>;
    case "component":
      return <ComponentRenderer raw={p} onAsk={onAsk} />;
    case "guard":
      return (
        <div className="guard-note" title="The number guard blocks figures that don't appear in tool results">
          {p.action === "regenerate"
            ? `Number guard held back a reply with ${p.count} unverified figure${p.count > 1 ? "s" : ""} and asked for a rewrite.`
            : `Number guard removed ${p.count} unverified figure${p.count > 1 ? "s" : ""}.`}
        </div>
      );
    case "error":
      return <div className="msg-error">{p.text}</div>;
  }
}
