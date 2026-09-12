/**
 * Živá oblast má předčítat odpověď, ne její zápis.
 *
 * Odečítač obrazovky dostával surový markdown přímo ze streamu, takže z
 * odpovědi s ukázkou kódu zaznělo „zpětný apostrof zpětný apostrof zpětný
 * apostrof python“ a pak řádek po řádku celý program. Testy níž hlídají obě
 * poloviny opravy: že se značky sloupnou, a že se stav bloku kódu udrží i
 * mezi dvěma chunky, protože fence a její konec skoro nikdy nedorazí spolu.
 */

import { describe, expect, it } from "vitest";
import { speakable } from "./speakable";

describe("speakable", () => {
  it("sloupne zvýraznění a nadpisy", () => {
    expect(speakable("## Nadpis\n**tučně** a *kurzíva*", false).said).toBe(
      "Nadpis tučně a kurzíva"
    );
  });

  it("z odkazu nechá popisek, ne URL", () => {
    expect(speakable("Viz [dokumentaci](https://example.com/a/b).", false).said).toBe(
      "Viz dokumentaci."
    );
  });

  it("blok kódu ohlásí jednou a obsah zahodí", () => {
    const { said, fenced } = speakable("```python\nprint(1)\nprint(2)\n```\nHotovo.", false);
    expect(said).toBe("Blok kódu. Hotovo.");
    expect(fenced).toBe(false);
  });

  it("nedokončený blok drží stav pro další chunk", () => {
    const first = speakable("Ukázka:\n```python\nprint(1)", false);
    expect(first.said).toBe("Ukázka: Blok kódu.");
    expect(first.fenced).toBe(true);

    // Druhý chunk začíná uvnitř bloku — nesmí se předčítat jako věta.
    const second = speakable("print(2)\n```\nA je to.", first.fenced);
    expect(second.said).toBe("A je to.");
    expect(second.fenced).toBe(false);
  });

  it("odrážky a citace ztratí značku, ne text", () => {
    expect(speakable("- první\n- druhý\n> poznámka", false).said).toBe("první druhý poznámka");
  });

  it("samotná fence nevydá nic k předčítání", () => {
    expect(speakable("```", true).said).toBe("");
  });
});
