import { BookOpenText, Check, FoldVertical, Globe, PenLine, Route } from "lucide-react";

import type { Message as ChatMessage } from "../lib/types";

type State = "done" | "active" | "todo";

interface Step {
  id: string;
  label: string;
  icon: typeof Route;
  state: State;
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

  steps.push({
    id: "write",
    label: message.reviewing ? "Reviduji odpověď" : "Píšu odpověď",
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
          <li key={step.id} className="step" data-state={step.state}>
            <span className="step-mark">
              {step.state === "done" ? <Check size={11} /> : <Icon size={11} />}
            </span>
            <span className="step-label">{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
