import { Check, Copy } from "lucide-react";
import type { ReactNode } from "react";
import { useCopy } from "../lib/useCopy";

interface Props {
  language: string;
  /** Plain source, extracted before syntax highlighting wrapped it in spans. */
  text: string;
  children: ReactNode;
}

export function CodeBlock({ language, text, children }: Props) {
  const { copied, copy } = useCopy();

  return (
    <div className="code-block">
      <div className="code-head">
        <span>{language || "kód"}</span>
        <button
          type="button"
          className={`copy-btn${copied ? " copied" : ""}`}
          onClick={() => copy(text)}
          aria-label="Kopírovat kód"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "Zkopírováno" : "Kopírovat"}
        </button>
      </div>
      {/* Široký blok se posouvá vodorovně. Bez `tabIndex` se do něj z
          klávesnice nedá dostat, takže konec dlouhého řádku je myší
          dosažitelný a jinak ne. `role="region"` s popiskem dává odečítači
          důvod, proč je ten prvek zaostřitelný. */}
      <pre tabIndex={0} role="region" aria-label={`Kód${language ? ` (${language})` : ""}`}>
        {children}
      </pre>
    </div>
  );
}
