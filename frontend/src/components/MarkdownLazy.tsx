import { Suspense, lazy, memo } from "react";

/**
 * `Markdown` na požádání, ne při startu.
 *
 * Statický řetěz `main.tsx → App.tsx → Message → Markdown` stáhl celý parser,
 * zvýrazňovač i sazeč vzorců dřív, než se objevilo pole na čtyřmístný kód.
 * Nic z toho není potřeba, dokud není otevřená konverzace — a než se to
 * dotáhne, ukáže se holý text místo prázdna, takže první tokeny odpovědi
 * nikam nezmizí.
 */
const Markdown = lazy(() =>
  import("./Markdown").then((module) => ({ default: module.Markdown }))
);

interface Props {
  content: string;
  sourceCount?: number;
  anchorPrefix?: string;
  streaming?: boolean;
}

export const MarkdownLazy = memo(function MarkdownLazy(props: Props) {
  return (
    <Suspense fallback={<div className="prose prose-plain">{props.content}</div>}>
      <Markdown {...props} />
    </Suspense>
  );
});
