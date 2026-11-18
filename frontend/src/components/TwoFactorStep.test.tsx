/**
 * The sixth digit is the submit. A full code has nothing left to decide, so
 * making the user find and press "Ověřit a vstoupit" was a step that only
 * ever had one answer.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const verifyTwoFactor = vi.fn();
const verifyTotp = vi.fn();

vi.mock("../lib/api", () => ({
  verifyTwoFactor: (...args: unknown[]) => verifyTwoFactor(...args),
  verifyTotp: (...args: unknown[]) => verifyTotp(...args),
  resendCode: vi.fn(),
}));

import type { TwoFactorChallenge } from "../lib/types";
import { TwoFactorStep } from "./TwoFactorStep";

const CHALLENGE: TwoFactorChallenge = {
  stage: "2fa",
  challenge_id: 1,
  email: "a…@example.com",
  method: "email",
};

beforeEach(() => {
  verifyTwoFactor.mockReset();
  verifyTotp.mockReset();
});

function typeCode(code: string) {
  code.split("").forEach((digit, i) => {
    fireEvent.change(screen.getByLabelText(`Číslice ${i + 1}`), { target: { value: digit } });
  });
}

describe("TwoFactorStep", () => {
  it("verifies as soon as the sixth digit is typed", async () => {
    verifyTwoFactor.mockResolvedValue(undefined);
    const onVerified = vi.fn();
    render(<TwoFactorStep challenge={CHALLENGE} onVerified={onVerified} onBack={() => {}} />);

    await act(async () => typeCode("168091"));

    expect(verifyTwoFactor).toHaveBeenCalledOnce();
    expect(verifyTwoFactor).toHaveBeenCalledWith(1, "168091");
    expect(onVerified).toHaveBeenCalledOnce();
  });

  it("does not verify a partial code", async () => {
    render(<TwoFactorStep challenge={CHALLENGE} onVerified={() => {}} onBack={() => {}} />);
    await act(async () => typeCode("16809"));
    expect(verifyTwoFactor).not.toHaveBeenCalled();
  });

  it("tries a wrong code once, then waits for a new one", async () => {
    verifyTwoFactor.mockRejectedValue(new Error("Kód nesedí."));
    render(<TwoFactorStep challenge={CHALLENGE} onVerified={() => {}} onBack={() => {}} />);

    await act(async () => typeCode("000000"));

    expect(verifyTwoFactor).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert").textContent).toContain("Kód nesedí.");
  });

  // The boxes are disabled while the code is checked, and a disabled input
  // cannot take focus — so focusing in the catch did nothing, and on a phone
  // the keyboard closed after every wrong auto-submitted code.
  it("puts the cursor back in the first box after a wrong code", async () => {
    // Rejects after a tick, like a real request: by then React has rendered
    // the disabled boxes.
    verifyTwoFactor.mockImplementation(
      () => new Promise((_, reject) => setTimeout(() => reject(new Error("Kód nesedí.")), 0))
    );
    render(<TwoFactorStep challenge={CHALLENGE} onVerified={() => {}} onBack={() => {}} />);

    await act(async () => typeCode("000000"));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    expect(document.activeElement).toBe(screen.getByLabelText("Číslice 1"));
  });
});
