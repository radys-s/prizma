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
        {content}
      </ReactMarkdown>
    </div>
  );
});
