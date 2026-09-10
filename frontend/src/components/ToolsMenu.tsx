import { ChevronDown, Globe, MonitorCog, Wrench } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { ComputerMode, SearchOption } from "../lib/types";

interface Props {
  /** Web modes from /api/routing/options — the backend owns the list. */
  searches: SearchOption[];
  search: string;
  onSearchChange: (id: string) => void;
  /** Whether the computer-access layer is on at all. Off = the row stays out. */
  toolsOn: boolean;
  computer: ComputerMode;
  onComputerChange: (mode: ComputerMode) => void;
  /** Povolené složky, aby „počítač“ nebyl abstraktní slib. */
  computerRoots: string[];
  canWrite: boolean;
  disabled?: boolean;
}

/** Which web mode a flick of the switch turns *on*. */
const WEB_ON_DEFAULT = "auto";

/** The last path segment, so the row says "Osobni" and not 60 characters. */
function shortRoot(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

/**
 * Web a počítač pod jedním tlačítkem.
 *
 * Dřív to byly dvě rozbalovačky vedle sebe, obě popsané svojí hodnotou —
 * „Auto“ a „Jen chat“. Kdo appku neznal, neměl z čeho poznat, že spolu
 * souvisejí, ani co která dělá; a co je zapnuté, se dalo zjistit jedině
 * rozkliknutím. Tady je to jedno tlačítko, které se **jmenuje** podle toho,
 * co je zapnuté („Web + Počítač“), a když je zapnuté cokoli, svítí akcentem.
 *
 * Přepínač zapíná, stupně pod ním upřesňují. To pořadí je záměr: rozhodnutí
 * „ano/ne“ je to časté, výběr režimu to vzácné.
 */
export function ToolsMenu({
  searches,
  search,
  onSearchChange,
  toolsOn,
  computer,
  onComputerChange,
  computerRoots,
  canWrite,
  disabled,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const webOn = search !== "off";
  const pcOn = computer !== "off";

  // Zapnuté nástroje se musí dát přečíst bez otevírání. Jméno, ne tečka.
  const label = useMemo(() => {
    const names: string[] = [];
    if (webOn) names.push("Web");
    if (toolsOn && pcOn) names.push("Počítač");
    return names.length > 0 ? names.join(" + ") : "Nástroje";
  }, [webOn, pcOn, toolsOn]);

  const anyOn = webOn || (toolsOn && pcOn);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
    }
  };

  const webModes = searches.filter((o) => o.id !== "off");
  const unavailable = searches.find((o) => o.id === search)?.available === false;

  const where = computerRoots.length
    ? computerRoots.map(shortRoot).join(", ")
    : "nastavených složkách";

  return (
    <div className="tools-menu" ref={rootRef} onKeyDown={onKeyDown} data-on={anyOn}>
      <button
        type="button"
        className="picker-trigger tools-trigger"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={anyOn ? `Nástroje: ${label}` : "Nástroje: žádné"}
        title={anyOn ? `Zapnuto: ${label}` : "Web a přístup k počítači"}
      >
        <span className="picker-icon">
          <Wrench size={13} />
        </span>
        <span className="picker-value">{label}</span>
        <ChevronDown size={13} className="picker-chevron" aria-hidden />
      </button>

      {open && (
        <div className="tools-panel" id={panelId} role="dialog" aria-label="Nástroje">
          <div className="tool-row" data-on={webOn}>
            <span className="tool-icon" aria-hidden>
              <Globe size={17} />
            </span>
            <div className="tool-body">
              <span className="tool-name">Web</span>
              <span className="tool-desc">
                {unavailable
                  ? "Tenhle režim teď nemá čím hledat."
                  : "Vyhledá na internetu a přečte stránky, než odpoví."}
              </span>
              {webOn && webModes.length > 0 && (
                <div className="tool-steps" role="group" aria-label="Režim hledání">
                  {webModes.map((o) => (
                    <button
                      key={o.id}
                      type="button"
                      className="tool-step"
                      data-on={o.id === search}
                      disabled={!o.available}
                      title={o.description}
                      onClick={() => onSearchChange(o.id)}
                    >
                      {o.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              type="button"
              className="tool-switch"
              role="switch"
              aria-checked={webOn}
              aria-label="Hledat na webu"
              onClick={() => onSearchChange(webOn ? "off" : WEB_ON_DEFAULT)}
            >
              <span className="tool-knob" />
            </button>
          </div>

          {/* Co se vůbec smí, drží dialog v nastavení; tady se rozhoduje, jestli
              se toho u téhle zprávy má použít. Když je vrstva vypnutá, řádek
              tu není — přepínač, který nic nepřepíná, je horší než jeho absence. */}
          {toolsOn && (
            <div className="tool-row" data-on={pcOn}>
              <span className="tool-icon" aria-hidden>
                <MonitorCog size={17} />
              </span>
              <div className="tool-body">
                <span className="tool-name">Počítač</span>
                <span className="tool-desc">
                  {canWrite ? "Čte i zapisuje soubory v: " : "Čte soubory v: "}
                  <b>{where}</b>
                </span>
                {pcOn && (
                  <div className="tool-steps" role="group" aria-label="Rozsah přístupu">
                    <button
                      type="button"
                      className="tool-step"
                      data-on={computer === "pc"}
                      title={
                        canWrite
                          ? "Smí číst i zapisovat soubory v povolených složkách."
                          : "Smí číst soubory v povolených složkách."
                      }
                      onClick={() => onComputerChange("pc")}
                    >
                      {canWrite ? "Číst i psát" : "Jen číst"}
                    </button>
                    <button
                      type="button"
                      className="tool-step"
                      data-on={computer === "commands"}
                      title="Navíc smí spustit git, python, npm nebo testy."
                      onClick={() => onComputerChange("commands")}
                    >
                      + příkazy
                    </button>
                  </div>
                )}
              </div>
              <button
                type="button"
                className="tool-switch"
                role="switch"
                aria-checked={pcOn}
                aria-label="Pracovat s počítačem"
                onClick={() => onComputerChange(pcOn ? "off" : "pc")}
              >
                <span className="tool-knob" />
              </button>
            </div>
          )}

          {toolsOn && computer === "commands" && (
            <p className="tools-note">
              Spouštění příkazů platí jen pro tuhle zprávu — po reloadu je zpátky
              na čtení.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
