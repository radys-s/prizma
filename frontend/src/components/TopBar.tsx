import { useEffect, useState } from "react";

import { Command, KeyRound, PanelLeft } from "lucide-react";

interface Props {
  title: string;
  configuredCount: number;
  totalKeys: number;
  /** How many slots the router insists on before it will answer at all. */
  minSlots: number;
  onToggleSidebar: () => void;
  onOpenSettings: () => void;
  onOpenPalette: () => void;
}

export function TopBar({
  title,
  configuredCount,
  totalKeys,
  minSlots,
  onToggleSidebar,
  onOpenSettings,
  onOpenPalette,
}: Props) {
  // Amber below the minimum, not only at zero: one key out of the two the
  // router needs is still a broken app, and this chip is the standing signal
  // now that the alert no longer sits on top of the conversation.
  const short = configuredCount < minSlots;

  // Živý stav připojení. `AppShell` testuje server jen při startu, takže když
  // se spojení ztratí uprostřed sezení, jinde v rozhraní se to nikde neukáže —
  // až u odeslané zprávy, která spadne. Tečka to řekne dřív.
  const [online, setOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  return (
    <header className="topbar">
      <button
        type="button"
        className="icon-btn"
        onClick={onToggleSidebar}
        aria-label="Zobrazit/skrýt postranní panel"
        title="Zobrazit/skrýt postranní panel"
      >
        <PanelLeft size={17} />
      </button>

      <span
        className={`topbar-status${online ? "" : " off"}`}
        role="status"
        aria-label={online ? "Připojeno" : "Bez připojení"}
        title={online ? "Připojeno" : "Bez připojení k internetu"}
      />

      <div className="topbar-title">{title}</div>

      <button
        type="button"
        className="chip command-chip"
        onClick={onOpenPalette}
        title="Příkazy a přepínání (Ctrl+K)"
        aria-label="Otevřít příkazy"
      >
        <Command size={12} />
        <kbd>K</kbd>
      </button>

      <button
        type="button"
        className={`chip${short ? " warn" : ""}`}
        onClick={onOpenSettings}
        title="Nastavení API klíčů"
      >
        <KeyRound size={12} />
        {configuredCount}/{totalKeys}
      </button>
    </header>
  );
}
