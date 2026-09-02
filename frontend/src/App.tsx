import { ArrowDown, KeyRound, Loader2, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CommandPalette } from "./components/CommandPalette";
import { CompactDivider } from "./components/CompactDivider";
import { Composer, type ComposerHandle } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { PrizmaMark } from "./components/Logo";
import { LoginScreen } from "./components/LoginScreen";
import { Message } from "./components/Message";
import { ProvidersModal } from "./components/ProvidersModal";
import { ShortcutsDialog } from "./components/ShortcutsDialog";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import {
  deleteConversation,
  editMessage,
  fetchAuthState,
  fetchConversation,
  fetchConversations,
  fetchProviders,
  fetchRoutingOptions,
  fetchSettings,
  logout,
  pinConversation,
  regenerate,
  renameConversation,
  streamChat,
  Unauthenticated,
  type RoutingChoice,
  type StreamHandlers,
} from "./lib/api";
import { conversationToMarkdown, downloadMarkdown, exportFilename } from "./lib/export";
import { useServiceWorker } from "./lib/pwa";
import { loadSetting, saveSetting } from "./lib/storage";
import { applyTheme, loadTheme, saveTheme, watchSystem, type Theme } from "./lib/theme";
import { useAutoScroll } from "./lib/useAutoScroll";
import { useHotkeys } from "./lib/useHotkeys";
import type {
  AuthState,
  CompactInfo,
  Conversation,
  Message as ChatMessage,
  Provider,
  RoutingOptions,
} from "./lib/types";

const MOBILE_QUERY = "(max-width: 900px)";

const isMobile = () => window.matchMedia(MOBILE_QUERY).matches;

const MODEL_KEY = "prizma.model";
const EFFORT_KEY = "prizma.effort";
const SEARCH_KEY = "prizma.search";

/** One unsent-draft slot per conversation; "new" until the thread has an id. */
const draftKeyFor = (conversationId: number | null) =>
  `prizma.draft.${conversationId ?? "new"}`;

function patchLast(
  list: ChatMessage[],
  patch: (m: ChatMessage) => ChatMessage
): ChatMessage[] {
  if (list.length === 0) return list;
  const next = [...list];
  next[next.length - 1] = patch(next[next.length - 1]);
  return next;
}

/**
 * Decides what to render before the chat exists: the boot spinner, the login
 * screen, or the app. Every authenticated screen lives below this, so no
 * component further down has to think about being logged out.
 */
/**
 * A new build is waiting in the service worker. Deliberately a prompt and not a
 * silent reload: the reload throws away whatever is on screen, and doing that
 * mid-answer is worse than running yesterday's bundle for another minute.
 */
function UpdateToast({ onApply }: { onApply: () => void }) {
  return (
    <div className="update-toast" role="status">
      <span>Je k dispozici nová verze.</span>
      <button type="button" className="btn btn-primary" onClick={onApply}>
        Načíst
      </button>
    </div>
  );
}

export default function App() {
  const { updateReady, applyUpdate } = useServiceWorker();
  return (
    <>
      <AppShell />
      {updateReady && <UpdateToast onApply={applyUpdate} />}
    </>
  );
}

function AppShell() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [booted, setBooted] = useState(false);
  const [offline, setOffline] = useState(false);

  const refreshAuth = useCallback(async () => {
    try {
      setAuth(await fetchAuthState());
      setOffline(false);
    } catch {
      setOffline(true);
    } finally {
      setBooted(true);
    }
  }, []);

  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);

  if (!booted) {
    return (
      <div className="auth-boot">
        <Loader2 size={22} className="spin" />
        <span>Načítám…</span>
      </div>
    );
  }

  if (offline || auth === null) {
    return (
      <div className="auth-boot">
        <PrizmaMark size={40} />
        {/* Two very different situations, one screen. Installed on a phone the
            cause is almost always the network; on Tomáš's desktop it is almost
            always that uvicorn is not running. Naming both beats naming the
            wrong one. */}
        <span>
          {navigator.onLine
            ? "Server neodpovídá. Pokud běží Prizma lokálně, zkontroluj, že běží uvicorn na portu 8000."
            : "Jsi offline. Aplikace se načetla z paměti telefonu, ale odpovídat bez připojení neumí."}
        </span>
        <button type="button" className="btn btn-primary" onClick={refreshAuth}>
          Zkusit znovu
        </button>
      </div>
    );
  }

  if (!auth.authenticated) {
    return <LoginScreen state={auth} onAuthenticated={refreshAuth} />;
  }

  // Keyed on the account so signing in as someone else remounts the chat with
  // clean state instead of inheriting the previous session's conversations.
  return (
    <ChatApp
      key={auth.user?.provider ?? "local"}
      accountLabel={auth.user?.email || auth.user?.display_name || "Přihlášeno"}
      onSignedOut={refreshAuth}
      auth={auth}
      onAuthChanged={refreshAuth}
    />
  );
}

interface ChatAppProps {
  accountLabel: string;
  onSignedOut: () => void;
  /** Passed down so the settings modal can list and change sign-in methods. */
  auth: AuthState;
  onAuthChanged: () => void;
}

function ChatApp({ accountLabel, onSignedOut, auth, onAuthChanged }: ChatAppProps) {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => !isMobile());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [routing, setRouting] = useState<RoutingOptions | null>(null);
  const [model, setModel] = useState(() => loadSetting(MODEL_KEY, "auto"));
  const [effort, setEffort] = useState(() => loadSetting(EFFORT_KEY, "medium"));
  const [search, setSearch] = useState(() => loadSetting(SEARCH_KEY, "auto"));
  const [theme, setTheme] = useState<Theme>(() => loadTheme());
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Dismissed for this browser session only — a missing key is still missing
  // tomorrow, so the strip has to come back on the next visit.
  const [alertHidden, setAlertHidden] = useState(false);
  const [readPages, setReadPages] = useState(true);
  const [customInstructions, setCustomInstructions] = useState("");
  const [customInstructionsMax, setCustomInstructionsMax] = useState(2000);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // What auto-compact folded away on the last run, or null when the whole
  // thread still fits. `folded` counts from the *start* of the conversation,
  // so it doubles as the index of the first message still sent verbatim.
  const [compact, setCompact] = useState<CompactInfo | null>(null);
  // Incremented by the ↑ shortcut; the last user message opens its editor
  // when the number changes. See `editNonce` in Message.
  const [editNonce, setEditNonce] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  /**
   * Which stream owns the message list (C6).
   *
   * Aborting a fetch does not cancel the promise chain already queued behind
   * it: a stream stopped by switching conversations still runs its `onDone`,
   * which used to stamp `streaming: false` — and a token buffer — onto
   * whatever message was last in the thread the user had just opened. Every
   * run carries the id it was started with and drops its callbacks once the
   * number moves on.
   */
  const runIdRef = useRef(0);
  const composerRef = useRef<ComposerHandle>(null);
  const { ref: scrollRef, pinned, scrollToBottom } = useAutoScroll(messages);

  // Deltas arrive per token; batching them into one render per frame keeps
  // markdown re-parsing off the hot path.
  const pendingRef = useRef("");
  const rafRef = useRef<number>();

  // The pre-review answer. Held outside state because the review pass has to be
  // able to put it back after the bubble was already cleared.
  const draftRef = useRef("");

  /**
   * A session can expire while the tab sits open. Rather than sprinkling
   * try/catch through every handler, everything that talks to the API goes
   * through here and a dead session bounces straight back to the login screen.
   */
  const guard = useCallback(
    async <T,>(run: () => Promise<T>): Promise<T | undefined> => {
      try {
        return await run();
      } catch (err) {
        if (err instanceof Unauthenticated) onSignedOut();
        else throw err;
        return undefined;
      }
    },
    [onSignedOut]
  );

  const reloadProviders = useCallback(async () => {
    const list = await guard(fetchProviders);
    if (list) setProviders(list);
  }, [guard]);

  const reloadConversations = useCallback(async () => {
    const list = await guard(fetchConversations);
    if (list) setConversations(list);
  }, [guard]);

  const reloadRouting = useCallback(async () => {
    const options = await guard(fetchRoutingOptions);
    if (!options) return;
    setRouting(options);
    // A pinned provider whose key was deleted (or that vanished from the
    // registry) would silently stay in localStorage and be sent forever.
    setModel((current) => {
      const match = options.models.find((m) => m.id === current);
      return match && match.configured ? current : "auto";
    });
    setEffort((current) =>
      options.efforts.some((e) => e.id === current) ? current : options.default_effort
    );
    // Same reset as the model picker: a stored mode that can no longer run —
    // because the search-capable key was deleted — must not keep being sent.
    setSearch((current) => {
      const match = options.searches.find((s) => s.id === current);
      return match && match.available ? current : options.default_search;
    });
  }, [guard]);

  useEffect(() => {
    reloadProviders();
    reloadConversations();
    reloadRouting();
  }, [reloadProviders, reloadConversations, reloadRouting]);

  // The inline script in index.html already stamped a forced theme before the
  // first paint, so this is a no-op on load and only does work when the user
  // actually flips the control.
  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
  }, [theme]);

  // "System" is resolved by CSS, so nothing here has to repaint — but the
  // <meta name="theme-color"> pair is media-scoped and the browser handles it.
  // The listener exists so anything reading the resolved mode stays honest.
  useEffect(() => {
    if (theme !== "system") return;
    return watchSystem(() => applyTheme("system"));
  }, [theme]);

  // Server-side, not localStorage: the backend is what decides whether a URL
  // leaves this machine, so the browser's copy is a mirror of that answer.
  useEffect(() => {
    guard(fetchSettings).then((settings) => {
      if (!settings) return;
      setReadPages(settings.read_pages);
      setCustomInstructions(settings.custom_instructions);
      setCustomInstructionsMax(settings.custom_instructions_max);
    });
  }, [guard]);

  useEffect(() => saveSetting(MODEL_KEY, model), [model]);
  useEffect(() => saveSetting(EFFORT_KEY, effort), [effort]);
  useEffect(() => saveSetting(SEARCH_KEY, search), [search]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const keySlots = useMemo(() => {
    const total = new Set(providers.map((p) => p.key_id));
    const configured = new Set(providers.filter((p) => p.configured).map((p) => p.key_id));
    return { total: total.size, configured: configured.size };
  }, [providers]);

  const providerNames = useMemo(
    () => Object.fromEntries(providers.map((p) => [p.id, p.display_name])),
    [providers]
  );

  const minSlots = routing?.min_slots ?? 2;
  const enoughKeys = keySlots.configured >= minSlots;

  const current = conversations.find((c) => c.id === currentId);
  const currentTitle = current?.title ?? "Nová konverzace";

  // Several tabs open on several threads is the normal way to use this, and
  // "Prizma" three times over tells you nothing about which is which.
  useEffect(() => {
    document.title = currentId === null ? "Prizma" : `${currentTitle} · Prizma`;
    return () => {
      document.title = "Prizma";
    };
  }, [currentId, currentTitle]);

  /* ------------------------------------------------------------ streaming */

  const drainBuffer = (): string => {
    if (rafRef.current !== undefined) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = undefined;
    }
    const chunk = pendingRef.current;
    pendingRef.current = "";
    return chunk;
  };

  const endStream = () => {
    setStreaming(false);
    abortRef.current = null;
    draftRef.current = "";
    reloadConversations();
  };

  /**
   * Handlers bound to one run. `stale()` is checked at the top of every one of
   * them rather than once at the call site, because they arrive over the life
   * of a stream — the run can be superseded between two of them.
   */
  const makeHandlers = (runId: number): StreamHandlers => {
  const stale = () => runIdRef.current !== runId;
  return {
    onStart: (conversationId) => {
      if (stale()) return;
      setCurrentId((prev) => prev ?? conversationId);
    },
    onCompact: (info) => {
      if (stale()) return;
      setCompact(info);
      // Also on the message, so the progress rail can name what happened
      // while the answer is still being written.
      setMessages((prev) => patchLast(prev, (m) => ({ ...m, compact: info })));
    },
    onPlan: (plan) => {
      if (stale()) return;
      setMessages((prev) =>
        patchLast(prev, (m) => ({
          ...m,
          capability: plan.capability,
          complexity: plan.complexity,
          effort: plan.effort,
          planner_model: plan.planner_model,
          goal: plan.goal,
          // "off" would render a chip claiming a search that never ran.
          search_mode: plan.search === "off" ? null : plan.search,
          planned: true,
        }))
      );
    },
    onSearchStart: (_depth, queries) => {
      if (stale()) return;
      setMessages((prev) =>
        // `searching` is the live flag the rail reads; `search_queries` is the
        // record of what was asked, which has to survive search_done.
        patchLast(prev, (m) => ({ ...m, searching: queries, search_queries: queries }))
      );
    },
    onReadStart: (urls) => {
      if (stale()) return;
      setMessages((prev) => patchLast(prev, (m) => ({ ...m, reading: urls })));
    },
    onSearchDone: (sources, partial) => {
      if (stale()) return;
      setMessages((prev) =>
        patchLast(prev, (m) => ({
          ...m,
          searching: null,
          sources: sources.length > 0 ? sources : null,
          // A partial run answered from fewer sub-questions than it asked for.
          // Saying so is the difference between a thin answer and a wrong one.
          search_error: partial ? "část podotázek se nepodařilo dohledat" : undefined,
        }))
      );
    },
    // Not an error state: the answer still arrives, just from model knowledge.
    onSearchAbort: (reason) => {
      if (stale()) return;
      setMessages((prev) =>
        patchLast(prev, (m) => ({
          ...m,
          searching: null,
          // The chip was set optimistically from the plan event. Nothing was
          // found, so it would be claiming a search that did not happen.
          search_mode: null,
          search_error: reason || "web se nepodařilo prohledat — odpovídám z vlastních znalostí",
        }))
      );
    },
    onMeta: (providerId, modelName, note) => {
      if (stale()) return;
      setMessages((prev) =>
        patchLast(prev, (m) => ({ ...m, provider_id: providerId, model_name: modelName, note }))
      );
    },
    onDelta: (delta) => {
      if (stale()) return;
      pendingRef.current += delta;
      if (rafRef.current !== undefined) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = undefined;
        const chunk = pendingRef.current;
        pendingRef.current = "";
        if (chunk && !stale()) {
          setMessages((prev) => patchLast(prev, (m) => ({ ...m, content: m.content + chunk })));
        }
      });
    },
    // Max tier only. The bubble is emptied and refilled by the revising model;
    // the draft is parked so onReviewAbort can put it straight back.
    onReviewStart: () => {
      if (stale()) return;
      const tail = drainBuffer();
      setMessages((prev) =>
        patchLast(prev, (m) => {
          draftRef.current = m.content + tail;
          return { ...m, content: "", reviewing: true };
        })
      );
    },
    onReviewAbort: () => {
      if (stale()) return;
      drainBuffer(); // whatever the review managed to write is discarded
      setMessages((prev) =>
        patchLast(prev, (m) => ({ ...m, content: draftRef.current, reviewing: false }))
      );
    },
    onDone: (revised, stats) => {
      // The one that matters most: an aborted run resolves through onDone, and
      // by then the list may belong to a different conversation entirely.
      if (stale()) return;
      const tail = drainBuffer();
      setMessages((prev) =>
        patchLast(prev, (m) => ({
          ...m,
          // Stopped mid-revision: the draft is a whole answer, the partial
          // rewrite is not. Same rule the backend applies when it saves.
          content: m.reviewing && !revised ? draftRef.current : m.content + tail,
          streaming: false,
          reviewing: false,
          revised,
          searching: null,
          // Absent when the provider reported no usage. Message renders only
          // the halves it actually has rather than printing a zero.
          latency_ms: stats?.latency_ms ?? m.latency_ms,
          tokens: stats?.tokens ?? m.tokens,
        }))
      );
      endStream();
    },
    onError: (msg) => {
      if (stale()) return;
      const tail = drainBuffer();
      setMessages((prev) => {
        const settled = patchLast(prev, (m) => ({
          ...m,
          // An error during the review pass falls back to the draft, same as
          // an abort — the user should not lose an answer they already read.
          content: m.reviewing ? draftRef.current : m.content + tail,
          streaming: false,
          reviewing: false,
        }));
        const last = settled[settled.length - 1];
        // Keep whatever already streamed and add the failure below it.
        return last && last.content
          ? [...settled, { role: "assistant" as const, content: msg, error: true }]
          : patchLast(settled, (m) => ({ ...m, content: msg, error: true }));
      });
      endStream();
    },
    // Not gated on `stale()`: a dead session is true no matter which run
    // noticed it, and staying on a screen that can no longer save anything
    // would be worse than a redundant bounce to the login form.
    onUnauthenticated: () => {
      setStreaming(false);
      abortRef.current = null;
      onSignedOut();
    },
  };
  };

  const runStream = async (start: (h: StreamHandlers, signal: AbortSignal) => Promise<void>) => {
    const runId = (runIdRef.current += 1);
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    // Re-announced by the `compact` event if it is still true this turn; an
    // edit can have invalidated the summary since the last one.
    setCompact(null);
    setMessages((prev) => [...prev, { role: "assistant", content: "", streaming: true }]);
    await start(makeHandlers(runId), controller.signal);
  };

  // Read at send time, not at render time, so a picker change mid-stream only
  // affects the next message.
  const choice = (): RoutingChoice => ({ model, effort, search });

  const sendMessage = async (text: string) => {
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    const conversationId = currentId;
    const picked = choice();
    await runStream((h, signal) => streamChat(text, conversationId, picked, h, signal));
  };

  const regenerateLast = async () => {
    if (currentId === null || streaming) return;
    setMessages((prev) => {
      const next = [...prev];
      while (next.length > 0 && next[next.length - 1].role === "assistant") next.pop();
      return next;
    });
    const conversationId = currentId;
    const picked = choice();
    await runStream((h, signal) => regenerate(conversationId, picked, h, signal));
  };

  /**
   * Rewrites a user message and re-runs the thread from there.
   *
   * The list is truncated to the edited turn *before* the request goes out,
   * because that is exactly what the backend does with the stored history —
   * leaving the old answers on screen would show a conversation that no longer
   * exists anywhere.
   */
  const editMessageAt = async (index: number, content: string) => {
    const target = messages[index];
    if (currentId === null || streaming || target?.id == null) return;
    const messageId = target.id;
    setMessages(messages.slice(0, index).concat({ ...target, content }));
    const conversationId = currentId;
    const picked = choice();
    await runStream((h, signal) =>
      editMessage(conversationId, messageId, content, picked, h, signal)
    );
  };

  const stopStreaming = () => abortRef.current?.abort();

  /**
   * Stop *and* disown the current run.
   *
   * `stopStreaming` alone is for the stop button, where the half-written answer
   * stays and `onDone` is supposed to land. This is for leaving the thread: the
   * run id moves on, so nothing still in flight can write into the list — which
   * also means nothing will clear `streaming` for us, hence doing it here.
   */
  const discardStream = () => {
    runIdRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    drainBuffer();
    draftRef.current = "";
    setStreaming(false);
    setCompact(null);
  };

  /* -------------------------------------------------------- conversations */

  const selectConversation = async (id: number) => {
    discardStream();
    setCurrentId(id);
    const data = await guard(() => fetchConversation(id));
    if (data) setMessages(data.messages);
    if (isMobile()) setSidebarOpen(false);
  };

  const newConversation = () => {
    discardStream();
    setCurrentId(null);
    setMessages([]);
    if (isMobile()) setSidebarOpen(false);
  };

  const removeConversation = async (id: number) => {
    await guard(() => deleteConversation(id));
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (id === currentId) {
      setCurrentId(null);
      setMessages([]);
    }
  };

  const rename = async (id: number, title: string) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await guard(() => renameConversation(id, title));
    } catch {
      reloadConversations(); // server said no — resync
    }
  };

  /** Optimistic, then resynced: the server owns the order pinning changes. */
  const pin = async (id: number, pinned: boolean) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, pinned } : c)));
    try {
      await guard(() => pinConversation(id, pinned));
    } finally {
      reloadConversations();
    }
  };

  /**
   * Refetched rather than exported from `messages`, so the sidebar can export
   * any thread and not only the open one — and so what lands in the file is
   * what is actually stored, not a list that a live stream is still editing.
   */
  const exportConversation = async (id: number) => {
    const data = await guard(() => fetchConversation(id));
    if (!data) return;
    const title = data.conversation.title || "Konverzace";
    downloadMarkdown(
      exportFilename(title),
      conversationToMarkdown(title, data.messages, providerNames)
    );
  };

  const signOut = async () => {
    discardStream();
    await logout();
    onSignedOut();
  };

  /* ------------------------------------------------------------ keyboard */

  const lastIndex = messages.length - 1;
  // Only a saved message can be edited: the backend needs an id to know where
  // to cut the thread, and a message still streaming has no id yet.
  const lastUserIndex = (() => {
    for (let i = lastIndex; i >= 0; i -= 1) {
      if (messages[i].role === "user") return messages[i].id != null ? i : -1;
    }
    return -1;
  })();

  useHotkeys([
    {
      key: "k",
      ctrl: true,
      inFields: true,
      handler: () => setPaletteOpen((open) => !open),
    },
    {
      key: "o",
      ctrl: true,
      shift: true,
      inFields: true,
      handler: newConversation,
    },
    {
      // Only while something is running. Otherwise Escape belongs to whatever
      // dialog or picker is open, and swallowing it here would trap the user.
      key: "Escape",
      inFields: true,
      handler: () => {
        if (!streaming) return false;
        stopStreaming();
      },
    },
    {
      key: "ArrowUp",
      inFields: true,
      handler: () => {
        if (streaming || lastUserIndex < 0) return false;
        if (composerRef.current && !composerRef.current.isEmpty()) return false;
        setEditNonce((n) => n + 1);
      },
    },
    {
      // Deliberately not `inFields`: "?" is a character people type.
      key: "?",
      shift: true,
      handler: () => setShortcutsOpen((open) => !open),
    },
    {
      key: "/",
      ctrl: true,
      inFields: true,
      handler: () => setShortcutsOpen((open) => !open),
    },
  ]);

  /* ------------------------------------------------------------- render */

  return (
    <div className="app" data-sidebar={sidebarOpen ? "open" : "closed"}>
      <Sidebar
        conversations={conversations}
        currentId={currentId}
        onSelect={selectConversation}
        onNew={newConversation}
        onDelete={removeConversation}
        onRename={rename}
        onPin={pin}
        onExport={exportConversation}
        configuredCount={keySlots.configured}
        totalKeys={keySlots.total}
        onOpenSettings={() => setSettingsOpen(true)}
        accountLabel={accountLabel}
        onSignOut={signOut}
        theme={theme}
        onThemeChange={setTheme}
      />

      <div className="scrim" onClick={() => setSidebarOpen(false)} />

      <main className="chat-area">
        <TopBar
          title={currentTitle}
          configuredCount={keySlots.configured}
          totalKeys={keySlots.total}
          minSlots={minSlots}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenPalette={() => setPaletteOpen(true)}
        />

        <div className="messages" ref={scrollRef}>
          {messages.length === 0 ? (
            <EmptyState
              onPick={(text, mode) => {
                // The chip promises a mode, so it has to actually set one —
                // otherwise "deep research" is just a label on a plain send.
                setSearch(mode);
                composerRef.current?.prefill(text);
              }}
            />
          ) : (
            <div className="messages-inner">
              {messages.map((m, i) => {
                const retryable =
                  i === lastIndex && m.role === "assistant" && !m.streaming && currentId !== null;
                return (
                  <div key={m.id ?? `local-${i}`} className="message-slot">
                    {/* `folded` counts from the start of the thread, so it is
                        also the index of the first turn still sent whole. */}
                    {compact && compact.folded === i && i > 0 && (
                      <CompactDivider info={compact} />
                    )}
                    <Message
                      message={m}
                      providerNames={providerNames}
                      onRegenerate={retryable && !m.error ? regenerateLast : undefined}
                      onRetry={retryable && m.error ? regenerateLast : undefined}
                      onEdit={
                        m.role === "user" && m.id != null && !streaming && currentId !== null
                          ? (content) => editMessageAt(i, content)
                          : undefined
                      }
                      editNonce={i === lastUserIndex ? editNonce : undefined}
                    />
                  </div>
                );
              })}
            </div>
          )}

          {!pinned && messages.length > 0 && (
            <button type="button" className="scroll-bottom-btn" onClick={scrollToBottom}>
              <ArrowDown size={13} />
              Nejnovější
            </button>
          )}
        </div>

        {/* Anchored to the composer instead of sitting on top of the thread:
            the old block pushed every message down and reflowed the chat on
            each render. The amber key chip in the topbar is the signal that
            stays put after this is dismissed. */}
        {providers.length > 0 && !enoughKeys && !alertHidden && (
          <div className="alert-strip" role="status">
            <TriangleAlert size={15} />
            <p>
              {keySlots.configured === 0
                ? `Zatím nemáš uložený žádný API klíč. Prizma potřebuje aspoň ${minSlots}, aby měla kam přepnout, když jeden provider selže.`
                : `Máš připojený jen jeden klíč. Prizma potřebuje aspoň ${minSlots} — bez záložního providera nemá kam přepnout, když první selže.`}
            </p>
            <button type="button" className="btn btn-primary" onClick={() => setSettingsOpen(true)}>
              <KeyRound size={14} />
              Nastavit klíče
            </button>
            <button
              type="button"
              className="icon-btn"
              onClick={() => setAlertHidden(true)}
              aria-label="Skrýt upozornění"
              title="Skrýt do příště"
            >
              <X size={15} />
            </button>
          </div>
        )}

        <Composer
          handleRef={composerRef}
          streaming={streaming}
          onSend={sendMessage}
          onStop={stopStreaming}
          routing={routing}
          model={model}
          effort={effort}
          search={search}
          onModelChange={setModel}
          onEffortChange={setEffort}
          onSearchChange={setSearch}
          draftKey={draftKeyFor(currentId)}
        />
      </main>

      {settingsOpen && (
        <ProvidersModal
          providers={providers}
          readPages={readPages}
          onReadPagesChange={setReadPages}
          customInstructions={customInstructions}
          customInstructionsMax={customInstructionsMax}
          onCustomInstructionsChange={setCustomInstructions}
          onClose={() => setSettingsOpen(false)}
          auth={auth}
          onAuthChanged={onAuthChanged}
          onChanged={() => {
            // A key change moves both lists: what's configured, and what the
            // model picker is allowed to offer.
            reloadProviders();
            reloadRouting();
          }}
        />
      )}

      {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          routing={routing}
          conversations={conversations}
          currentId={currentId}
          model={model}
          effort={effort}
          search={search}
          theme={theme}
          onModelChange={setModel}
          onEffortChange={setEffort}
          onSearchChange={setSearch}
          onThemeChange={setTheme}
          onSelectConversation={selectConversation}
          onNewChat={newConversation}
          onOpenKeys={() => setSettingsOpen(true)}
          onShowShortcuts={() => setShortcutsOpen(true)}
          onExport={exportConversation}
          onPin={pin}
        />
      )}
    </div>
  );
}
