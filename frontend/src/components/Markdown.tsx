import "katex/dist/katex.min.css";

import { Children, isValidElement, memo, useMemo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { rehypeCitations } from "../lib/citations";
import { CodeBlock } from "./CodeBlock";

/** Flattens a rendered React subtree back to plain text (for the copy button). */
function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) return nodeText((node.props as { children?: ReactNode }).children);
  return "";
}

/**
 * `detect: false` keeps highlighting to fenced blocks that declare a language.
 * Auto-detection would re-guess the language on every streamed chunk, which is
 * the expensive part — this way live rendering stays cheap.
 */
const HIGHLIGHT = [rehypeHighlight, { detect: false, ignoreMissing: true }];

/**
 * `throwOnError: false` je tady zásadní: model si vymyslí nějaké
 * `\notacommand` a KaTeX by jinak shodil render celé zprávy. Takhle
 * zčervená jen ten jeden vzorec a zbytek odpovědi zůstane čitelný.
 */
const KATEX = [rehypeKatex, { throwOnError: false, strict: false, output: "html" }];

/** Jeden dolar je cena, ne vzorec — viz `dollarise`. */
const MATH = [remarkMath, { singleDollarTextMath: false }];

const BR = /<br\s*\/?>/gi;
const TABLE_ROW = /^\s*\|/;

/**
 * Modely píšou matematiku v LaTeXu, jenže skoro nikdy dolary. Chodí
 * `\(x\)` a `\[x\]`, což markdown čte jako escapovanou závorku — na
 * obrazovce z toho zbude holé `(x)` a uvnitř syrový `\frac{a}{b}`.
 *
 * Proto se převádějí na dolarovou podobu, kterou `remark-math` zná. Inline
 * varianta schválně na **dva** dolary: jeden je vypnutý (`singleDollarTextMath`),
 * aby věta „stálo to $20, teď $30" nezmizela v sazbě vzorce. Za dva dolary
 * se nikdo neplete, takže cena zůstane cenou a vzorec vzorcem.
 */
const MATH_ESCAPE = /\\[([]/;
const DISPLAY_MATH = /\\\[([\s\S]*?)\\\]/g;
const INLINE_MATH = /\\\(([\s\S]*?)\\\)/g;

/** Fenced bloky i inline `kód` — tam se nesahá. */
const CODE_SPAN = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/;

function dollarise(content: string): string {
  if (!MATH_ESCAPE.test(content)) return content;
  // Liché indexy jsou kód (regex má jednu skupinu), ty se přeskakují.
  return content
    .split(CODE_SPAN)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(DISPLAY_MATH, (_m, body: string) => `\n\n$$\n${body.trim()}\n$$\n\n`)
            // Nedokončený vzorec (stream došel po `\(`) se nechytí a chvíli
            // svítí syrový. Další chunk to spraví — lepší než sázet půlku.
            .replace(INLINE_MATH, (_m, body: string) => `$$${body.trim()}$$`)
    )
    .join("");
}

/**
 * Models write `<br>` into markdown constantly, because a GFM table cell cannot
 * contain a newline and that is the only way they know to break a line inside
 * one. We do not render raw HTML — `rehype-raw` would hand model output a
 * direct path into the DOM — so those tags arrive on screen as literal text,
 * which looks like a bug in the app rather than a quirk of the model.
 *
 * So they are translated instead of rendered. Outside a table a `<br>` really
 * is a line break. Inside one it cannot be, since a newline would end the row —
 * a space is the most it can honestly become, and it reads correctly because
 * the models that do this are already writing bullets on either side.
 */
function normalise(content: string): string {
  if (!BR.test(content)) {
    BR.lastIndex = 0;
    return dollarise(content);
  }
  BR.lastIndex = 0;
  return dollarise(
    content
      .split("\n")
      .map((line) => line.replace(BR, TABLE_ROW.test(line) ? " " : "\n"))
      .join("\n")
  );
}

interface Props {
  content: string;
  /** How many sources this message has, so `[n]` links only when n exists. */
  sourceCount?: number;
  /** Scopes the `#…-src-n` anchors to one message. */
  anchorPrefix?: string;
}

export const Markdown = memo(function Markdown({
  content,
  sourceCount = 0,
  anchorPrefix = "live",
}: Props) {
  // Rebuilt only when the source count changes: react-markdown reconstructs its
  // processor whenever this array's identity does, and that is not cheap to do
  // on every streamed token.
  const source = useMemo(() => normalise(content), [content]);

  const rehypePlugins = useMemo(
    // KaTeX až po highlightu: sazbu vzorce už nemá co zvýrazňovat.
    () => [HIGHLIGHT, KATEX, rehypeCitations(sourceCount, anchorPrefix)] as never,
    [sourceCount, anchorPrefix]
  );

  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, MATH] as never}
        rehypePlugins={rehypePlugins}
        components={{
          pre({ children }) {
            const only = Children.toArray(children)[0];
            const className = isValidElement(only)
              ? ((only.props as { className?: string }).className ?? "")
              : "";
            const language = /language-([\w-]+)/.exec(className)?.[1] ?? "";
            return (
              <CodeBlock language={language} text={nodeText(children)}>
                {children}
              </CodeBlock>
            );
          },
          table({ children }) {
            return (
              <div className="table-wrap">
                <table>{children}</table>
              </div>
            );
          },
          // `node` is react-markdown's own hast handle. It is pulled out and
          // dropped here because spreading `rest` onto the anchor would
          // otherwise stamp `node="[object Object]"` into the DOM.
          a({ href, children, node: _node, ...rest }) {
            // Citations are in-page anchors handled by Message.tsx; sending
            // them to a new tab would open a blank one.
            if (href?.includes("-src-")) {
              return (
                <a href={href} {...rest}>
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
});
