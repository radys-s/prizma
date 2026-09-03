import {
  Check,
  Download,
  KeyRound,
  LogOut,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { searchMessages } from "../lib/api";
import { dayBucket, normalize } from "../lib/date";
import type { Theme } from "../lib/theme";
import type { Conversation, MessageHit } from "../lib/types";
import { PrizmaTile } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

const BUCKET_ORDER = ["Dnes", "Včera", "Posledních 7 dní", "Starší"];

const PINNED_LABEL = "Připnuté";

/** Long enough that the server isn't asked about `a`, short enough to feel live. */
const SEARCH_MIN_CHARS = 2;
const SEARCH_DEBOUNCE_MS = 250;

interface Props {
  conversations: Conversation[];
  currentId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  onRename: (id: number, title: string) => void;
  onPin: (id: number, pinned: boolean) => void;
  onExport: (id: number) => void;
  configuredCount: number;
  totalKeys: number;
  onOpenSettings: () => void;
  accountLabel: string;
  onSignOut: () => void;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
}

export function Sidebar({
  conversations,
  currentId,
  onSelect,
  onNew,
  onDelete,
  onRename,
  onPin,
  onExport,
  configuredCount,
  totalKeys,
  onOpenSettings,
  accountLabel,
  onSignOut,
  theme,
  onThemeChange,
}: Props) {
  const [query, setQuery] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [hits, setHits] = useState<MessageHit[]>([]);

  const needle = query.trim();

  /**
   * Titles filter instantly and locally; message bodies are asked of the
   * server. Two halves of one box on purpose — a thread called "Nová
   * konverzace" is invisible to a title filter no matter what is inside it.
   *
   * The request is debounced and every in-flight answer is discarded once the
   * query moves on, so a slow response can't repaint results for text the user
   * already deleted.
   */
  useEffect(() => {
    if (needle.length < SEARCH_MIN_CHARS) {
      setHits([]);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      searchMessages(needle)
        .then((results) => {
          if (live) setHits(results);
        })
        .catch(() => {
          if (live) setHits([]); // the title filter above still works
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [needle]);

  const groups = useMemo(() => {
    const folded = normalize(needle);
    const matched = folded
      ? conversations.filter((c) => normalize(c.title).includes(folded))
      : conversations;

    // Pinned threads leave the day buckets entirely — a conversation you
    // pinned three weeks ago belongs at the top, not under "Starší".
    const pinned = matched.filter((c) => c.pinned);
    const rest = matched.filter((c) => !c.pinned);

    const byBucket = new Map<string, Conversation[]>();
    for (const c of rest) {
      const bucket = dayBucket(c.created_at);
      const list = byBucket.get(bucket);
      if (list) list.push(c);
      else byBucket.set(bucket, [c]);
    }

    const out = BUCKET_ORDER.filter((b) => byBucket.has(b)).map((b) => ({
      label: b,
      items: byBucket.get(b)!,
    }));
    return pinned.length > 0 ? [{ label: PINNED_LABEL, items: pinned }, ...out] : out;
  }, [conversations, needle]);

  // Anything the title filter already found is not news; the point of this
  // group is the threads that match only somewhere inside.
  const titleMatchIds = useMemo(
    () => new Set(groups.flatMap((g) => g.items.map((c) => c.id))),
    [groups]
  );
  const bodyHits = useMemo(
    () => hits.filter((h) => !titleMatchIds.has(h.conversation_id)),
    [hits, titleMatchIds]
  );

  const startRename = (c: Conversation) => {
    setConfirmingId(null);
    setRenamingId(c.id);
    setDraft(c.title);
  };

  const commitRename = () => {
    if (renamingId === null) return;
    const title = draft.trim();
    const original = conversations.find((c) => c.id === renamingId);
    if (title && original && title !== original.title) onRename(renamingId, title);
    setRenamingId(null);
  };

  const onRenameKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitRename();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setRenamingId(null);
    }
  };

  const nothingAtAll = groups.length === 0 && bodyHits.length === 0;

  return (
    <aside className="sidebar">
      <div className="sidebar-inner">
        <div className="brand">
          <PrizmaTile size={28} />
          <span className="brand-text">Prizma</span>
        </div>

        <button type="button" className="new-chat-btn" onClick={onNew}>
          <Plus size={16} />
          Nová konverzace
        </button>

        <div className="search-field">
          <Search size={14} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Hledat v konverzacích"
            aria-label="Hledat v názvech i ve zprávách"
          />
          {query && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => setQuery("")}
              aria-label="Zrušit hledání"
            >
              <X size={13} />
            </button>
          )}
        </div>

        <div className="conversation-list">
          {nothingAtAll && (
            <p className="list-group-label">
              {needle ? "Nic nenalezeno" : "Zatím žádné konverzace"}
            </p>
          )}

          {groups.map((group) => (
            <div key={group.label}>
              <p className="list-group-label">{group.label}</p>
              {group.items.map((c) =>
                confirmingId === c.id ? (
                  <div key={c.id} className="confirm-row">
                    <span>Smazat „{c.title}“?</span>
                    <button
                      type="button"
                      className="icon-btn danger"
                      onClick={() => {
                        setConfirmingId(null);
                        onDelete(c.id);
                      }}
                      aria-label="Potvrdit smazání"
                    >
                      <Check size={15} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => setConfirmingId(null)}
                      aria-label="Zrušit"
                    >
                      <X size={15} />
                    </button>
                  </div>
                ) : (
                  <div
                    key={c.id}
                    className={`conversation-item${c.id === currentId ? " active" : ""}`}
                    onClick={() => renamingId !== c.id && onSelect(c.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && renamingId !== c.id) onSelect(c.id);
                    }}
                  >
                    {renamingId === c.id ? (
                      <input
                        className="rename-input"
                        value={draft}
                        autoFocus
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={onRenameKey}
                        onBlur={commitRename}
                        onClick={(e) => e.stopPropagation()}
                        aria-label="Nový název konverzace"
                      />
                    ) : (
                      <>
                        {c.pinned && (
                          <Pin size={12} className="pin-indicator" aria-hidden />
                        )}
                        <span className="conversation-title">{c.title}</span>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              onPin(c.id, !c.pinned);
                            }}
                            aria-label={c.pinned ? "Odepnout konverzaci" : "Připnout konverzaci"}
                            title={c.pinned ? "Odepnout" : "Připnout nahoru"}
                          >
                            {c.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                          </button>
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              onExport(c.id);
                            }}
                            aria-label="Stáhnout konverzaci jako Markdown"
                            title="Stáhnout jako Markdown"
                          >
                            <Download size={14} />
                          </button>
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              startRename(c);
                            }}
                            aria-label="Přejmenovat konverzaci"
                            title="Přejmenovat"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            type="button"
                            className="icon-btn danger"
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmingId(c.id);
                            }}
                            aria-label="Smazat konverzaci"
                            title="Smazat"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                )
              )}
            </div>
          ))}

          {bodyHits.length > 0 && (
            <div>
              <p className="list-group-label">Ve zprávách</p>
              {bodyHits.map((hit) => (
                <div
                  key={`hit-${hit.conversation_id}`}
                  className={`conversation-item search-hit${
                    hit.conversation_id === currentId ? " active" : ""
                  }`}
                  onClick={() => onSelect(hit.conversation_id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onSelect(hit.conversation_id);
                  }}
                >
                  <span className="conversation-title">{hit.title}</span>
                  <span className="search-snippet">{hit.snippet}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="sidebar-footer">
          <button
            type="button"
            className={`key-status-btn${configuredCount === 0 ? " empty" : ""}`}
            onClick={onOpenSettings}
          >
            <KeyRound size={15} />
            API klíče
            <span className="count">
              {configuredCount}/{totalKeys}
            </span>
          </button>

          <div className="theme-row">
            <span className="theme-row-label">Vzhled</span>
            <ThemeToggle value={theme} onChange={onThemeChange} />
          </div>

          <div className="account-row">
            <span className="account-id" title={accountLabel}>
              {/* Iniciála místo ikonky panáčka: jeden účet, který má jméno,
                  vypadá jako účet — obecný piktogram vypadá jako placeholder. */}
              <span className="account-avatar" aria-hidden="true">
                {accountLabel.trim().charAt(0).toUpperCase() || <UserRound size={13} />}
              </span>
              <span className="account-name">{accountLabel}</span>
            </span>
            <button
              type="button"
              className="icon-btn"
              onClick={onSignOut}
              aria-label="Odhlásit se"
              title="Odhlásit se"
            >
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
