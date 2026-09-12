import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * Callback s neměnnou identitou, který přitom vidí čerstvý stav.
 *
 * `memo(Message)` a `memo(Sidebar)` fungují jen tehdy, když se jim identita
 * props nemění. `useCallback` se závislostmi to nesplní: obsluhy tady čtou
 * `messages`, `currentId`, `streaming` nebo výběr modelu, takže by se pole
 * závislostí měnilo při každém snímku streamu a memoizace by nechytla nic.
 *
 * Tenhle hook to obrátí — funkci schová do refu, který se aktualizuje po
 * každém renderu, a ven vrací jeden jediný obal. Volající tím dostane vždy
 * poslední verzi, ale React vidí pořád stejnou referenci.
 *
 * Jediné omezení: nesmí se volat během renderu. Ref se přepisuje až v
 * layout efektu, takže by se během renderu sáhlo na předchozí verzi. Všechna
 * použití jsou obsluhy událostí, kde tohle nastat nemůže.
 */
export function useStableCallback<A extends unknown[], R>(fn: (...args: A) => R) {
  const latest = useRef(fn);

  useLayoutEffect(() => {
    latest.current = fn;
  });

  return useCallback((...args: A) => latest.current(...args), []);
}
