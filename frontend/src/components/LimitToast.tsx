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
 * Bez známého času oznámení po chvíli samo zmizí. S časem zůstane, dokud
 * neodtiká — mizí přesně ve chvíli, kdy přestane platit.
 */
const BLIND_TIMEOUT_MS = 12000;

function LimitCard({
  item,
  onDismiss,
}: {
  item: LimitNoticeItem;
  onDismiss: (id: string) => void;
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (item.until === null) {
      const timer = window.setTimeout(() => onDismiss(item.id), BLIND_TIMEOUT_MS);
      return () => window.clearTimeout(timer);
    }
    // Vteřinový tik jen dokud je co odpočítávat. Interval, který běží dál po
    // vypršení, by byl tichý žrout snímků na stránce, kde se stejně nic nemění.
    const timer = window.setInterval(() => {
      const next = Date.now();
      setNow(next);
      if (next >= (item.until as number)) onDismiss(item.id);
    }, 1000);
    return () => window.clearInterval(timer);
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
          {left
            ? `Zkusím ho znovu ${left}. Zatím odpovídá jiný model.`
            : "Odpovídá jiný model. Provider neřekl, na jak dlouho."}
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
