import { Check, ChevronDown } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

export interface PickerOption {
  id: string;
  label: string;
  description?: string;
  /** Rendered but unselectable — used for providers with no key yet. */
  disabled?: boolean;
  /** Small tag after the label, e.g. "doporučeno". */
  tag?: string;
}

interface Props {
  /** Accessible name — what this picker controls, e.g. "Model". */
  label: string;
  icon: ReactNode;
  options: PickerOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * The dropdown behind both the model and the effort picker.
 *
 * Written by hand rather than pulled in as a dependency: it needs exactly one
 * behaviour (single select, keyboard reachable, closes on Escape and on an
 * outside click) and a headless-UI package would be more bundle than that is
 * worth. Kept generic so the two pickers stay identical without duplication.
 */
export function RoutingPicker({ label, icon, options, value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const selected = options.find((o) => o.id === value) ?? options[0];
  const selectable = options.filter((o) => !o.disabled);

  // Clicking anywhere else — including inside the textarea below — closes the
  // menu. `mousedown` rather than `click` so it beats the composer's focus.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  // Open on the current value, so arrow keys start from where the user is.
  useEffect(() => {
    if (!open) return;
    const index = options.findIndex((o) => o.id === value);
    setActive(index >= 0 ? index : 0);
  }, [open, options, value]);

  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const pick = (option: PickerOption) => {
    if (option.disabled) return;
    onChange(option.id);
    setOpen(false);
  };

  const step = (delta: number) => {
    if (selectable.length === 0) return;
    // Walk over disabled entries rather than landing on them.
    let next = active;
    for (let i = 0; i < options.length; i += 1) {
      next = (next + delta + options.length) % options.length;
      if (!options[next].disabled) break;
    }
    setActive(next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    switch (e.key) {
      case "Escape":
        if (open) {
          e.stopPropagation();
          setOpen(false);
        }
        break;
      case "ArrowDown":
        e.preventDefault();
        if (!open) setOpen(true);
        else step(1);
        break;
      case "ArrowUp":
        e.preventDefault();
        if (!open) setOpen(true);
        else step(-1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (!open) setOpen(true);
        else if (options[active]) pick(options[active]);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  return (
    <div className="picker" ref={rootRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        className="picker-trigger"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        /* Fokus zůstává na spouštěči, takže bez tohohle odečítač při
           projíždění šipkami nic neřekne — položka se jen vizuálně zvýrazní. */
        aria-activedescendant={
          open && options[active] ? `${listId}-${options[active].id}` : undefined
        }
        aria-label={`${label}: ${selected?.label ?? ""}`}
        title={`${label}: ${selected?.label ?? ""}`}
      >
        <span className="picker-icon">{icon}</span>
        <span className="picker-value">{selected?.label ?? "—"}</span>
        <ChevronDown size={13} className="picker-chevron" aria-hidden />
      </button>

      {open && (
        <div className="picker-menu" id={listId} role="listbox" aria-label={label} ref={listRef}>
          {options.map((option, index) => (
            <button
              key={option.id}
              id={`${listId}-${option.id}`}
              type="button"
              data-index={index}
              role="option"
              aria-selected={option.id === value}
              aria-disabled={option.disabled}
              className="picker-option"
              data-active={index === active}
              data-disabled={option.disabled}
              onClick={() => pick(option)}
              onMouseEnter={() => !option.disabled && setActive(index)}
            >
              <span className="picker-check">
                {option.id === value && <Check size={14} />}
              </span>
              <span className="picker-text">
                <span className="picker-label">
                  {option.label}
                  {option.tag && <span className="picker-tag">{option.tag}</span>}
                </span>
                {option.description && (
                  <span className="picker-desc">{option.description}</span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
