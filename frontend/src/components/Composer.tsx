import { ArrowUp, Cpu, Globe, Square } from "lucide-react";
import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from "react";

import { clearSetting, loadSetting, saveSetting } from "../lib/storage";
import type { RoutingOptions } from "../lib/types";
import { EffortSegments } from "./EffortSegments";
import { RoutingPicker, type PickerOption } from "./RoutingPicker";
import { SearchPicker } from "./SearchPicker";

const MAX_HEIGHT_PX = 200;

export interface ComposerHandle {
  /** Used by the suggestion chips to drop text into the box and focus it. */
  prefill: (text: string) => void;
  /**
   * Whether the box is blank. The `↑` shortcut only takes the arrow key
   * when there is nothing to move the caret through — stealing it from a
   * half-written message would be worse than having no shortcut.
   */
  isEmpty: () => boolean;
}

interface Props {
  streaming: boolean;
  disabled?: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  handleRef?: Ref<ComposerHandle>;
  /** Null until /api/routing/options answers — the row stays out until then. */
  routing: RoutingOptions | null;
  model: string;
  effort: string;
  search: string;
  onModelChange: (id: string) => void;
  onEffortChange: (id: string) => void;
  onSearchChange: (id: string) => void;
  /**
   * Where this box's unsent text is parked, one slot per conversation.
   * Switching threads or reloading the tab used to throw away a half-written
   * prompt with no warning and no undo — the single most annoying thing the
   * app did.
   */
  draftKey: string;
}

export function Composer({
  streaming,
  disabled,
  onSend,
  onStop,
  handleRef,
  routing,
  model,
  effort,
  search,
  onModelChange,
  onEffortChange,
  onSearchChange,
  draftKey,
}: Props) {
  const [value, setValue] = useState(() => loadSetting(draftKey, ""));
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Which slot `value` currently belongs to. Compared rather than used as an
  // effect dependency alone, so the swap happens before the save effect below
  // can write this conversation's text into the next conversation's slot.
  const ownerRef = useRef(draftKey);

  useEffect(() => {
    if (ownerRef.current === draftKey) return;
    ownerRef.current = draftKey;
    setValue(loadSetting(draftKey, ""));
  }, [draftKey]);

  useEffect(() => {
    if (ownerRef.current !== draftKey) return;
    if (value.trim()) saveSetting(draftKey, value);
    else clearSetting(draftKey);
  }, [draftKey, value]);

  // Auto-grow: reset to auto first so the box can also shrink again.
  //
  // The width guard is not paranoia. A textarea laid out at zero width reports
  // a scrollHeight of hundreds of pixels even when it is empty, so measuring
  // then pins the box open at MAX_HEIGHT_PX — and because the measurement is
  // keyed on the text, nothing ever corrects it. Zero width is not exotic: it
  // is a background tab, a restored session, and a PWA launched from the home
  // screen while the window is still being sized.
  const fit = useCallback(() => {
    const el = textareaRef.current;
    if (!el || el.clientWidth === 0) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, []);

  useEffect(fit, [fit, value]);

  // Re-measure whenever the box changes width: rotating a phone, opening the
  // sidebar, or the first paint after mounting hidden. `ResizeObserver` covers
  // all three, where a window resize listener misses the last one.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [fit]);

  useImperativeHandle(handleRef, () => ({
    prefill(text: string) {
      setValue(text);
      textareaRef.current?.focus();
    },
    isEmpty: () => value.trim().length === 0,
  }));

  const modelOptions = useMemo<PickerOption[]>(
    () =>
      (routing?.models ?? []).map((m) => ({
        id: m.id,
        label: m.label,
        // A provider with no key can't be pinned. Saying why beats a silently
        // greyed-out row the user can't explain.
        description: m.configured ? m.description : "Nemá uložený klíč.",
        disabled: !m.configured,
        tag: m.recommended ? "doporučeno" : undefined,
      })),
    [routing]
  );

  const effortOptions = useMemo<PickerOption[]>(
    () =>
      (routing?.efforts ?? []).map((e) => ({
        id: e.id,
        label: e.label,
        description: e.description,
      })),
    [routing]
  );

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || disabled) return;
    onSend(text);
    setValue("");
    clearSetting(draftKey);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        <div className="composer">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Napiš zprávu…"
            rows={1}
            disabled={disabled}
            aria-label="Zpráva"
          />

          <div className="composer-controls">
            {routing && (
              <div className="composer-pickers">
                <RoutingPicker
                  label="Model"
                  icon={<Cpu size={13} />}
                  options={modelOptions}
                  value={model}
                  onChange={onModelChange}
                  disabled={disabled}
                />
                <EffortSegments
                  options={effortOptions}
                  value={effort}
                  onChange={onEffortChange}
                  disabled={disabled}
                />
                <SearchPicker
                  options={routing.searches}
                  value={search}
                  onChange={onSearchChange}
                  disabled={disabled}
                />
              </div>
            )}

            {streaming ? (
              <button
                type="button"
                className="send-btn stop"
                onClick={onStop}
                aria-label="Zastavit generování"
                title="Zastavit generování"
              >
                <Square size={13} fill="currentColor" />
              </button>
            ) : (
              <button
                type="button"
                className="send-btn"
                onClick={submit}
                disabled={disabled || !value.trim()}
                aria-label="Odeslat"
                title="Odeslat"
              >
                <ArrowUp size={18} />
              </button>
            )}
          </div>
        </div>
        {/* The globe modes always run — the keyless Wikipedia/arXiv floor is
            permanently in the chain — but "can search" and "can search well"
            are different claims, and the second one needs a key. */}
        {routing?.search_hint && search !== "off" && (
          <p className="composer-note">
            <Globe size={12} aria-hidden />
            {routing.search_hint}
          </p>
        )}
        <p className="composer-hint">Enter odešle · Shift+Enter nový řádek · ? zkratky</p>
      </div>
    </div>
  );
}
