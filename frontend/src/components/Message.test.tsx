/**
 * A failed answer says what to do next, and when that is "go to the keys",
 * the way there is one click away instead of a trip to the sidebar.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Message as ChatMessage } from "../lib/types";
import { Message } from "./Message";

const FAILED: ChatMessage = {
  role: "assistant",
  error: true,
  content:
    "Žádný provider neodpověděl.\n\n- Groq: vyčerpaný limit.\n- Cerebras: klíč byl odmítnut.\n\n" +
    "Co s tím: zkontroluj klíč (Cerebras) v „API klíče“ — tlačítko Otestovat řekne víc.",
};

describe("Message error bubble", () => {
  it("offers the key dialog when the error points there", () => {
    const onOpenKeys = vi.fn();
    render(<Message message={FAILED} onOpenKeys={onOpenKeys} />);
    fireEvent.click(screen.getByRole("button", { name: "Otevřít API klíče" }));
    expect(onOpenKeys).toHaveBeenCalledOnce();
  });

  it("does not offer it for an error that has nothing to do with keys", () => {
    render(
      <Message
        message={{ role: "assistant", error: true, content: "Spojení se serverem se přerušilo." }}
        onOpenKeys={() => {}}
      />
    );
    expect(screen.queryByRole("button", { name: "Otevřít API klíče" })).toBeNull();
  });

  it("keeps one failure per line", () => {
    render(<Message message={FAILED} />);
    const text = document.querySelector(".message-error-text");
    expect(text?.textContent).toContain("\n- Groq");
    expect(text?.className).toContain("message-error-text");
  });
});
