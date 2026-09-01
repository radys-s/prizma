import {
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  Globe,
  ListOrdered,
  Pencil,
  RefreshCw,
  Shuffle,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { absoluteTime, relativeTime } from "../lib/date";
import { useCopy } from "../lib/useCopy";
import { scrollBehavior } from "../lib/motion";
import type { Message as ChatMessage, Source } from "../lib/types";
import { Markdown } from "./Markdown";
import { ProviderMark } from "./ProviderMark";
import { SearchSteps } from "./SearchSteps";

interface Props {
  message: ChatMessage;
  /** Present only on the last assistant message. */
  onRegenerate?: () => void;
  /** Present only on a failed answer that is still the last thing in the thread. */
  onRetry?: () => void;
  /** Rewrites this user message and re-runs the thread from here. */
  onEdit?: (content: string) => void;
  /**
   * Bumped by the app when ↑ in an empty composer asks for the last user
   * message. A counter rather than a boolean, because pressing ↑ again
   * after cancelling has to reopen the editor — a flag stuck at `true`
   * would only work once.
   */
  editNonce?: number;
  /** provider id -> display name, so a reloaded thread can still name them. */
  providerNames?: Record<string, string>;
}

const CAPABILITY_LABELS: Record<string, string> = {
  fast: "rychlé",
  general: "obecné",
  code: "kód",
  creative: "kreativní",
  long_context: "dlouhý kontext",
};

const COMPLEXITY_LABELS: Record<string, string> = {
  trivial: "triviální",
  moderate: "běžné",
  hard: "složité",
};

const SEARCH_LABELS: Record<string, string> = {
  web: "web",
  deep: "deep research",
};

/** "prizma.ai" — the domain is what tells a source apart at a glance. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * "kód · složité" — what the front model decided before handing off. Absent
 * when the heuristic decided (no planner ran), because "obecné · běžné" from a
 * regex is a claim the app can't actually back up.
 */
function planChip(message: ChatMessage): string | null {
  if (!message.planner_model) return null;
  const parts = [
    CAPABILITY_LABELS[message.capability ?? ""] ?? message.capability,
    COMPLEXITY_LABELS[message.complexity ?? ""] ?? message.complexity,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * The substitution note. Live it arrives ready-made on the meta event; on a
 * reloaded conversation it is rebuilt from the two stored provider ids, so the
 * fact that a pin wasn't honoured survives a refresh instead of quietly
 * disappearing.
 */
function substitutionNote(message: ChatMessage, names: Record<string, string>): string | null {
  if (message.note) return message.note;
  if (!message.substituted_from || !message.provider_id) return null;
  const short = (id: string) => (names[id] ?? id).split(" (")[0];
  return `${short(message.substituted_from)} nedostupný, odpověděl ${short(message.provider_id)}`;
}

const NUMBER = new Intl.NumberFormat("cs-CZ");

/**
 * "2,4 s · 1 180 tokenů" — what the answer actually cost.
 *
 * Both halves are optional on purpose: wall clock is always measurable, token
 * usage only when the provider reports it, and inventing a number for the ones
 * that don't would make the router look like it knows more than it does.
 */
function costLine(message: ChatMessage): string | null {
  const parts: string[] = [];
  if (message.latency_ms && message.latency_ms > 0) {
    parts.push(`${(message.latency_ms / 1000).toFixed(1).replace(".", ",")} s`);
  }
  if (message.tokens && message.tokens > 0) {
    const n = message.tokens;
    const noun = n === 1 ? "token" : n >= 2 && n <= 4 ? "tokeny" : "tokenů";
    parts.push(`${NUMBER.format(n)} ${noun}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** Favicon when the search API gave one, first letter of the host otherwise. */
function SourceMark({ source }: { source: Source }) {
  const [broken, setBroken] = useState(false);
  const host = hostOf(source.url);

  if (source.favicon && !broken) {
    return (
      <img
        className="source-icon"
        src={source.favicon}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
      />
    );
  }
  return <span className="source-icon mono">{host[0]?.toUpperCase() ?? "?"}</span>;
}

function SourceCard({ source, index, prefix }: { source: Source; index: number; prefix: string }) {
  return (
    <a
      id={`${prefix}-src-${index}`}
      className="source-card"
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      <span className="source-index">{index}</span>
      <SourceMark source={source} />
      <span className="source-text">
        <span className="source-title">{source.title || hostOf(source.url)}</span>
        <span className="source-host">
          {hostOf(source.url)}
          <ExternalLink size={10} aria-hidden />
        </span>
      </span>
    </a>
  );
}

/** The answer with its sources appended, so a paste keeps its receipts. */
function withSources(message: ChatMessage): string {
  const sources = message.sources ?? [];
  if (sources.length === 0) return message.content;
  const list = sources.map((s, i) => `${i + 1}. [${s.title || s.url}](${s.url})`);
  return [message.content, "", "**Zdroje**", "", ...list].join("\n");
}

export function Message({
  message,
  onRegenerate,
  onRetry,
  onEdit,
  editNonce,
  providerNames = {},
}: Props) {
  const { copied, copy } = useCopy();
  const { copied: copiedFull, copy: copyFull } = useCopy();
  const rootRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const editRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!editNonce) return;
    setDraft(message.content);
    // Only the nonce: re-running this when the text changes would fight the
    // textarea for control of what is being typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editNonce]);

  useEffect(() => {
    if (draft === null) return;
    const el = editRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  if (message.role === "user") {
    if (draft !== null) {
      const submit = () => {
        const text = draft.trim();
        setDraft(null);
        if (text && text !== message.content) onEdit?.(text);
      };
      return (
        <div className="message user editing">
          <div className="bubble edit">
            <textarea
              ref={editRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setDraft(null);
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              aria-label="Upravit zprávu"
            />
            <div className="edit-actions">
              <button type="button" className="btn" onClick={() => setDraft(null)}>
                Zrušit
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={submit}
                disabled={!draft.trim()}
              >
                Odeslat znovu
              </button>
            </div>
          </div>
          {/* Saying it up front beats a surprise: everything below this turn
              is dropped server-side, and there is no undo. */}
          <p className="edit-hint">Odpovědi pod touhle zprávou se zahodí.</p>
        </div>
      );
    }

    return (
      <div className="message user">
        <div className="bubble">{message.content}</div>
        {onEdit && (
          <div className="message-actions user-actions">
            <button
              type="button"
              className="icon-btn"
              onClick={() => setDraft(message.content)}
              aria-label="Upravit zprávu"
              title="Upravit a odeslat znovu"
            >
              <Pencil size={14} />
            </button>
          </div>
        )}
      </div>
    );
  }

  if (message.error) {
    return (
      <div className="message">
        <div className="message-error">
          <TriangleAlert size={16} />
          <div className="message-error-text">{message.content}</div>
          {/* The thread is intact behind this — a failed answer no longer
              deletes the one it was replacing — so retrying costs nothing. */}
          {onRetry && (
            <div className="message-error-actions">
              <button type="button" className="btn" onClick={onRetry}>
                <RefreshCw size={14} />
                Zkusit znovu
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  const chip = planChip(message);
  const note = substitutionNote(message, providerNames);
  const sources = message.sources ?? [];
  const cost = costLine(message);
  // Ids have to be unique across the whole page. Only one message streams at a
  // time, so "live" cannot collide with anything.
  const prefix = message.id != null ? `m${message.id}` : "live";
  const providerName = message.provider_id ? providerNames[message.provider_id] : undefined;
  // A strip reads at a glance; past four cards it needs to be foldable again.
  const asStrip = sources.length > 0 && sources.length <= 4;

  /** Clicking `[2]` in the answer jumps to source 2 and flashes it. */
  const onCitationClick = (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as HTMLElement).closest?.("a.citation");
    if (!link) return;
    const index = link.getAttribute("data-citation");
    if (!index) return;
    const target = rootRef.current?.querySelector<HTMLElement>(`#${prefix}-src-${index}`);
    if (!target) return;
    event.preventDefault();
    target.closest("details")?.setAttribute("open", "");
    target.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
    target.classList.remove("flash");
    // Reflow, or re-adding the class in the same frame is a no-op and a second
    // click on the same citation does nothing visible.
    void target.offsetWidth;
    target.classList.add("flash");
  };

  return (
    <div
      className="message assistant"
      ref={rootRef}
      onClick={onCitationClick}
      data-streaming={message.streaming ? "true" : undefined}
    >
      <div className="model-badge">
        {message.streaming && !message.model_name ? (
          <span className="model-name pending">hledám model…</span>
        ) : (
          <span
            className="model-name"
            // The raw slug is still the ground truth — it just doesn't belong
            // in the middle of a sentence-shaped UI.
            title={message.model_name ?? undefined}
          >
            {message.provider_id && (
              <ProviderMark id={message.provider_id} name={providerName ?? message.provider_id} />
            )}
            {providerName ?? message.model_name}
          </span>
        )}
        {chip && (
          <span className="plan-chip" title={message.goal || undefined}>
            {chip}
          </span>
        )}
        {message.search_mode && (
          <span
            className="plan-chip search"
            title="Tahle odpověď vychází z výsledků z webu, ne jen ze znalostí modelu"
          >
            <Globe size={11} aria-hidden />
            {SEARCH_LABELS[message.search_mode] ?? message.search_mode}
          </span>
        )}
        {message.reviewing && <span className="plan-chip reviewing">reviduju…</span>}
        {!message.reviewing && message.revised && (
          <span className="plan-chip revised" title="Odpověď prošla druhým, kontrolním průchodem">
            po revizi
          </span>
        )}
      </div>

      {note && (
        <div className="meta-note">
          <Shuffle size={13} />
          <span>{note}</span>
        </div>
      )}

      {/* The rail replaces itself with the answer — once text is arriving, the
          text is the progress indicator. */}
      {message.streaming && !message.content && <SearchSteps message={message} />}

      {message.search_error && (
        <div className="meta-note">
          <Globe size={13} />
          <span>{message.search_error}</span>
        </div>
      )}

      {message.content && (
        <Markdown
          content={message.content}
          sourceCount={sources.length}
          anchorPrefix={prefix}
        />
      )}

      {!message.streaming && sources.length > 0 && (
        <div className="sources">
          {asStrip ? (
            <>
              <p className="sources-head">Zdroje</p>
              <div className="sources-strip">
                {sources.map((source, i) => (
                  <SourceCard
                    key={`${source.url}-${i}`}
                    source={source}
                    index={i + 1}
                    prefix={prefix}
                  />
                ))}
              </div>
            </>
          ) : (
            <details>
              <summary>
                <ChevronRight size={13} className="sources-caret" aria-hidden />
                Zdroje
                <span className="sources-count">{sources.length}</span>
              </summary>
              {/* Numbered, not bulleted: the numbers are the [n] citations in
                  the answer above, so the order here is load-bearing. */}
              <div className="sources-grid">
                {sources.map((source, i) => (
                  <SourceCard
                    key={`${source.url}-${i}`}
                    source={source}
                    index={i + 1}
                    prefix={prefix}
                  />
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {!message.streaming && message.content && (
        <div className="message-actions">
          <button
            type="button"
            className="icon-btn"
            onClick={() => copy(message.content)}
            aria-label="Kopírovat odpověď"
            title="Kopírovat odpověď"
          >
            {copied ? <Check size={15} /> : <Copy size={15} />}
          </button>
          {sources.length > 0 && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => copyFull(withSources(message))}
              aria-label="Kopírovat odpověď i se zdroji"
              title="Kopírovat i se zdroji (Markdown)"
            >
              {copiedFull ? <Check size={15} /> : <ListOrdered size={15} />}
            </button>
          )}
          {onRegenerate && (
            <button
              type="button"
              className="icon-btn"
              onClick={onRegenerate}
              aria-label="Přegenerovat odpověď"
              title="Přegenerovat — router může sáhnout po jiném modelu"
            >
              <RefreshCw size={15} />
            </button>
          )}
          {cost && (
            <span className="cost" title="Doba odpovědi a spotřebované tokeny">
              {cost}
            </span>
          )}
          {/* Stored on every row since day one and never shown until now.
              Relative, because "před 5 min" is the question people actually
              have; the exact stamp is one hover away. */}
          {message.created_at && (
            <time
              className="message-time"
              dateTime={message.created_at}
              title={absoluteTime(message.created_at)}
            >
              {relativeTime(message.created_at)}
            </time>
          )}
        </div>
      )}
    </div>
  );
}
