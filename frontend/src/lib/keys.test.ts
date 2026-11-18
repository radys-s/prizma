/**
 * The header chip, the sidebar and the "need two keys" banner have to count
 * what the router counts: keys that can answer. Search-only keys and the
 * keyless searcher do not get the app past the minimum.
 */

import { describe, expect, it } from "vitest";
import { answeringSlots } from "./keys";
import type { Provider } from "./types";

function p(id: string, capabilities: string[], configured: boolean, key_id = id): Provider {
  return {
    id,
    display_name: id,
    model: id,
    models: [id],
    capabilities,
    requires_card: false,
    notes: "",
    signup_url: "",
    key_prefix: "",
    key_id,
    configured,
  };
}

describe("answeringSlots", () => {
  it("does not count search-only keys towards the minimum", () => {
    const slots = answeringSlots([
      p("groq", ["fast", "general"], true),
      p("tavily", ["search"], true),
      p("gemini", ["general"], false),
    ]);
    expect(slots).toEqual({ configured: 1, total: 2 });
  });

  it("counts a shared account key once", () => {
    const slots = answeringSlots([
      p("mistral_code", ["code"], true, "mistral"),
      p("mistral_general", ["general"], true, "mistral"),
    ]);
    expect(slots).toEqual({ configured: 1, total: 1 });
  });
});
