import { absoluteTime } from "./date";
import type { Message } from "./types";

/**
 * A conversation as Markdown.
 *
 * Done in the browser rather than on the server because everything it needs is
 * already here: the messages are loaded, the provider names are on screen, and
 * a `/export` endpoint would be a second place that has to agree with `Message`
 * about what an answer consists of.
 *
 * The sources are numbered exactly as they are in the UI, because the `[n]`
 * markers inside the answer text point at them — renumbering or dropping them
 * would leave the exported answer citing nothing.
 */
export function conversationToMarkdown(
  title: string,
  messages: Message[],
  providerNames: Record<string, string> = {}
): string {
  const out: string[] = [`# ${title}`, ""];

  for (const message of messages) {
    if (message.role === "system") continue;

    if (message.role === "user") {
      out.push("## Otázka", "", message.content.trim(), "");
      continue;
    }

    out.push("## Odpověď", "");

    const meta: string[] = [];
    const provider = message.provider_id
      ? providerNames[message.provider_id] ?? message.provider_id
      : null;
    if (provider) meta.push(provider);
    if (message.model_name) meta.push(`\`${message.model_name}\``);
    if (message.search_mode) meta.push(message.search_mode);
    if (message.created_at) meta.push(absoluteTime(message.created_at));
    if (meta.length > 0) out.push(`*${meta.join(" · ")}*`, "");

    out.push(message.content.trim(), "");

    const sources = message.sources ?? [];
    if (sources.length > 0) {
      out.push("**Zdroje**", "");
      sources.forEach((source, i) => {
        out.push(`${i + 1}. [${source.title || source.url}](${source.url})`);
      });
      out.push("");
    }
  }

  return out.join("\n");
}

/** Turns a title into something a filesystem will accept on Windows too. */
export function exportFilename(title: string): string {
  const base = title
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 60);
  return `${base || "prizma-konverzace"}.md`;
}

/**
 * Hands the file to the browser through an object URL.
 *
 * A `data:` URL would be simpler but is size-capped in some browsers, and a
 * long conversation is exactly the one worth exporting.
 */
export function downloadMarkdown(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next tick: revoking synchronously can race the download in
  // Safari, and the object would otherwise be held for the life of the page.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
