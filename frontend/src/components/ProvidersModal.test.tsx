/**
 * The key dialog is the first-run blocker: the app answers nothing until two
 * answering keys are in. These tests pin the two things that make that step
 * short — the answering keys come first, and the search-only keys (which do
 * not count towards the two) sit in their own section below them.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Provider, ToolsSettings } from "../lib/types";
import { ProvidersModal } from "./ProvidersModal";

function provider(id: string, name: string, capabilities: string[], configured = false): Provider {
  return {
    id,
    display_name: name,
    model: `${id}/model`,
    models: [`${id}/model`],
    capabilities,
    requires_card: false,
    notes: "",
    signup_url: "https://example.com",
    key_prefix: "",
    key_id: id,
    configured,
  };
}

const PROVIDERS: Provider[] = [
  provider("groq", "Groq", ["fast", "general"], true),
  provider("jina", "Jina", ["search"]),
  provider("exa", "Exa", ["search"]),
  provider("gemini", "Gemini", ["general", "long_context"]),
];

const TOOLS: ToolsSettings = {
  local_tools: false,
  local_write: false,
  tools_roots: "",
  tools_roots_effective: [],
  tools_supported: true,
};

function renderModal() {
  return render(
    <ProvidersModal
      providers={PROVIDERS}
      readPages={true}
      onReadPagesChange={() => {}}
      customInstructions=""
      customInstructionsMax={2000}
      onCustomInstructionsChange={() => {}}
      tools={TOOLS}
      onToolsChange={() => {}}
      onClose={() => {}}
      onChanged={() => {}}
      auth={null}
      onAuthChanged={() => {}}
    />
  );
}

beforeEach(() => {
  // The local-model section asks the server on mount; nothing answers here.
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProvidersModal", () => {
  it("opens on the answering keys, before any other setting", () => {
    renderModal();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings[0]).toMatch(/^Modely na odpovědi/);
    expect(headings[1]).toMatch(/^Hledání na webu/);
  });

  it("keeps search-only keys out of the answering section", () => {
    renderModal();
    const answering = screen.getByRole("region", { name: /Modely na odpovědi/ });
    const search = screen.getByRole("region", { name: /Hledání na webu/ });

    expect(within(answering).getByText("Groq")).toBeTruthy();
    expect(within(answering).getByText("Gemini")).toBeTruthy();
    expect(within(answering).queryByText("Jina")).toBeNull();
    expect(within(search).getByText("Jina")).toBeTruthy();
    expect(within(search).getByText("Exa")).toBeTruthy();
  });

  it("says how many answering keys are in against the two it needs", () => {
    renderModal();
    const answering = screen.getByRole("region", { name: /Modely na odpovědi/ });
    expect(within(answering).getByText(/1 ze 2/)).toBeTruthy();
  });

  it("does not claim the key lives in Credential Manager", () => {
    renderModal();
    expect(screen.queryByText(/Credential Manager/)).toBeNull();
  });
});
