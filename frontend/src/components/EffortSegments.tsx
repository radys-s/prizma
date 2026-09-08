import { useRef, type KeyboardEvent } from "react";

import type { PickerOption } from "./RoutingPicker";

interface Props {
  options: PickerOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * Náročnost jako přepínač, ne jako rozbalovací nabídka.
 *
 * Rozdíl proti pickeru vedle: modelů je hodně a mění se, stupňů náročnosti
 * jsou vždycky čtyři a jsou seřazené. Nabídka, ve které je pořadí významné
 * a položky se vejdou vedle sebe, nemá důvod se zavírat — schovávala jedinou
 * informaci, na které tady záleží: kde na té škále právě jsem.
 *
 * `role="radiogroup"` s roving tabindexem, protože přesně to to je. Skupina
 * má v tabulátoru jedno zastavení a mezi stupni se chodí šipkami; čtyři
 * tabstopy v řádku nad polem na psaní by z klávesnice udělaly překážku.
 *
 * Popis stupně se neztratil — jde do `title`, takže ho ukáže hover i čtečka
 * přes `aria-description`.
 */
export function EffortSegments({ options, value, onChange, disabled }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);

  const focusAt = (index: number) => {
    const next = options[(index + options.length) % options.length];
    if (!next) return;
    onChange(next.id);
    rootRef.current
      ?.querySelector<HTMLButtonElement>(`[data-id="${CSS.escape(next.id)}"]`)
      ?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = options.findIndex((o) => o.id === value);
    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
        e.preventDefault();
        focusAt(current + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        e.preventDefault();
        focusAt(current - 1);
        break;
      case "Home":
        e.preventDefault();
        focusAt(0);
        break;
      case "End":
        e.preventDefault();
        focusAt(options.length - 1);
        break;
    }
  };

  return (
    <div
      className="segments"
      role="radiogroup"
      aria-label="Náročnost"
      ref={rootRef}
      onKeyDown={onKeyDown}
    >
      {options.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            data-id={option.id}
            role="radio"
            aria-checked={active}
            aria-description={option.description}
            className="segment"
            // Roving tabindex: jen zvolený stupeň je v pořadí tabulátoru.
            tabIndex={active ? 0 : -1}
            disabled={disabled}
            title={option.description ? `${option.label} — ${option.description}` : option.label}
            onClick={() => onChange(option.id)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
