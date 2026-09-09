import { Brain, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Message as ChatMessage } from "../lib/types";
import { Markdown } from "./Markdown";

interface Props {
  message: ChatMessage;
}

/** "6,2 s" — desetina sekundy stačí, na tisícinách tady nezáleží. */
function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
}

/**
 * Uvažování modelu nad odpovědí — otevřené, dokud přemýšlí, pak sbalené.
 *
 * Proč vlastní blok a ne text v bublině: uvažování je poznámkový blok, ne
 * odpověď. Bývá delší než ta odpověď, píše se v jiném rejstříku (často
 * anglicky, i když se odpovídá česky) a končí i ve slepé uličce, kterou model
 * sám zavrhne. Smíchat obojí znamená, že čtenář nepozná, co je závěr.
 *
 * Proč se sbalí samo: dokud se nic jiného neděje, je uvažování jediná známka
 * života a má být vidět. Jakmile začne přibývat odpověď, je z něj kontext —
 * a kontext nemá přetlačovat obsah. Ruční přepnutí má přednost: kdo si blok
 * otevřel, chce ho otevřený i potom.
 */
export function Reasoning({ message }: Props) {
  const text = message.reasoning ?? "";
  const streaming = message.streaming === true;
  const hasAnswer = message.content.length > 0;

  const [open, setOpen] = useState(streaming);
  const touched = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Živé počítadlo. Server pošle přesné číslo až v `done`, ale mlčící blok s
  // nápisem „Přemýšlím…" a bez čísla vypadá po deseti sekundách zaseknutě.
  const startedAt = useRef(Date.now());
  const [elapsed, setElapsed] = useState(0);
  const thinking = streaming && !hasAnswer;

  useEffect(() => {
    if (!thinking) return;
    const id = window.setInterval(() => setElapsed(Date.now() - startedAt.current), 100);
    return () => window.clearInterval(id);
  }, [thinking]);

  useEffect(() => {
    if (touched.current) return;
    // Sbalit v okamžiku, kdy odpověď převezme roli ukazatele postupu.
    if (hasAnswer || !streaming) setOpen(false);
  }, [hasAnswer, streaming]);

  useEffect(() => {
    if (!open || !thinking) return;
    const el = bodyRef.current;
    // Držet konec v dohledu, jinak text odteče nahoru a je vidět jen minulost.
    if (el) el.scrollTop = el.scrollHeight;
  }, [text, open, thinking]);

  if (!text.trim()) return null;

  const measured = message.reasoning_ms ?? (thinking ? elapsed : 0);
  const label = thinking
    ? `Přemýšlím… ${seconds(elapsed)}`
    : measured > 0
      ? `Přemýšlel ${seconds(measured)}`
      : "Uvažování modelu";

  return (
    <div className={`reasoning${open ? " open" : ""}${thinking ? " live" : ""}`}>
      <button
        type="button"
        className="reasoning-head"
        aria-expanded={open}
        onClick={() => {
          touched.current = true;
          setOpen((was) => !was);
        }}
      >
        <ChevronRight size={13} className="reasoning-caret" aria-hidden />
        <Brain size={13} aria-hidden />
        <span className="reasoning-label">{label}</span>
      </button>
      {open && (
        <div className="reasoning-body" ref={bodyRef}>
          {/* Markdown ano — Gemini si uvažování samo nadpisuje (`**Krok 1**`) a
              syrové hvězdičky vypadají jako chyba. Nadpisy uvnitř bloku jsou
              ale stylem sražené na velikost běžného textu, aby se z poznámek
              nestala druhá, hlasitější odpověď. */}
          <Markdown content={text} />
        </div>
      )}
    </div>
  );
}
