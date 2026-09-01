import { X } from "lucide-react";
import { useEffect, useRef } from "react";

interface Shortcut {
  keys: string[];
  what: string;
}

interface Section {
  title: string;
  items: Shortcut[];
}

/**
 * Kept next to the hotkey registrations in App.tsx in spirit, not in code — a
 * shortcut that stops working and a help screen that still lists it is worse
 * than no help screen. Whenever a `useHotkeys` entry changes, this list is the
 * other half of that change.
 */
const SECTIONS: Section[] = [
  {
    title: "Konverzace",
    items: [
      { keys: ["Ctrl", "K"], what: "Příkazy — modely, režimy, konverzace" },
      { keys: ["Ctrl", "Shift", "O"], what: "Nová konverzace" },
      { keys: ["↑"], what: "Upravit poslední odeslanou zprávu (v prázdném poli)" },
      { keys: ["Esc"], what: "Zastavit generování" },
    ],
  },
  {
    title: "Psaní",
    items: [
      { keys: ["Enter"], what: "Odeslat" },
      { keys: ["Shift", "Enter"], what: "Nový řádek" },
    ],
  },
  {
    title: "Nápověda",
    items: [
      { keys: ["?"], what: "Tenhle přehled" },
      { keys: ["Ctrl", "/"], what: "Tenhle přehled" },
    ],
  },
];

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div
        className="modal shortcuts-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <div style={{ flex: 1 }}>
            <h2 id="shortcuts-title">Klávesové zkratky</h2>
            <p>Ctrl platí i pro ⌘ na Macu.</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Zavřít">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body">
          {SECTIONS.map((section) => (
            <section key={section.title} className="settings-section">
              <h3>{section.title}</h3>
              <dl className="shortcut-grid">
                {section.items.map((item) => (
                  <div key={item.what + item.keys.join("+")} className="shortcut-row">
                    <dt>
                      {item.keys.map((key) => (
                        <kbd key={key} className="mono">
                          {key}
                        </kbd>
                      ))}
                    </dt>
                    <dd>{item.what}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
