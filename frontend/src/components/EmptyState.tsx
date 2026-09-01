import { Code, Globe, Wand2 } from "lucide-react";

import type { SearchMode } from "../lib/types";
import { PrizmaMark } from "./Logo";

/**
 * Three prompts that demonstrate the product rather than fill the space.
 *
 * Each one lands on a different capability *and* a different web mode, so the
 * first thing a new user sees is the router doing the thing the app is for.
 * `search` is applied to the globe picker when the chip is used, which is why
 * the chip can promise "deep research" and be telling the truth.
 */
const SUGGESTIONS: {
  icon: typeof Code;
  label: string;
  tag: string;
  search: SearchMode;
}[] = [
  {
    icon: Globe,
    label: "Porovnej Postgres a SQLite pro lokální desktopovou aplikaci",
    tag: "deep research",
    search: "deep",
  },
  {
    icon: Code,
    label: "Napiš Python funkci, která z CSV udělá měsíční souhrn",
    tag: "kód",
    search: "off",
  },
  {
    icon: Wand2,
    label: "Vymysli tři názvy pro aplikaci na sledování běhu",
    tag: "kreativní",
    search: "off",
  },
];

interface Props {
  onPick: (text: string, search: SearchMode) => void;
}

export function EmptyState({ onPick }: Props) {
  return (
    <div className="empty-state">
      <div className="empty-mark">
        <PrizmaMark size={44} />
      </div>
      <h1>Na co se dnes zeptáme?</h1>
      <p>Router rozebere zprávu a pošle ji na nejlepší dostupný model.</p>

      <div className="suggestions">
        {SUGGESTIONS.map(({ icon: Icon, label, tag, search }) => (
          <button
            key={label}
            type="button"
            className="suggestion"
            onClick={() => onPick(label, search)}
          >
            <Icon size={14} className="suggestion-icon" />
            <span className="suggestion-label">{label}</span>
            <span className="suggestion-tag">{tag}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
