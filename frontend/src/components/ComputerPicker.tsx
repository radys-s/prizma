import { MonitorCog } from "lucide-react";
import { useMemo } from "react";

import type { ComputerMode } from "../lib/types";
import { RoutingPicker, type PickerOption } from "./RoutingPicker";

interface Props {
  value: ComputerMode;
  onChange: (mode: ComputerMode) => void;
  /** Where the model may look, from /api/settings. Shown so "počítač" is not
   *  an abstract promise — the user sees which folders it means. */
  roots: string[];
  /** Whether writing is on in the dialog; only changes what the label claims. */
  canWrite: boolean;
  disabled?: boolean;
}

/** The last path segment, so the menu shows "Osobni" and not 60 characters. */
function shortRoot(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

/**
 * Pracovat s počítačem, nebo si jen povídat.
 *
 * Jedno rozhodnutí, tři stupně, protože jsou vnořené: příkazy nedávají smysl
 * bez čtení a dvě zaškrtávátka vedle sebe by to tvrdily. Odpovídá to i tomu,
 * jak se to chová na serveru — `use_tools` je přepínač, `allow_commands`
 * povolení navíc.
 *
 * Stupeň „příkazy" se schválně **neukládá**. Model, který smí spustit program,
 * je rozhodnutí pro jednu zprávu; zapamatovaný stav by z něj udělal trvalý
 * režim aplikace, což je něco jiného, než k čemu uživatel svolil. Po reloadu
 * je zpátky na „počítač".
 */
export function ComputerPicker({ value, onChange, roots, canWrite, disabled }: Props) {
  const options = useMemo<PickerOption[]>(() => {
    const where = roots.length
      ? roots.map(shortRoot).join(", ")
      : "nastavených složkách";
    return [
      {
        id: "off",
        label: "Jen chat",
        description: "Model na disk nevidí a odpovídá z toho, co umí.",
      },
      {
        id: "pc",
        label: "Počítač",
        description: canWrite
          ? `Smí číst i zapisovat soubory v: ${where}`
          : `Smí číst soubory v: ${where}`,
      },
      {
        id: "commands",
        label: "Počítač + příkazy",
        description: "Navíc smí spustit git, python, npm nebo testy. Jen pro tuhle zprávu.",
      },
    ];
  }, [roots, canWrite]);

  return (
    <div className="picker-computer" data-on={value !== "off"}>
      <RoutingPicker
        label="Počítač"
        icon={<MonitorCog size={13} />}
        options={options}
        value={value}
        onChange={(id) => onChange(id as ComputerMode)}
        disabled={disabled}
      />
    </div>
  );
}
