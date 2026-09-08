import type {
  AppSettings,
  AuthState,
  TwoFactorChallenge,
  CompactInfo,
  Conversation,
  KeyTestResult,
  LocalStatus,
  Message,
  MessageHit,
  Provider,
  RoutingOptions,
  RoutingPlan,
  Source,
  TaskPart,
} from "./types";

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* empty or non-JSON body — fall through to the generic message */
  }
  return fallback;
}

/* ------------------------------------------------------------------ csrf -- */

/**
 * The backend sets a readable `ma_csrf` cookie next to the HttpOnly session
 * cookie and expects it echoed back in a header on every write. A cross-origin
 * page can make the browser *send* the session cookie but cannot read this one,
 * so echoing it is what proves the request came from our own UI.
 */
function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)ma_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** Every mutating request goes through here so none can forget the header. */
function write(url: string, method: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = { "x-csrf-token": csrfToken() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * Raised when the backend says the session is gone. App.tsx catches this and
 * drops back to the login screen instead of showing a confusing error.
 */
export class Unauthenticated extends Error {
  constructor() {
    super("Přihlášení vypršelo.");
    this.name = "Unauthenticated";
  }
}

function assertAuthed(res: Response): void {
  if (res.status === 401) throw new Unauthenticated();
}

/* ------------------------------------------------------------------ auth -- */

export async function fetchAuthState(): Promise<AuthState> {
  const res = await fetch("/api/auth/state");
  if (!res.ok) throw new Error(await detailOf(res, `Stav přihlášení selhal (${res.status})`));
  return res.json();
}

export async function setupPasscode(passcode: string): Promise<void> {
  const res = await write("/api/auth/setup", "POST", { passcode });
  if (!res.ok) throw new Error(await detailOf(res, `Vytvoření hesla selhalo (${res.status})`));
}

export async function loginWithPasscode(passcode: string): Promise<void> {
  const res = await write("/api/auth/login", "POST", { passcode });
  if (!res.ok) throw new Error(await detailOf(res, `Přihlášení selhalo (${res.status})`));
}

export async function logout(): Promise<void> {
  await write("/api/auth/logout", "POST", {});
}

/** Set or change the access code. Requires an existing session — this is how a
 *  server account claimed with Google gets a four-digit code afterwards. */
export async function setAccessCode(passcode: string): Promise<void> {
  const res = await write("/api/auth/passcode", "POST", { passcode });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Uložení kódu selhalo (${res.status})`));
}

/** Full page navigation on purpose — the OAuth round trip leaves the app. */
export function startOAuth(providerId: string): void {
  window.location.href = `/api/auth/oauth/${providerId}/start`;
}

/* ------------------------------------------------------------- email 2fa -- */

/**
 * Both of these answer with a *challenge*, never a session. That is the whole
 * shape of the second factor: the password proves who you say you are, and the
 * code proves you can reach the inbox or the phone. Only `verifyTwoFactor` and
 * `verifyTotp` below ever hand back cookies.
 */
export async function registerEmail(
  email: string,
  password: string,
): Promise<TwoFactorChallenge> {
  const res = await write("/api/auth/email/register", "POST", { email, password });
  if (!res.ok) throw new Error(await detailOf(res, `Registrace selhala (${res.status})`));
  return res.json();
}

export async function loginWithEmail(
  email: string,
  password: string,
): Promise<TwoFactorChallenge> {
  const res = await write("/api/auth/email/login", "POST", { email, password });
  if (!res.ok) throw new Error(await detailOf(res, `Přihlášení selhalo (${res.status})`));
  return res.json();
}

/** The emailed six-digit code. */
export async function verifyTwoFactor(challengeId: number, code: string): Promise<void> {
  const res = await write("/api/auth/email/verify", "POST", {
    challenge_id: challengeId,
    code,
  });
  if (!res.ok) throw new Error(await detailOf(res, `Ověření selhalo (${res.status})`));
}

/** An authenticator-app code, or one of the eight recovery codes. */
export async function verifyTotp(challengeId: number, code: string): Promise<void> {
  const res = await write("/api/auth/totp/verify", "POST", {
    challenge_id: challengeId,
    code,
  });
  if (!res.ok) throw new Error(await detailOf(res, `Ověření selhalo (${res.status})`));
}

export async function resendCode(challengeId: number): Promise<void> {
  const res = await write("/api/auth/email/resend", "POST", { challenge_id: challengeId });
  if (!res.ok) throw new Error(await detailOf(res, `Odeslání selhalo (${res.status})`));
}

/* ------------------------------------------------------------------ totp -- */

export interface TotpSetup {
  secret: string;
  uri: string;
  digits: number;
}

export async function startTotpSetup(): Promise<TotpSetup> {
  const res = await write("/api/auth/totp/setup", "POST", {});
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Nastavení selhalo (${res.status})`));
  return res.json();
}

/** Returns the eight recovery codes, shown once and never retrievable again. */
export async function confirmTotp(code: string): Promise<string[]> {
  const res = await write("/api/auth/totp/confirm", "POST", { code });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Potvrzení selhalo (${res.status})`));
  return ((await res.json()) as { recovery_codes: string[] }).recovery_codes;
}

export async function disableTotp(): Promise<void> {
  const res = await write("/api/auth/totp/disable", "POST", {});
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Vypnutí selhalo (${res.status})`));
}

export async function unlinkIdentity(identityId: number): Promise<void> {
  const res = await write(`/api/auth/identities/${identityId}`, "DELETE");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Odpojení selhalo (${res.status})`));
}

/* ------------------------------------------------------------ providers -- */

export async function fetchProviders(): Promise<Provider[]> {
  const res = await fetch("/api/providers");
  assertAuthed(res);
  return res.json();
}

export async function saveProviderKey(id: string, apiKey: string): Promise<void> {
  const res = await write(`/api/providers/${id}/key`, "PUT", { api_key: apiKey });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Uložení selhalo (${res.status})`));
}

export async function deleteProviderKey(id: string): Promise<void> {
  const res = await write(`/api/providers/${id}/key`, "DELETE");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Smazání selhalo (${res.status})`));
}

export async function testProviderKey(id: string): Promise<KeyTestResult> {
  const res = await write(`/api/providers/${id}/test`, "POST");
  assertAuthed(res);
  if (!res.ok) {
    return {
      ok: false,
      level: "error",
      detail: await detailOf(res, `Test selhal (${res.status})`),
    };
  }
  return res.json();
}

/**
 * Asks the backend to open the provider's key page in the OS default browser.
 * Falls back to a new tab if the backend can't (e.g. no default browser set).
 */
export async function openSignupPage(provider: Provider): Promise<void> {
  try {
    const res = await write(`/api/providers/${provider.id}/open-signup`, "POST");
    if (res.ok) {
      const body = (await res.json()) as { opened?: boolean };
      if (body.opened) return;
    }
  } catch {
    /* backend unreachable — the tab fallback below still works */
  }
  window.open(provider.signup_url, "_blank", "noopener,noreferrer");
}

/* ----------------------------------------------------------- local llm -- */

/**
 * A model running on this machine, wired in as one more provider.
 *
 * All four go through `write()` even though the first only reads: the backend
 * gates them on local mode and the read is what tells the UI whether to render
 * the section at all, so a stale cached answer here would show a form that
 * cannot work. `fetch` without a body is not cached by the browser anyway; the
 * consistency is for the next person reading this file.
 */
export async function fetchLocalStatus(): Promise<LocalStatus> {
  const res = await fetch("/api/providers/local");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Načtení selhalo (${res.status})`));
  return res.json();
}

/** Asks the endpoint which models it serves, so the slug isn't typed blind. */
export async function probeLocal(baseUrl: string): Promise<string[]> {
  const res = await write("/api/providers/local/probe", "POST", { base_url: baseUrl });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Endpoint neodpověděl (${res.status})`));
  const body = (await res.json()) as { models: string[] };
  return body.models;
}

export async function saveLocal(
  baseUrl: string,
  model: string,
  label: string,
): Promise<void> {
  const res = await write("/api/providers/local", "PUT", {
    base_url: baseUrl,
    model,
    label,
  });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Uložení selhalo (${res.status})`));
}

export async function clearLocal(): Promise<void> {
  const res = await write("/api/providers/local", "DELETE");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Odpojení selhalo (${res.status})`));
}

/* ------------------------------------------------------------ settings -- */

/**
 * App-level switches. Only one so far — whether deep research is allowed to
 * fetch pages in full through Jina Reader — but it lives on the server rather
 * than in localStorage because the *backend* is what acts on it, and a toggle
 * the browser forgot would silently change what leaves this machine.
 */
export async function fetchSettings(): Promise<AppSettings> {
  const res = await fetch("/api/settings");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Nastavení selhalo (${res.status})`));
  return res.json();
}

/** What the dialog may change. `custom_instructions_max` is server-owned. */
export type SettingsPatch = Partial<Omit<AppSettings, "custom_instructions_max">>;

export async function saveSettings(patch: SettingsPatch): Promise<AppSettings> {
  const res = await write("/api/settings", "PUT", patch);
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Uložení nastavení selhalo (${res.status})`));
  return res.json();
}

/* ------------------------------------------------------------- routing -- */

/**
 * The contents of the model and effort pickers.
 *
 * Fetched rather than hardcoded so a provider added to the backend registry
 * appears in the picker on the next reload, and a renamed effort tier renames
 * itself here — the two can't drift apart.
 */
export async function fetchRoutingOptions(): Promise<RoutingOptions> {
  const res = await fetch("/api/routing/options");
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Nastavení routeru selhalo (${res.status})`));
  return res.json();
}

/* -------------------------------------------------------- conversations -- */

export async function fetchConversations(): Promise<Conversation[]> {
  const res = await fetch("/api/conversations");
  assertAuthed(res);
  return res.json();
}

/**
 * `sources` is stored as a JSON TEXT column. The server decodes it, but a
 * message can still arrive encoded — an older backend, or a shape that
 * changes again later — so both are accepted here, at the one boundary where
 * stored messages enter the app. Every component below can then treat
 * `Message.sources` as a real array.
 *
 * Anything unreadable degrades to no sources — never to a crashed
 * conversation.
 */
function hydrate(message: Message & { sources?: unknown }): Message {
  const raw = message.sources;
  let parsed: unknown = raw;
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { ...message, sources: null };
    }
  }
  const sources = Array.isArray(parsed)
    ? (parsed.filter(
        (s) => s && typeof s === "object" && typeof (s as Source).url === "string"
      ) as Source[])
    : [];
  return { ...message, sources: sources.length > 0 ? sources : null };
}

export async function fetchConversation(
  id: number
): Promise<{ conversation: Conversation; messages: Message[] }> {
  const res = await fetch(`/api/conversations/${id}`);
  assertAuthed(res);
  const data = (await res.json()) as { conversation: Conversation; messages: Message[] };
  return { ...data, messages: data.messages.map(hydrate) };
}

export async function renameConversation(id: number, title: string): Promise<Conversation> {
  const res = await write(`/api/conversations/${id}`, "PATCH", { title });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Přejmenování selhalo (${res.status})`));
  return res.json();
}

export async function deleteConversation(id: number): Promise<void> {
  const res = await write(`/api/conversations/${id}`, "DELETE");
  assertAuthed(res);
}

export async function pinConversation(id: number, pinned: boolean): Promise<Conversation> {
  const res = await write(`/api/conversations/${id}/pin`, "POST", { pinned });
  assertAuthed(res);
  if (!res.ok) throw new Error(await detailOf(res, `Připnutí selhalo (${res.status})`));
  return res.json();
}

/**
 * Hits inside message *bodies* — the other half of the sidebar's title filter.
 *
 * A short or empty query is answered locally rather than by the server: the box
 * fires on every keystroke, and `a` would match most of the database.
 */
export async function searchMessages(query: string): Promise<MessageHit[]> {
  const needle = query.trim();
  if (needle.length < 2) return [];
  const res = await fetch(`/api/conversations/search?q=${encodeURIComponent(needle)}`);
  assertAuthed(res);
  if (!res.ok) return [];
  const data = (await res.json()) as { results?: MessageHit[] };
  return Array.isArray(data.results) ? data.results : [];
}

/* ---------------------------------------------------------------- chat --- */

export interface StreamHandlers {
  onStart?: (conversationId: number, capability: string) => void;
  /** The thread was too long, so its older turns were folded into a summary. */
  onCompact?: (info: CompactInfo) => void;
  /** The front model's decision, before a single answer token arrives. */
  onPlan?: (plan: RoutingPlan) => void;
  /** The searcher is off. `queries` is what it is about to look up. */
  onSearchStart?: (depth: string, queries: string[]) => void;
  /** Search finished — these are the pages the answer may cite. */
  onSearchDone?: (sources: Source[], partial: boolean) => void;
  /** No searcher answered. The model answers from its own knowledge instead. */
  onSearchAbort?: (reason: string) => void;
  /** Deep mode only: the top hits are being fetched in full. */
  onReadStart?: (urls: string[]) => void;
  /** Pro/Max on a hard task: it was split, each part goes to its own model. */
  onSplitStart?: (parts: TaskPart[]) => void;
  /** One part came back. `ok` false means that model produced nothing. */
  onPartDone?: (part: TaskPart) => void;
  /** No split happened — the answer is written the ordinary way. */
  onSplitAbort?: (reason: string) => void;
  /** `note` is set when a pinned provider had to be swapped out. */
  onMeta?: (providerId: string, modelName: string, note: string) => void;
  onDelta?: (content: string) => void;
  /** Max tier: the draft is done and is about to be rewritten in place. */
  onReviewStart?: () => void;
  /** The rewrite failed — put the draft back. */
  onReviewAbort?: () => void;
  /** `stats` is absent when the stream was aborted before the done event. */
  onDone?: (revised: boolean, stats?: DoneStats) => void;
  onError?: (message: string) => void;
  onUnauthenticated?: () => void;
}

/** What an answer cost. Both are best-effort: not every provider reports usage. */
export interface DoneStats {
  latency_ms?: number;
  tokens?: number;
}

/** What the pickers contribute to a chat request. */
export interface RoutingChoice {
  /** Provider id, or "auto" for full routing. */
  model: string;
  effort: string;
  /** Globe control: "auto" | "web" | "deep" | "off". */
  search: string;
}

export function streamChat(
  message: string,
  conversationId: number | null,
  choice: RoutingChoice,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  return runStream(
    "/api/chat/stream",
    { message, conversation_id: conversationId, ...choice },
    handlers,
    signal
  );
}

export function regenerate(
  conversationId: number,
  choice: RoutingChoice,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  return runStream(
    "/api/chat/regenerate",
    { conversation_id: conversationId, ...choice },
    handlers,
    signal
  );
}

/**
 * Rewrites one user message and re-runs the thread from there.
 *
 * Everything after the edited turn is dropped server-side, so the caller must
 * truncate its own list to match — see `editMessage` in App.tsx.
 */
export function editMessage(
  conversationId: number,
  messageId: number,
  content: string,
  choice: RoutingChoice,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  return runStream(
    "/api/chat/edit",
    { conversation_id: conversationId, message_id: messageId, content, ...choice },
    handlers,
    signal
  );
}

/**
 * Reads the backend's SSE stream from a POST fetch — EventSource can't POST.
 *
 * An abort is the user pressing stop, not a failure: it resolves through
 * onDone so the UI keeps whatever text already arrived.
 */
async function runStream(
  url: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrf-token": csrfToken() },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (isAbort(err)) {
      handlers.onDone?.(false);
      return;
    }
    handlers.onError?.("Backend neodpovídá — běží uvicorn na portu 8000?");
    return;
  }

  if (res.status === 401) {
    handlers.onUnauthenticated?.();
    return;
  }

  if (!res.ok || !res.body) {
    handlers.onError?.(await detailOf(res, `Server vrátil ${res.status}`));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIndex: number;
      while ((sepIndex = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const rawEvent = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex).replace(/^\r?\n\r?\n/, "");
        handleEvent(rawEvent, handlers);
      }
    }
  } catch (err) {
    if (isAbort(err)) {
      handlers.onDone?.(false);
      return;
    }
    handlers.onError?.("Spojení se serverem se přerušilo.");
  }
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function handleEvent(raw: string, handlers: StreamHandlers): void {
  let eventType = "message";
  let data = "";
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;

  let payload: {
    conversation_id?: number;
    capability?: string;
    complexity?: string;
    goal?: string;
    source?: string;
    planner_model?: string | null;
    effort?: string;
    provider_id?: string;
    model_name?: string;
    content?: string;
    note?: string;
    revised?: boolean;
    needs_search?: boolean;
    search?: string;
    depth?: string;
    queries?: string[];
    sources?: Source[];
    partial?: boolean;
    reason?: string;
    urls?: string[];
    parts?: { index: number; title: string }[];
    index?: number;
    title?: string;
    display_name?: string;
    model?: string;
    ok?: boolean;
    ms?: number;
    latency_ms?: number;
    tokens?: number;
    folded?: number;
    kept?: number;
    fallback?: boolean;
  };
  try {
    payload = JSON.parse(data);
  } catch {
    return;
  }

  switch (eventType) {
    case "start":
      handlers.onStart?.(payload.conversation_id!, payload.capability!);
      break;
    case "compact":
      handlers.onCompact?.({
        folded: payload.folded ?? 0,
        kept: payload.kept ?? 0,
        fallback: payload.fallback === true,
      });
      break;
    case "plan":
      handlers.onPlan?.({
        capability: payload.capability ?? "",
        complexity: payload.complexity ?? "",
        goal: payload.goal ?? "",
        source: payload.source ?? "heuristic",
        planner_model: payload.planner_model ?? null,
        effort: payload.effort ?? "",
        needs_search: payload.needs_search === true,
        search: payload.search ?? "off",
      });
      break;
    case "search_start":
      handlers.onSearchStart?.(payload.depth ?? "web", payload.queries ?? []);
      break;
    case "search_done":
      handlers.onSearchDone?.(payload.sources ?? [], payload.partial === true);
      break;
    case "search_abort":
      handlers.onSearchAbort?.(payload.reason ?? "");
      break;
    case "read_start":
      handlers.onReadStart?.(payload.urls ?? []);
      break;
    case "split_start":
      handlers.onSplitStart?.(payload.parts ?? []);
      break;
    case "part_done":
      if (payload.index !== undefined) {
        handlers.onPartDone?.({
          index: payload.index,
          title: payload.title ?? "",
          display_name: payload.display_name,
          provider_id: payload.provider_id,
          model: payload.model,
          ok: payload.ok === true,
          ms: payload.ms,
        });
      }
      break;
    case "split_abort":
      handlers.onSplitAbort?.(payload.reason ?? "");
      break;
    case "meta":
      if (payload.provider_id && payload.model_name) {
        handlers.onMeta?.(payload.provider_id, payload.model_name, payload.note ?? "");
      }
      break;
    case "delta":
      handlers.onDelta?.(payload.content ?? "");
      break;
    case "review_start":
      handlers.onReviewStart?.();
      break;
    case "review_abort":
      handlers.onReviewAbort?.();
      break;
    case "done":
      handlers.onDone?.(payload.revised === true, {
        latency_ms: payload.latency_ms,
        tokens: payload.tokens,
      });
      break;
    case "error":
      handlers.onError?.(payload.content ?? "Neznámá chyba");
      break;
  }
}
