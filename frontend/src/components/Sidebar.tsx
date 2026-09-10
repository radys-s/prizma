import {
  Check,
  ChevronDown,
  Download,
  KeyRound,
  LogOut,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
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

/**
 * Zavře, co je otevřené, když se klikne jinam nebo padne Escape.
 *
 * Používají to obě nabídky v panelu — u řádku konverzace i u účtu. Bez toho
 * zůstane menu viset přes seznam, dokud se netrefíš zpátky na to samé tlačítko.
 */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

/**
 * Akce jednoho řádku pod jedním „⋯“.
 *
 * Dřív tu na hoveru vyjely čtyři ikony vedle sebe. Byly pořád zabrané —
 * `.row-actions` sice startuje na `opacity: 0`, ale místo v řádku drží
 * i neviditelné, takže z 260px panelu ukusovaly ~90 px a názvy se zkracovaly
 * na „Přeh“, „Vý“ nebo „Přístup k int…“. Jedno tlačítko zabere 24 px a otevře
 * nabídku, kde má každá akce jméno — což je navíc jediný způsob, jak se dá
 * poznat, co ta ikonka se šipkou dolů vlastně dělá.
 */
function RowMenu({
  conversation,
  open,
  onToggle,
  onClose,
  onRename,
  onPin,
  onExport,
  onDelete,
}: {
  conversation: Conversation;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onRename: () => void;
  onPin: () => void;
  onExport: () => void;
  onDelete: () => void;
}) {
  const ref = useDismiss(open, onClose);
  const run = (action: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onClose();
    action();
  };

  return (
    <div className="row-menu" ref={ref}>
      <button
        type="button"
        className="icon-btn row-menu-trigger"
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Akce konverzace ${conversation.title}`}
        title="Další akce"
      >
        <MoreHorizontal size={15} />
      </button>

      {open && (
        <div className="row-menu-list" role="menu">
          <button type="button" role="menuitem" onClick={run(onRename)}>
            <Pencil size={14} />
            Přejmenovat
          </button>
          <button type="button" role="menuitem" onClick={run(onPin)}>
            {conversation.pinned ? <PinOff size={14} /> : <Pin size={14} />}
            {conversation.pinned ? "Odepnout" : "Připnout nahoru"}
          </button>
          <button type="button" role="menuitem" onClick={run(onExport)}>
            <Download size={14} />
            Stáhnout jako Markdown
          </button>
          <div className="row-menu-sep" />
          <button type="button" role="menuitem" className="danger" onClick={run(onDelete)}>
            <Trash2 size={14} />
            Smazat
          </button>
        </div>
      )}
    </div>
  );
}

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
  const [menuId, setMenuId] = useState<number | null>(null);
  const [accountOpen, setAccountOpen] = useState(false);
  const accountRef = useDismiss(accountOpen, () => setAccountOpen(false));

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
                        <RowMenu
                          conversation={c}
                          open={menuId === c.id}
                          onToggle={() => setMenuId(menuId === c.id ? null : c.id)}
                          onClose={() => setMenuId(null)}
                          onRename={() => startRename(c)}
                          onPin={() => onPin(c.id, !c.pinned)}
                          onExport={() => onExport(c.id)}
                          onDelete={() => setConfirmingId(c.id)}
                        />
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

          {/* Vzhled i odhlášení pod účtem, ne jako dva samostatné řádky.
              Patička držela tři pruhy pro věci, na které se sahá jednou za
              měsíc, a ukusovala výšku seznamu, na který se dívá člověk pořád. */}
          <div className="account-menu" ref={accountRef}>
            <button
              type="button"
              className="account-trigger"
              onClick={() => setAccountOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={accountOpen}
              title={accountLabel}
            >
              {/* Iniciála místo ikonky panáčka: jeden účet, který má jméno,
                  vypadá jako účet — obecný piktogram vypadá jako placeholder. */}
              <span className="account-avatar" aria-hidden="true">
                {accountLabel.trim().charAt(0).toUpperCase() || <UserRound size={13} />}
              </span>
              <span className="account-name">{accountLabel}</span>
              <ChevronDown size={14} className="account-chevron" aria-hidden />
            </button>

            {accountOpen && (
              <div className="account-menu-list" role="menu">
                <div className="account-menu-head">Vzhled</div>
                <div className="account-menu-theme">
                  <ThemeToggle value={theme} onChange={onThemeChange} />
                </div>
                <div className="row-menu-sep" />
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountOpen(false);
                    onOpenSettings();
                  }}
                >
                  <KeyRound size={14} />
                  Nastavení a klíče
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAccountOpen(false);
                    onSignOut();
                  }}
                >
                  <LogOut size={14} />
                  Odhlásit se
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
