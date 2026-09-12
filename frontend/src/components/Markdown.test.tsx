/**
 * Rozdělení odpovědi během streamu nesmí změnit, co je na obrazovce.
 *
 * Sazba běžící odpovědi se kvůli rychlosti řeže u posledního prázdného řádku:
 * hotová část se parsuje jednou a `memo` ji pak nechá na pokoji. Celá úspora
 * ale stojí na tom, že řez padne mimo blok kódu a mimo vysázený vzorec —
 * jinak by z poloviny fence vznikl nedokončený blok a zbytek zprávy by se
 * sesypal. Testy níž jdou po obou půlkách: že se výsledek shoduje s
 * nerozděleným průchodem, a že se uvnitř bloku neřeže.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

/**
 * Normalizovaný HTML výstup.
 *
 * Mezery mezi značkami se zahazují: rozdělená sazba nechá na švu textový uzel
 * s odřádkováním navíc (ReactMarkdown ho připojí za poslední blok každého
 * průchodu), a mezi blokovými prvky se nic takového nevykreslí.
 */
function html(content: string, streaming: boolean): string {
  const { container } = render(<Markdown content={content} streaming={streaming} />);
  return container.innerHTML
    .replace(/\s+/g, " ")
    .replace(/>\s+</g, "><")
    .trim();
}

function sameEitherWay(content: string): void {
  expect(html(content, true)).toBe(html(content, false));
}

describe("sazba během streamu", () => {
  it("odstavce vypadají stejně rozdělené i vcelku", () => {
    sameEitherWay("První odstavec.\n\nDruhý odstavec.\n\nTřetí, zrovna rozepsaný");
  });

  it("seznam za odstavcem přežije řez", () => {
    sameEitherWay("Úvod k výčtu:\n\n- první\n- druhý\n- třetí\n\nA závěr.");
  });

  it("blok kódu s prázdným řádkem uvnitř se nerozpadne", () => {
    sameEitherWay("Ukázka:\n\n```python\ndef a():\n    pass\n\n\ndef b():\n    pass\n```\n\nHotovo.");
  });

  it("nedokončený blok kódu se nechá celý na živé části", () => {
    // Řez uvnitř by z prefixu udělal fence bez konce a `Hotovo.` by zmizelo
    // v kódu — proto se v takovém stavu nedělí vůbec.
    sameEitherWay("Ukázka:\n\n```python\ndef a():\n\n    return 1");
  });

  it("nadpis a tabulka vypadají stejně", () => {
    sameEitherWay("## Nadpis\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nPod tabulkou.");
  });

  it("text bez prázdného řádku se sází vcelku", () => {
    sameEitherWay("Jediný rozepsaný odstavec bez konce");
  });
});
