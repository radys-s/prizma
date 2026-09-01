import { FoldVertical } from "lucide-react";

import type { CompactInfo } from "../lib/types";

function plural(count: number): string {
  if (count === 1) return "zpráva";
  return count >= 2 && count <= 4 ? "zprávy" : "zpráv";
}

/**
 * The seam between the summarised prefix and the turns still being sent whole.
 *
 * Nothing above this line was deleted — every message is still in the database
 * and still on screen. What changed is what the *model* sees from here down,
 * and that is worth saying: an answer that has forgotten a detail from message
 * three is otherwise indistinguishable from a model that simply got it wrong.
 */
export function CompactDivider({ info }: { info: CompactInfo }) {
  const label = info.fallback
    ? "Starší část konverzace se do kontextu nevešla"
    : "Starší část konverzace byla shrnuta";

  const title = info.fallback
    ? `Shrnutí se nepodařilo vytvořit, takže ${info.folded} ${plural(info.folded)} nad touhle čarou ` +
      `model nevidí vůbec. Zprávy zůstávají uložené — jen se neposílají.`
    : `${info.folded} ${plural(info.folded)} nad touhle čarou model dostává jako shrnutí, ` +
      `zbylých ${info.kept} beze změny. Nic se nesmazalo.`;

  return (
    <div className="compact-divider" role="separator" title={title}>
      <span className="compact-divider-label">
        <FoldVertical size={12} aria-hidden />
        {label}
        <span className="compact-divider-count">{info.folded}</span>
      </span>
    </div>
  );
}
