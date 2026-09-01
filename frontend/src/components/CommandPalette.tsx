import {
  Check,
  Cpu,
  Download,
  Gauge,
  Globe,
  Keyboard,
  KeyRound,
  MessageSquare,
  Monitor,
  Moon,
  Pin,
  PinOff,
  Plus,
  Sun,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { Theme } from "../lib/theme";
import type { Conversation, RoutingOptions } from "../lib/types";

interface Command {
  id: string;
  group: string;
  label: string;
  icon: ReactNode;
  /** Right-hand text: a shortcut, a description, or why the row is dead. */
  hint?: string;
  /** Drawn with a tick — the currently active model, effort, mode or theme. */
  active?: boolean;
  disabled?: boolean;
  run: () => void;
}

/** Accent-insensitive contains, so "resers" finds „rešerše". */
function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

interface Props {
  onClose: () => void;
  routing: RoutingOptions | null;
  conversations: Conversation[];
  /** Null on an unsent thread — the per-conversation rows stay out until then. */
  currentId: number | null;
  model: string;
  effort: string;
  search: string;
  theme: Theme;
  onModelChange: (id: string) => void;
  onEffortChange: (id: string) => void;
  onSearchChange: (id: string) => void;
  onThemeChange: (theme: Theme) => void;
  onSelectConversation: (id: number) => void;
  onNewChat: () => void;
  onOpenKeys: () => void;
  onShowShortcuts: () => void;
  onExport: (id: number) => void;
  onPin: (id: number, pinned: boolean) => void;
}

/**
 * Ctrl+K.
 *
 * Everything the app can do without the mouse, in one list. Written by hand
 * for the same reason as `RoutingPicker`: the behaviour is a filtered list with
 * arrow keys, and a command-palette package would be more bundle than that.
 */
export function CommandPalette({
  onClose,
  routing,
  conversations,
  currentId,
  model,
  effort,
  search,
  theme,
  onModelChange,
  onEffortChange,
  onSearchChange,
  onThemeChange,
  onSelectConversation,
  onNewChat,
  onOpenKeys,
  onShowShortcuts,
  onExport,
  onPin,
}: Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => inputRef.current?.focus(), []);

  const commands = useMemo<Command[]>(() => {
    const done = (fn: () => void) => () => {
      fn();
      onClose();
    };

    const out: Command[] = [
      {
        id: "new",
        group: "Akce",
        label: "Nová konverzace",
        icon: <Plus size={15} />,
        hint: "Ctrl+Shift+O",
        run: done(onNewChat),
      },
      {
        id: "keys",
        group: "Akce",
        label: "API klíče",
        icon: <KeyRound size={15} />,
        run: done(onOpenKeys),
      },
      {
        id: "shortcuts",
        group: "Akce",
        label: "Klávesové zkratky",
        icon: <Keyboard size={15} />,
        hint: "?",
        run: done(onShowShortcuts),
      },
      {
        id: "theme-light",
        group: "Vzhled",
        label: "Světlý režim",
        icon: <Sun size={15} />,
        active: theme === "light",
        run: done(() => onThemeChange("light")),
      },
      {
        id: "theme-dark",
        group: "Vzhled",
        label: "Tmavý režim",
        icon: <Moon size={15} />,
        active: theme === "dark",
        run: done(() => onThemeChange("dark")),
      },
      {
        id: "theme-system",
        group: "Vzhled",
        label: "Podle systému",
        icon: <Monitor size={15} />,
        active: theme === "system",
        run: done(() => onThemeChange("system")),
      },
    ];

    // Only meaningful once the thread exists on the server.
    const current = conversations.find((c) => c.id === currentId);
    if (current) {
      out.push({
        id: "export",
        group: "Akce",
        label: "Stáhnout konverzaci jako Markdown",
        icon: <Download size={15} />,
        run: done(() => onExport(current.id)),
      });
      out.push({
        id: "pin",
        group: "Akce",
        label: current.pinned ? "Odepnout konverzaci" : "Připnout konverzaci",
        icon: current.pinned ? <PinOff size={15} /> : <Pin size={15} />,
        run: done(() => onPin(current.id, !current.pinned)),
      });
    }

    for (const m of routing?.models ?? []) {
      out.push({
        id: `model-${m.id}`,
        group: "Model",
        label: m.label,
        icon: <Cpu size={15} />,
        hint: m.configured ? undefined : "chybí klíč",
        active: m.id === model,
        disabled: !m.configured,
        run: done(() => onModelChange(m.id)),
      });
    }

    for (const e of routing?.efforts ?? []) {
      out.push({
        id: `effort-${e.id}`,
        group: "Náročnost",
        label: e.label,
        icon: <Gauge size={15} />,
        active: e.id === effort,
        run: done(() => onEffortChange(e.id)),
      });
    }

    for (const s of routing?.searches ?? []) {
      out.push({
        id: `search-${s.id}`,
        group: "Web",
        label: s.label,
        icon: <Globe size={15} />,
        hint: s.available
          ? s.id !== "off" && routing?.search_keyed === 0
            ? "bez klíče: Wikipedia a arXiv"
            : undefined
          : "nemá čím hledat",
        active: s.id === search,
        disabled: !s.available,
        run: done(() => onSearchChange(s.id)),
      });
    }

    for (const c of conversations) {
      out.push({
        id: `conv-${c.id}`,
        group: "Konverzace",
        label: c.title,
        icon: <MessageSquare size={15} />,
        run: done(() => onSelectConversation(c.id)),
      });
    }

    return out;
  }, [
    routing,
    conversations,
    currentId,
    model,
    effort,
    search,
    theme,
    onModelChange,
    onEffortChange,
    onSearchChange,
    onThemeChange,
    onSelectConversation,
    onNewChat,
    onOpenKeys,
    onShowShortcuts,
    onExport,
    onPin,
    onClose,
  ]);

  const matches = useMemo(() => {
    const needle = fold(query.trim());
    if (!needle) return commands;
    return commands.filter((c) => fold(`${c.group} ${c.label}`).includes(needle));
  }, [commands, query]);

  // A shorter result list must not leave the highlight past its end.
  useEffect(() => setActive(0), [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const step = (delta: number) => {
    if (matches.length === 0) return;
    let next = active;
    for (let i = 0; i < matches.length; i += 1) {
      next = (next + delta + matches.length) % matches.length;
      if (!matches[next].disabled) break;
    }
    setActive(next);
  };

  let lastGroup = "";

  return (
    <div className="palette-scrim" onClick={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Příkazy"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            onClose();
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            step(1);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            step(-1);
          } else if (e.key === "Enter") {
            e.preventDefault();
            const chosen = matches[active];
            if (chosen && !chosen.disabled) chosen.run();
          }
        }}
      >
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Hledat příkaz, model nebo konverzaci…"
          aria-label="Hledat příkaz"
          spellCheck={false}
          autoComplete="off"
        />

        <div className="palette-list" ref={listRef} role="listbox" aria-label="Příkazy">
          {matches.length === 0 && <p className="palette-empty">Nic takového tu není.</p>}
          {matches.map((command, index) => {
            const header = command.group !== lastGroup ? command.group : null;
            lastGroup = command.group;
            return (
              <div key={command.id}>
                {header && <p className="palette-group">{header}</p>}
                <button
                  type="button"
                  role="option"
                  aria-selected={index === active}
                  aria-disabled={command.disabled}
                  data-index={index}
                  data-active={index === active}
                  data-disabled={command.disabled}
                  className="palette-item"
                  onClick={() => !command.disabled && command.run()}
                  onMouseEnter={() => !command.disabled && setActive(index)}
                >
                  <span className="palette-icon">{command.icon}</span>
                  <span className="palette-label">{command.label}</span>
                  {command.active && <Check size={14} className="palette-check" />}
                  {command.hint && <span className="palette-hint">{command.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
