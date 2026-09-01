import { Monitor, Moon, Sun } from "lucide-react";

import type { Theme } from "../lib/theme";

const OPTIONS: { id: Theme; label: string; icon: typeof Sun }[] = [
  { id: "light", label: "Světlý", icon: Sun },
  { id: "dark", label: "Tmavý", icon: Moon },
  { id: "system", label: "Podle systému", icon: Monitor },
];

interface Props {
  value: Theme;
  onChange: (theme: Theme) => void;
}

/**
 * Three-way segmented control in the sidebar footer.
 *
 * A radiogroup rather than three toggle buttons: the three states are mutually
 * exclusive and a screen reader should say "2 of 3", not read three unrelated
 * pressed/unpressed buttons. Arrow keys move between them for the same reason.
 */
export function ThemeToggle({ value, onChange }: Props) {
  const step = (delta: number) => {
    const index = OPTIONS.findIndex((o) => o.id === value);
    const next = OPTIONS[(index + delta + OPTIONS.length) % OPTIONS.length];
    onChange(next.id);
  };

  return (
    <div
      className="theme-toggle"
      role="radiogroup"
      aria-label="Vzhled"
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          step(1);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          step(-1);
        }
      }}
    >
      {OPTIONS.map((option) => {
        const Icon = option.icon;
        const checked = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={checked}
            // Only the selected segment is a tab stop; the arrows do the rest.
            tabIndex={checked ? 0 : -1}
            className="theme-seg"
            data-checked={checked}
            onClick={() => onChange(option.id)}
            aria-label={option.label}
            title={option.label}
          >
            <Icon size={14} aria-hidden />
          </button>
        );
      })}
    </div>
  );
}
