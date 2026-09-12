const FENCE_LINE = /^\s{0,3}(?:```|~~~)/;
const LINK = /\[([^\]]*)\]\([^)]*\)/g;
const EMPHASIS = /(\*\*|__|\*|_|`|~~)/g;
const LINE_PREFIX = /^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+\.\s+)/;

/**
 * Text pro odečítač obrazovky, ne markdown.
 *
 * Do živé oblasti šlo dosud přesně to, co přišlo ze streamu — takže čtečka
 * předčítala „zpětné apostrofy python“, hvězdičky kolem tučného písma i celý
 * zdrojový kód řádek po řádku. Fence se proto počítají napříč voláními
 * (`fenced` drží stav mezi chunky) a obsah bloku se nahradí jedinou větou;
 * ostatní značky se z řádku sloupnou. Odkaz zůstane popiskem, protože URL se
 * neposlouchá.
 */
export function speakable(text: string, fenced: boolean): { said: string; fenced: boolean } {
  const out: string[] = [];
  let inside = fenced;

  for (const line of text.split("\n")) {
    if (FENCE_LINE.test(line)) {
      if (!inside) out.push("Blok kódu.");
      inside = !inside;
      continue;
    }
    if (inside) continue;
    const plain = line.replace(LINE_PREFIX, "").replace(LINK, "$1").replace(EMPHASIS, "").trim();
    if (plain) out.push(plain);
  }

  return { said: out.join(" "), fenced: inside };
}
