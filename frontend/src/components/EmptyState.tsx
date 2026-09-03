import { Code, Globe, Lightbulb, RefreshCw, Wand2 } from "lucide-react";
import { useState } from "react";

import { randomTip } from "../lib/tips";
import type { SearchMode } from "../lib/types";
import { PrizmaTile } from "./Logo";

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
  // Picked once per mount rather than per render: a tip that changed every
  // time React re-rendered would flicker while the user typed in the composer.
  const [tip, setTip] = useState(() => randomTip());

  return (
    <div className="empty-state">
      {/* Ambientní vrstva pod nadpisem. Čistě dekorativní, proto aria-hidden
          a nulový vliv na layout — leží v absolutní pozici za obsahem. */}
      <div className="empty-aurora" aria-hidden="true" />

      <PrizmaTile size={52} className="empty-mark" />

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

      <div className="tip">
        <Lightbulb size={14} className="tip-icon" />
        <p className="tip-text">{tip.text}</p>
        <button
          type="button"
          className="icon-btn tip-next"
          onClick={() => setTip((prev) => randomTip(prev))}
          aria-label="Další tip"
          title="Další tip"
        >
          <RefreshCw size={13} />
        </button>
      </div>
    </div>
  );
}
