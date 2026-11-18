/**
 * The toast arrives the moment a provider hits its limit — before anyone knows
 * whether another model will answer. When none did, "Odpovídá jiný model"
 * stood next to an error bubble saying nobody answered. The toast may only
 * say what is already true: this provider is skipped, and until when.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LimitToasts, withoutNotices, type LimitNoticeItem } from "./LimitToast";

function item(until: number | null): LimitNoticeItem {
  return { id: "groq:m", provider: "Groq", model: "groq/m", wait: "", until };
}

describe("LimitToasts", () => {
  it("does not promise that another model answers", () => {
    render(<LimitToasts items={[item(null)]} onDismiss={() => {}} />);
    expect(screen.queryByText(/odpovídá jiný model/i)).toBeNull();
    expect(screen.getByText(/přeskakuje/)).toBeTruthy();
  });

  it("still says when the provider is tried again", () => {
    render(<LimitToasts items={[item(Date.now() + 5 * 60_000)]} onDismiss={() => {}} />);
    expect(screen.getByText(/za 5 min/)).toBeTruthy();
    expect(screen.queryByText(/odpovídá jiný model/i)).toBeNull();
  });
});

describe("withoutNotices", () => {
  it("drops only the notices this run raised", () => {
    const earlier: LimitNoticeItem = { ...item(null), id: "mistral:m", provider: "Mistral" };
    const fromRun = item(null);
    expect(withoutNotices([earlier, fromRun], new Set([fromRun.id]))).toEqual([earlier]);
  });
});
