import {
  BookOpenText,
  Check,
  Cpu,
  FoldVertical,
  Globe,
  PenLine,
  Route,
  Split,
  SquareTerminal,
  X,
} from "lucide-react";

import type { Message as ChatMessage } from "../lib/types";

type State = "done" | "active" | "todo";

interface Step {
  id: string;
  label: string;
  icon: typeof Route;
  state: State;
  /** Which model took this step, shown dimmed after the label. */
  detail?: string;
  /** Subtask rows sit indented under the split step they belong to. */
  nested?: boolean;
  /** The step ran and came back with nothing. Marked, not hidden. */
  failed?: boolean;
}

/** What each local tool is called in the rail. Unknown names fall back to the
 *  raw name — a model that invents one should show up, not vanish. */
const TOOL_LABELS: Record<string, string> = {
  list_dir: "Prohlížím složku",
  read_file: "Čtu soubor",
  search_text: "Hledám v souborech",
  write_file: "Zapisuji soubor",
  run_command: "Spouštím příkaz",
};

/** The tail of a path, so a 90-character absolute path doesn't push the rail
 *  sideways. Commands and search summaries come through unchanged. */
function shorten(detail: string): string {
  const parts = detail.split(/[\\/]/);
  return parts.length > 3 ? `…\\${parts.slice(-2).join("\\")}` : detail;
}

/** Czech counts three ways. Both nouns below actually hit all three forms. */
function plural(count: number, one: string, few: string, many: string): string {
  if (count === 1) return one;
  return count >= 2 && count <= 4 ? few : many;
}

/**
 * The progress rail under a streaming answer.
 *
 * A deep run takes up to two minutes, and before this it showed one line of
 * text for the whole of it — which reads as "stuck", not "working". Every step
 * here is driven by an SSE event the backend already sends, so the rail never
 * claims a stage that isn't actually running: the search step only exists once
 * `search_start` has named its sub-questions, and the reading step only once
 * `read_start` has said which pages it is fetching.
 */
function buildSteps(message: ChatMessage): Step[] {
  const steps: Step[] = [];
  const writing = message.content.length > 0;

  // Already finished by the time the event reaches us — compaction runs before
  // the planner. Shown anyway because it explains the pause before the first
  // step and, on the fallback path, why the answer may have forgotten things.
  const compact = message.compact;
  if (compact && compact.folded > 0) {
    steps.push({
      id: "compact",
      label: compact.fallback
        ? "Starší část konverzace zkrácena"
        : `Shrnul jsem ${compact.folded} ${plural(compact.folded, "starší zprávu", "starší zprávy", "starších zpráv")}`,
      icon: FoldVertical,
      state: "done",
    });
  }

  steps.push({
    id: "plan",
    label: "Plánuji",
    icon: Route,
    state: message.planned ? "done" : "active",
  });

  const queries = message.search_queries ?? [];
  if (queries.length > 0) {
    const searching = (message.searching?.length ?? 0) > 0;
    steps.push({
      id: "search",
      label:
        queries.length === 1
          ? "Hledám na webu"
          : `Hledám ${queries.length} ${plural(queries.length, "podotázku", "podotázky", "podotázek")}`,
      icon: Globe,
      state: searching ? "active" : "done",
    });
  }

  const reading = message.reading ?? [];
  if (reading.length > 0) {
    steps.push({
      id: "read",
      label: `Čtu ${reading.length} ${plural(reading.length, "stránku", "stránky", "stránek")}`,
      icon: BookOpenText,
      // No `read_done` event: the pages are in as soon as the answer starts.
      state: writing ? "done" : "active",
    });
  }

  // The fan-out. One row naming the split, then one row per part, each
  // labelled with the model that actually took it — the whole point of the
  // stage is that they are different models, so saying which is the feature.
  const parts = message.parts ?? [];
  if (parts.length > 0) {
    const pending = parts.filter((p) => p.ok === undefined).length;
    steps.push({
      id: "split",
      label: `Rozděluji na ${parts.length} ${plural(parts.length, "část", "části", "částí")}`,
      icon: Split,
      state: pending > 0 ? "active" : "done",
    });
    for (const part of parts) {
      steps.push({
        id: `part-${part.index}`,
        label: part.title,
        detail: part.display_name,
        icon: Cpu,
        state: part.ok === undefined ? "active" : "done",
        failed: part.ok === false,
        nested: true,
      });
    }
  }

  // Every reach for the machine, in order. One row per call rather than a
  // count: which file was opened is the part worth checking, and a step that
  // says "Odmítnuto" is how the sandbox's boundary becomes visible at all.
  for (const tool of message.tools ?? []) {
    steps.push({
      id: `tool-${tool.index}`,
      label: TOOL_LABELS[tool.name] ?? tool.name,
      detail: shorten(tool.detail),
      icon: SquareTerminal,
      state: tool.ok === undefined ? "active" : "done",
      failed: tool.ok === false,
    });
  }

  steps.push({
    id: "write",
    label: message.reviewing
      ? "Reviduji odpověď"
      : parts.length > 0
        ? "Skládám odpověď"
        : "Píšu odpověď",
    icon: PenLine,
    state: writing ? "active" : "todo",
  });

  return steps;
}

export function SearchSteps({ message }: { message: ChatMessage }) {
  const steps = buildSteps(message);

  return (
    <ol className="steps" aria-live="polite">
      {steps.map((step) => {
        const Icon = step.icon;
        return (
          <li
            key={step.id}
            className="step"
            data-state={step.state}
            data-nested={step.nested ? "" : undefined}
            data-failed={step.failed ? "" : undefined}
          >
            <span className="step-mark">
              {step.failed ? (
                <X size={11} />
              ) : step.state === "done" ? (
                <Check size={11} />
              ) : (
                <Icon size={11} />
              )}
            </span>
            <span className="step-label">{step.label}</span>
            {step.detail ? <span className="step-detail">{step.detail}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
