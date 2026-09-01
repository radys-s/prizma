import type { CSSProperties } from "react";

/**
 * The little coloured square in front of a provider's name.
 *
 * Deliberately a monogram rather than a vendor logo. Eighteen registry entries
 * would mean eighteen hand-copied brand marks, several of which change without
 * warning and none of which we have permission to redraw from memory — a wrong
 * logo is worse than no logo. A monogram is always correct, always the same
 * size, and reads as intentional.
 *
 * The colour is a stable hash of the provider id onto the brand spectrum, so
 * Groq is the same square in every message, in both themes, forever.
 */

const SPECTRUM = [
  "var(--spectrum-1)",
  "var(--spectrum-2)",
  "var(--spectrum-3)",
  "var(--spectrum-4)",
  "var(--spectrum-5)",
];

function tint(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return SPECTRUM[hash % SPECTRUM.length];
}

/** "Mistral (Codestral)" -> "M". Falls back to "?" for an unnamed provider. */
function letter(name: string): string {
  const first = name.trim()[0];
  return first ? first.toUpperCase() : "?";
}

interface Props {
  /** Provider id — what the colour is derived from. */
  id: string;
  /** Display name — what the letter is taken from. */
  name: string;
}

export function ProviderMark({ id, name }: Props) {
  // The tint travels as a custom property rather than as `color`: the letter
  // is a direct text child, so colouring the element itself would paint the
  // monogram in the same shade as the tile it sits on and hide it entirely.
  return (
    <span
      className="provider-mark"
      style={{ "--mark-tint": tint(id) } as CSSProperties}
      aria-hidden
    >
      {letter(name)}
    </span>
  );
}
