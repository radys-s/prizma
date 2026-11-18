import { TriangleAlert, X } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * Oznámení o vyčerpaném limitu.
 *
 * Vzniklo z jedné konkrétní stížnosti: model, který odpovídal celý den, se
 * najednou přestal objevovat a nikde nebylo proč. Router se totiž zachoval
 * správně — narazil na limit, přepnul jinam a odpověď stejně přišla — jenže
 * uživateli tím zůstalo jen to, že „je to najednou horší".
 *
 * Proto popup a ne řádek v bublině: událost se netýká té jedné odpovědi, ale
 * příštích několika. A proto odpočet místo statické věty — „za 7 min" je za
 * pět minut lež, kdežto odpočet stárne správně.
 */
export interface LimitNoticeItem {
  /** Stabilní klíč, ať se stejný provider neobjeví dvakrát pod sebou. */
  id: string;
  /** Jméno providera bez modelu v závorce, např. „Groq". */
  provider: string;
  /** Slug modelu, který na limit narazil. */
  model: string;
  /** Věta ze serveru, když se čas nedal spočítat („", když ho provider neřekl). */
  wait: string;
  /** Časové razítko, kdy má smysl zkusit znovu. null = provider čas neřekl. */
  until: number | null;
}

/**
 * Oznámení bez těch, která vyvolal jeden běh. Když běh skončí tím, že
 * neodpověděl nikdo, bublina s chybou vyjmenuje tytéž limity i s časem —
 * karty navíc by jen ležely přes ni.
 */
export function withoutNotices(items: LimitNoticeItem[], ids: Set<string>): LimitNoticeItem[] {
  return items.filter((item) => !ids.has(item.id));
}

/** Kolik zbývá, řečeno tak, jak by to řekl člověk. */
function remaining(until: number | null, now: number): string {
  if (until === null) return "";
  const seconds = Math.max(0, Math.round((until - now) / 1000));
  if (seconds === 0) return "";
  if (seconds < 90) return `za ${seconds} s`;
  if (seconds < 3600) return `za ${Math.ceil(seconds / 60)} min`;
  return `za ${Math.ceil(seconds / 3600)} h`;
}

/**
 * Každé oznámení po chvíli samo zmizí — i to s odpočtem. Dřív zůstávalo, dokud
 * neodtikalo, takže hodinový limit znamenal hodinu karty přes začátek
 * konverzace. Čas, kdy to zkusit znovu, říká i bublina s chybou.
 */
const VISIBLE_MS = 12000;

function LimitCard({
  item,
  onDismiss,
}: {
  item: LimitNoticeItem;
  onDismiss: (id: string) => void;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const hide = window.setTimeout(() => onDismiss(item.id), VISIBLE_MS);
    if (item.until === null) return () => window.clearTimeout(hide);
    // Vteřinový tik jen dokud je karta vidět a je co odpočítávat.
    const tick = window.setInterval(() => {
      const next = Date.now();
      setNow(next);
      if (next >= (item.until as number)) onDismiss(item.id);
    }, 1000);
    return () => {
      window.clearTimeout(hide);
      window.clearInterval(tick);
    };
  }, [item.id, item.until, onDismiss]);

  const left = item.until === null ? item.wait : remaining(item.until, now);

  return (
    <div className="limit-toast" role="status" aria-live="polite">
      <span className="limit-icon" aria-hidden="true">
        <TriangleAlert size={15} />
      </span>
      <div className="limit-body">
        <strong className="limit-title">
          {item.provider}: vyčerpaný limit
        </strong>
        <span className="limit-detail">
          {/* Jen to, co už platí: jestli odpoví jiný model, se v tuhle chvíli
              ještě neví — a když ne, řekne to bublina s chybou. */}
          {left
            ? `Router ho přeskakuje, znovu ho zkusí ${left}.`
            : "Router ho přeskakuje. Provider neřekl, na jak dlouho."}
        </span>
        {item.model && <span className="limit-model">{item.model}</span>}
      </div>
      <button
        type="button"
        className="icon-btn limit-close"
        onClick={() => onDismiss(item.id)}
        aria-label="Zavřít oznámení"
        title="Zavřít"
      >
        <X size={15} />
      </button>
    </div>
  );
}

export function LimitToasts({
  items,
  onDismiss,
}: {
  items: LimitNoticeItem[];
  onDismiss: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="limit-stack">
      {items.map((item) => (
        <LimitCard key={item.id} item={item} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
