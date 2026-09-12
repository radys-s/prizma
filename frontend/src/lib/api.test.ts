import { describe, it, expect, vi } from "vitest";
import { streamChat } from "./api";

function mockReadableStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    async start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

describe("streamChat SSE parser & EOF handling", () => {
  it("calls onDone(false) when stream ends naturally without done event", async () => {
    const stream = mockReadableStream([
      "event: start\ndata: {\"conversation_id\": 1, \"capability\": \"code\"}\n\n",
      "event: delta\ndata: {\"content\": \"Hello \"}\n\n",
      "event: delta\ndata: {\"content\": \"world\"}\n\n",
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: stream,
    } as Response);

    const onStart = vi.fn();
    const onDelta = vi.fn();
    const onDone = vi.fn();

    await streamChat(
      "hi",
      null,
      { model: "auto", effort: "medium", search: "off", use_tools: false, allow_commands: false },
      { onStart, onDelta, onDone }
    );

    expect(onStart).toHaveBeenCalledWith(1, "code", undefined);
    expect(onDelta).toHaveBeenNthCalledWith(1, "Hello ");
    expect(onDelta).toHaveBeenNthCalledWith(2, "world");
    expect(onDone).toHaveBeenCalledWith(false);
  });

  it("handles events split across chunk boundaries and multiline data", async () => {
    const stream = mockReadableStream([
      "event: delta\ndata: {\"content\": \"line 1",
      "\\nline 2\"}\n\n",
    ]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: stream,
    } as Response);

    const onDelta = vi.fn();
    const onDone = vi.fn();

    await streamChat(
      "test",
      1,
      { model: "auto", effort: "medium", search: "off", use_tools: false, allow_commands: false },
      { onDelta, onDone }
    );

    expect(onDelta).toHaveBeenCalledWith("line 1\nline 2");
    expect(onDone).toHaveBeenCalledWith(false);
  });

  it("joins consecutive data: lines with a newline", async () => {
    // Spec SSE: víc `data:` řádků v jedné události je jedna hodnota spojená
    // `\n`. Backend dnes posílá kompaktní JSON na jednom řádku, takže by to
    // jinak nikdo nezjistil — až ve chvíli, kdy někdo do payloadu dá text.
    const stream = mockReadableStream([
      "event: delta\ndata: {\"content\":\ndata:  \"a b\"}\n\n",
    ]);
    mockFetch(stream);

    const onDelta = vi.fn();
    await run({ onDelta });

    expect(onDelta).toHaveBeenCalledWith("a b");
  });

  it("strips exactly one leading space, not all whitespace", async () => {
    const stream = mockReadableStream([
      "event: delta\ndata:  {\"content\": \"x\"}\n\n",
    ]);
    mockFetch(stream);

    const onDelta = vi.fn();
    const onError = vi.fn();
    await run({ onDelta, onError });

    // Po odstranění jedné mezery zbyde ` {...}`, což `JSON.parse` zvládne.
    expect(onDelta).toHaveBeenCalledWith("x");
    expect(onError).not.toHaveBeenCalled();
  });

  it("ignores comment lines used as keep-alive", async () => {
    const stream = mockReadableStream([
      ": ping\n\n",
      "event: delta\ndata: {\"content\": \"po pingu\"}\n\n",
    ]);
    mockFetch(stream);

    const onDelta = vi.fn();
    const onError = vi.fn();
    await run({ onDelta, onError });

    expect(onDelta).toHaveBeenCalledTimes(1);
    expect(onDelta).toHaveBeenCalledWith("po pingu");
    expect(onError).not.toHaveBeenCalled();
  });

  it("does not announce a second ending after a real done event", async () => {
    // `onDone(false)` na EOF je záchrana pro spojení, které umřelo bez
    // rozloučení. Zavolat ho i po řádném `done` znamená přepsat `revised`
    // na false u odpovědi, která revizí prošla.
    const stream = mockReadableStream([
      "event: delta\ndata: {\"content\": \"hotovo\"}\n\n",
      "event: done\ndata: {\"revised\": true, \"message_id\": 7, \"created_at\": \"2026-09-12T10:00:00\"}\n\n",
    ]);
    mockFetch(stream);

    const onDone = vi.fn();
    await run({ onDone });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(true, {
      latency_ms: undefined,
      tokens: undefined,
      reasoning_ms: undefined,
      message_id: 7,
      created_at: "2026-09-12T10:00:00",
    });
  });

  it("keeps a final event that arrived without its blank line", async () => {
    const stream = mockReadableStream([
      "event: done\ndata: {\"message_id\": 12}",
    ]);
    mockFetch(stream);

    const onDone = vi.fn();
    await run({ onDone });

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone.mock.calls[0][1]).toMatchObject({ message_id: 12 });
  });

  it("passes the question's id from the start event", async () => {
    const stream = mockReadableStream([
      "event: start\ndata: {\"conversation_id\": 3, \"capability\": \"general\", \"user_message_id\": 41}\n\n",
    ]);
    mockFetch(stream);

    const onStart = vi.fn();
    await run({ onStart });

    expect(onStart).toHaveBeenCalledWith(3, "general", 41);
  });
});

function mockFetch(stream: ReadableStream<Uint8Array>): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
    ok: true,
    status: 200,
    body: stream,
  } as Response);
}

function run(handlers: Parameters<typeof streamChat>[3]): Promise<void> {
  return streamChat(
    "test",
    1,
    { model: "auto", effort: "medium", search: "off", use_tools: false, allow_commands: false },
    handlers
  );
}
