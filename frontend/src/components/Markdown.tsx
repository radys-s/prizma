import { Children, isValidElement, memo, useMemo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
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

const BR = /<br\s*\/?>/gi;
const TABLE_ROW = /^\s*\|/;

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
    return content;
  }
  BR.lastIndex = 0;
  return content
    .split("\n")
    .map((line) => line.replace(BR, TABLE_ROW.test(line) ? " " : "\n"))
    .join("\n");
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
    () => [HIGHLIGHT, rehypeCitations(sourceCount, anchorPrefix)] as never,
    [sourceCount, anchorPrefix]
  );

  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
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
