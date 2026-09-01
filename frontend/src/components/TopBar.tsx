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
