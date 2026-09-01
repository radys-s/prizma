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
      <pre>{children}</pre>
    </div>
  );
}
