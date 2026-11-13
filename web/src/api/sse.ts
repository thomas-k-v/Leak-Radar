// POST + Server-Sent Events. EventSource only supports GET, so we read the fetch body stream ourselves.

export type ChatEvent =
  | { type: "start"; conversation_id: string; provider: string }
  | { type: "status"; text: string }
  | { type: "text"; text: string }
  | { type: "component"; component: string; props: Record<string, unknown> }
  | { type: "guard"; action: "regenerate" | "redacted"; violations: string[] }
  | { type: "error"; text: string }
  | { type: "done"; conversation_id: string };

/** Splits an SSE byte stream into JSON events. Exported for tests. */
export function parseSseChunk(buffer: string): { events: ChatEvent[]; rest: string } {
  const events: ChatEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n");
    if (data) events.push(JSON.parse(data) as ChatEvent);
  }
  return { events, rest };
}

export async function streamChat(
  message: string,
  conversationId: string | null,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = parseSseChunk(buffer);
    buffer = rest;
    events.forEach(onEvent);
  }
}
