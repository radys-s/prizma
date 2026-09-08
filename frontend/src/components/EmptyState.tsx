import { Code, Globe, Lightbulb, RefreshCw, Split, Wand2 } from "lucide-react";
import { useState, type CSSProperties } from "react";

import { randomTip } from "../lib/tips";
import type { SearchMode } from "../lib/types";

/**
 * Four prompts that demonstrate the product rather than fill the space.
 *
 * Each one lands on a different capability *and* a different web mode, so the
 * first thing a new user sees is the router doing the thing the app is for.
 * `search` is applied to the globe picker when the chip is used, which is why
 * the chip can promise "deep research" and be telling the truth.
 *
 * `title` and `prompt` are split on purpose: the card shows a short label so
 * the tiles line up equal, but the click sends the full sentence, which is
 * what actually makes a decent prompt. Showing the whole sentence made the
 * cards different heights and turned the row into confetti.
 *
 * `hue` picks a stop out of the prism spectrum. Different colours across the
 * grid are the product in one image: one prompt, several paths out.
 */
const SUGGESTIONS: {
  icon: typeof Code;
  title: string;
  prompt: string;
  tag: string;
  search: SearchMode;
  hue: string;
}[] = [
  {
    icon: Globe,
    title: "Porovnej Postgres a SQLite",
    prompt: "Porovnej Postgres a SQLite pro lokální desktopovou aplikaci",
    tag: "deep research",
    search: "deep",
    hue: "var(--spectrum-5)",
  },
  {
    icon: Code,
    title: "Měsíční souhrn z CSV",
    prompt: "Napiš Python funkci, která z CSV udělá měsíční souhrn",
    tag: "kód",
    search: "off",
    hue: "var(--spectrum-4)",
  },
  {
    icon: Wand2,
    title: "Názvy pro appku na běhání",
    prompt: "Vymysli tři názvy pro aplikaci na sledování běhu",
    tag: "kreativní",
    search: "off",
    hue: "var(--spectrum-2)",
  },
  // Schválně dost široké zadání na to, aby ho plánovač označil za `hard`.
  // V režimu Pro a Max je to jediná ukázka rozdělení mezi víc modelů, kterou
  // člověk uvidí, aniž by ji musel vymyslet sám.
  {
    icon: Split,
    title: "Rozeber architekturu appky",
    prompt:
      "Rozeber architekturu chatovací aplikace s vlastním routerem: backend, " +
      "datový model, bezpečnost a nasazení. U každé části uveď rizika.",
    tag: "rozbor",
    search: "off",
    hue: "var(--spectrum-1)",
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

      {/* Bez dlaždice se znakem. Ikona appky uvnitř appky nic neříká — člověk
          ví, co spustil. Identitu nese nadpis, který je obarvený tímtéž
          spektrem jako znak i přihlašovací obrazovka. */}
      <h1 className="empty-title">Na co se dnes zeptáme?</h1>
      <p>Router rozebere zprávu a pošle ji na nejlepší dostupný model.</p>

      <div className="suggestions">
        {SUGGESTIONS.map(({ icon: Icon, title, prompt, tag, search, hue }) => (
          <button
            key={title}
            type="button"
            className="suggestion"
            style={{ "--hue": hue } as CSSProperties}
            onClick={() => onPick(prompt, search)}
          >
            <span className="suggestion-icon" aria-hidden="true">
              <Icon size={16} />
            </span>
            <span className="suggestion-title">{title}</span>
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
