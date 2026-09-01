/**
 * Turns `[1]` in an answer into a link to source 1.
 *
 * Written as a rehype plugin rather than a regex over the raw markdown for two
 * reasons: by the time the tree exists, code fences and inline code are already
 * their own nodes, so `arr[0]` in a Python snippet can be skipped structurally
 * instead of guessed at; and a link inserted here survives whatever the
 * markdown renderer does around it.
 *
 * `unist-util-visit` would do the walk, but it is only a *transitive*
 * dependency of the remark packages — importing it directly would mean relying
 * on a package.json we don't control. The walk below is fifteen lines.
 */

/** The subset of hast this plugin touches. */
interface TextNode {
  type: "text";
  value: string;
}

interface ElementNode {
  type: "element";
  tagName: string;
  properties?: Record<string, unknown>;
  children?: Node[];
}

type Node = TextNode | ElementNode | { type: string; children?: Node[] };

/** Inside these, `[2]` is source code or already a link — never a citation. */
const SKIP = new Set(["code", "pre", "a"]);

const PATTERN = /\[(\d{1,2})\]/g;

function isElement(node: Node): node is ElementNode {
  return node.type === "element";
}

function isText(node: Node): node is TextNode {
  return node.type === "text";
}

/** `[3]` -> [text, <a>, text] for a text node, or null when there is nothing. */
function split(value: string, count: number, prefix: string): Node[] | null {
  PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null;
  let cursor = 0;
  const out: Node[] = [];

  while ((match = PATTERN.exec(value)) !== null) {
    const index = Number(match[1]);
    // A number with no source behind it stays plain text: an answer that says
    // "[15]" with three sources is quoting something, not citing.
    if (index < 1 || index > count) continue;
    if (match.index > cursor) out.push({ type: "text", value: value.slice(cursor, match.index) });
    out.push({
      type: "element",
      tagName: "a",
      properties: {
        href: `#${prefix}-src-${index}`,
        className: ["citation"],
        "data-citation": String(index),
        title: `Přejít na zdroj ${index}`,
      },
      children: [{ type: "text", value: String(index) }],
    });
    cursor = match.index + match[0].length;
  }

  if (out.length === 0) return null;
  if (cursor < value.length) out.push({ type: "text", value: value.slice(cursor) });
  return out;
}

function walk(node: Node, count: number, prefix: string): void {
  const children = (node as ElementNode).children;
  if (!children) return;

  const next: Node[] = [];
  for (const child of children) {
    if (isElement(child) && SKIP.has(child.tagName)) {
      next.push(child);
      continue;
    }
    if (isText(child)) {
      const replacement = split(child.value, count, prefix);
      if (replacement) {
        next.push(...replacement);
        continue;
      }
      next.push(child);
      continue;
    }
    walk(child, count, prefix);
    next.push(child);
  }
  (node as ElementNode).children = next;
}

/**
 * Rehype plugin factory. `count` is how many sources the message actually has;
 * pass 0 to disable linking entirely (which is what an answer with no sources
 * gets, so `[1]` in ordinary prose is left alone). `prefix` scopes the anchor
 * to one message — two answers in a thread would otherwise both claim `#src-1`.
 */
export function rehypeCitations(count: number, prefix: string) {
  return () => (tree: Node) => {
    if (count > 0) walk(tree, count, prefix);
  };
}
