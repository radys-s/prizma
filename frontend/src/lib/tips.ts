/**
 * Short things about Prizma that are not visible from the interface.
 *
 * Every line here has to be *true of this build* and has to say what it costs,
 * not just what it does. "Max je nejlepší" is marketing; "Max přidá revizi a
 * trvá zhruba dvakrát tak dlouho" is the sentence someone can act on. The rule
 * for adding one: if it does not name a control the user can find, or if it
 * promises an upside with no downside, it does not belong in this list.
 *
 * They render on the empty screen, one at a time, picked at random. That is
 * deliberately the only place — a tip that follows you around the app stops
 * being a tip and becomes noise.
 */
export interface Tip {
  /** Grouping only, so the list stays scannable when it grows. */
  topic: "router" | "web" | "keys" | "ovládání" | "soukromí";
  text: string;
}

export const TIPS: Tip[] = [
  {
    topic: "router",
    text: "Sílu modelu přepneš vlevo dole u pole na psaní. Light odpoví hned, Max přidá plán a revizi navíc — je to znatelně lepší, ale trvá to zhruba dvakrát tak dlouho.",
  },
  {
    topic: "router",
    text: "„Auto“ neznamená náhodu: model nejdřív zváží, co je úkol zač, a teprve pak vybere providera. Když chceš konkrétní, přepni ho v pickeru vedle síly.",
  },
  {
    topic: "router",
    text: "Když jeden provider spadne nebo dojde na limit, Prizma sama zkusí dalšího v pořadí. Proto se vyplatí připojit víc klíčů, i když používáš pořád jeden.",
  },
  {
    topic: "web",
    text: "Globus má čtyři polohy. „Deep research“ rozdělí dotaz na podotázky, každou vyhledá zvlášť a nejlepší stránky si přečte celé — počítej se dvěma minutami místo pár vteřin.",
  },
  {
    topic: "web",
    text: "Čísla v hranatých závorkách v odpovědi jsou odkazy na stránky, které Prizma opravdu četla. Klikni na ně, když ti něco nesedí.",
  },
  {
    topic: "web",
    text: "Bez vyhledávacího klíče hledá Prizma jen ve Wikipedii a na arXivu. Na pojmy to stačí, na to, co se stalo tenhle týden, ne.",
  },
  {
    topic: "keys",
    text: "Vlastní model puštěný u tebe v počítači (Ollama, LM Studio, llama.cpp) se dá připojit v Nastavení jako další provider. Nic pak neodchází ven a nestojí to kredity — zaplatíš rychlostí.",
  },
  {
    topic: "keys",
    text: "„Uloženo“ nic nedokazuje. Tlačítko „Otestovat“ u klíče pošle skutečný požadavek, takže zjistíš i vyčerpaný kredit nebo zrušený model.",
  },
  {
    topic: "ovládání",
    text: "Ctrl+K otevře příkazy — přepnutí modelu, režimu webu i skok do jiné konverzace, aniž bys sáhl na myš.",
  },
  {
    topic: "ovládání",
    text: "Šipka nahoru v prázdném poli vytáhne poslední odeslanou zprávu k úpravě. Konverzace se pak přepočítá od ní dál.",
  },
  {
    topic: "ovládání",
    text: "Esc zastaví generování. Rozepsaná odpověď zůstane — nezmizí to, co už přišlo.",
  },
  {
    topic: "ovládání",
    text: "Otazník kdekoli v aplikaci ukáže všechny klávesové zkratky.",
  },
  {
    topic: "ovládání",
    text: "Dlouhou konverzaci Prizma sama zkrátí do shrnutí, aby se vešla do kontextu. Ve vlákně to pozná podle dělící čáry — nad ní se odpovídá už jen ze souhrnu.",
  },
  {
    topic: "soukromí",
    text: "V Nastavení si můžeš nastavit vlastní instrukce — jazyk, délku, formát. Připojí se ke každé odpovědi, takže je nemusíš opakovat.",
  },
  {
    topic: "soukromí",
    text: "Klíče se šifrují AES-256 a zpátky do prohlížeče je aplikace nikdy neposílá — ani tobě.",
  },
];

/**
 * One tip, picked at random, optionally avoiding the one already on screen.
 *
 * The `exclude` guard matters more than it looks: with fifteen tips a naive
 * random pick repeats itself roughly every seventh click, and a "next tip"
 * button that shows the same tip reads as broken.
 */
export function randomTip(exclude?: Tip): Tip {
  const pool = exclude ? TIPS.filter((t) => t !== exclude) : TIPS;
  return pool[Math.floor(Math.random() * pool.length)];
}
