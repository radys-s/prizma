import { Globe } from "lucide-react";
import { useMemo } from "react";

import type { SearchOption } from "../lib/types";
import { RoutingPicker, type PickerOption } from "./RoutingPicker";

interface Props {
  /** From /api/routing/options — the backend owns the list of modes. */
  options: SearchOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * Why a mode is greyed out. Same rule as the model picker: never a silent no.
 *
 * Effectively unreachable since the keyless Wikipedia/arXiv floor went in —
 * `can_search` is now permanently true. Kept because the backend still owns
 * `available`, and a mode that cannot run must still say why rather than
 * silently doing nothing. What *is* visible in the keyless case is
 * `RoutingOptions.search_hint`, shown under the composer.
 */
const NO_SEARCHER = "Tenhle režim teď nemá čím hledat.";

/**
 * The globe control.
 *
 * Thin on purpose — `RoutingPicker` already does the dropdown. What lives here
 * is the one thing the other two pickers don't have: modes that exist but
 * cannot run, because searching needs a key that can actually reach the web.
 * Those stay visible and explain themselves rather than disappearing, so the
 * feature doesn't look missing when it is merely unconfigured.
 */
export function SearchPicker({ options, value, onChange, disabled }: Props) {
  const items = useMemo<PickerOption[]>(
    () =>
      options.map((o) => ({
        id: o.id,
        label: o.label,
        description: o.available ? o.description : NO_SEARCHER,
        disabled: !o.available,
        tag: o.id === "auto" ? "doporučeno" : undefined,
      })),
    [options]
  );

  return (
    <RoutingPicker
      label="Web"
      icon={<Globe size={13} />}
      options={items}
      value={value}
      onChange={onChange}
      disabled={disabled}
    />
  );
}
