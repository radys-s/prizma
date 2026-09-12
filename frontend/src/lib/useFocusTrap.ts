import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Kolik překryvných oken je právě otevřených.
 *
 * Escape je v appce dvakrát: zavírá dialog a zastavuje odpověď. Dialog se ho
 * pokoušel spolknout přes `stopPropagation()` z posluchače na `window`, což
 * proti jiným posluchačům na stejném uzlu nedělá nic — takže zavřít nápovědu
 * ke zkratkám uprostřed odpovědi tu odpověď zároveň zastavilo. Globální
 * zkratka se proto ptá tady místo toho, aby se o událost přetahovala.
 */
let openOverlays = 0;

export function anyOverlayOpen(): boolean {
  return openOverlays > 0;
}

/**
 * Drží fokus uvnitř `ref`, dokud je dialog otevřený, a při zavření ho vrátí
 * tam, odkud přišel.
 *
 * `aria-modal="true"` na dialogu, který fokus nedrží, je nepravda: odečítač
 * obrazovky podle něj schová zbytek stránky, ale Tab tam dál odejde — a
 * uživatel se ocitne v obsahu, o kterém mu appka tvrdí, že tam není. Vracení
 * fokusu je druhá půlka: bez něj začíná Tab po zavření nastavení od začátku
 * stránky, ne u tlačítka, kterým se nastavení otevřelo.
 */
export function useFocusTrap<T extends HTMLElement = HTMLDivElement>(isOpen: boolean = true) {
  const ref = useRef<T>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    openOverlays += 1;
    previousFocus.current = document.activeElement as HTMLElement | null;

    const el = ref.current;
    if (el) {
      const focusables = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusables.length > 0) focusables[0].focus();
      else el.focus();
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab" || !ref.current) return;

      // Přepočítává se při každém stisku: obsah dialogu se mění (rozbalený
      // provider, odhalené pole), takže seznam z doby otevření by lhal.
      // Skryté prvky ven — jinak by Tab skočil na něco, co není vidět.
      const focusables = Array.from(
        ref.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      ).filter((node) => node.offsetWidth > 0 || node.offsetHeight > 0);

      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      const outside = !ref.current.contains(active);

      if (e.shiftKey) {
        if (active === first || outside) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || outside) {
        e.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      openOverlays = Math.max(0, openOverlays - 1);
      const previous = previousFocus.current;
      // `isConnected`: prvek, který se mezitím odmontoval (smazaná konverzace,
      // přepnutá obrazovka), fokus nepřevezme a prohlížeč by ho poslal na
      // `<body>` — tam se z klávesnice pokračuje od začátku stránky.
      if (previous && previous.isConnected && typeof previous.focus === "function") {
        previous.focus();
      }
    };
  }, [isOpen]);

  return ref;
}
