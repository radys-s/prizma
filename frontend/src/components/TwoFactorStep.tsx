import { useEffect, useRef, useState, type ClipboardEvent, type FormEvent, type KeyboardEvent } from "react";
import { ArrowLeft, ArrowRight, Loader2, LifeBuoy, Mail, Smartphone, TriangleAlert } from "lucide-react";

import { resendCode, verifyTotp, verifyTwoFactor } from "../lib/api";
import type { TwoFactorChallenge } from "../lib/types";

const DIGITS = 6;
const RESEND_COOLDOWN = 30;

interface Props {
  challenge: TwoFactorChallenge;
  onVerified: () => void;
  onBack: () => void;
}

/**
 * The second half of an email sign-in. Deliberately its own screen rather than
 * a field on the login form: the password step has already succeeded by the
 * time this renders, and mixing the two invites the user to retype the password
 * when the code is what's wrong.
 *
 * Six separate inputs rather than one — it is what every authenticator flow
 * looks like, so the shape itself tells the user what to type, and a paste of
 * "123456" fills all six at once.
 */
export function TwoFactorStep({ challenge, onVerified, onBack }: Props) {
  const isApp = challenge.method === "totp";

  const [digits, setDigits] = useState<string[]>(() => Array(DIGITS).fill(""));
  const [recovery, setRecovery] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // Starts at zero for an app code: there is nothing to resend, and the whole
  // resend row is hidden. The mailed code starts on cooldown because one was
  // just sent a moment ago by the step that got us here.
  const [cooldown, setCooldown] = useState(isApp ? 0 : RESEND_COOLDOWN);

  const boxes = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    boxes.current[0]?.focus();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((n) => Math.max(0, n - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const code = digits.join("");
  const value = useRecovery ? recovery.trim() : code;
  const complete = useRecovery ? value.length >= 8 : code.length === DIGITS;

  function place(index: number, raw: string) {
    const clean = raw.replace(/\D/g, "");
    if (!clean) {
      setDigits((prev) => prev.map((d, i) => (i === index ? "" : d)));
      return;
    }
    // A paste lands in whichever box had focus, so spread it forward from there
    // rather than assuming the user pasted into the first one.
    setDigits((prev) => {
      const next = [...prev];
      for (let i = 0; i < clean.length && index + i < DIGITS; i += 1) {
        next[index + i] = clean[i];
      }
      return next;
    });
    const landed = Math.min(index + clean.length, DIGITS - 1);
    boxes.current[landed]?.focus();
  }

  function onKeyDown(index: number, event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace" && !digits[index] && index > 0) {
      // Backspace in an empty box steps back and clears the previous one, which
      // is what people expect and what a plain input would do anyway.
      event.preventDefault();
      setDigits((prev) => prev.map((d, i) => (i === index - 1 ? "" : d)));
      boxes.current[index - 1]?.focus();
    }
    if (event.key === "ArrowLeft" && index > 0) boxes.current[index - 1]?.focus();
    if (event.key === "ArrowRight" && index < DIGITS - 1) boxes.current[index + 1]?.focus();
  }

  function onPaste(index: number, event: ClipboardEvent<HTMLInputElement>) {
    event.preventDefault();
    place(index, event.clipboardData.getData("text"));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!complete || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (isApp) await verifyTotp(challenge.challenge_id, value);
      else await verifyTwoFactor(challenge.challenge_id, value);
      onVerified();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ověření selhalo.");
      setDigits(Array(DIGITS).fill(""));
      setRecovery("");
      boxes.current[0]?.focus();
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (cooldown > 0 || busy) return;
    setBusy(true);
    setError("");
    try {
      await resendCode(challenge.challenge_id);
      setNotice(`Poslal jsem nový kód na ${challenge.email}.`);
      setDigits(Array(DIGITS).fill(""));
      setCooldown(RESEND_COOLDOWN);
      boxes.current[0]?.focus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Odeslání selhalo.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-card">
      <header className="auth-card-head">
        <span className="auth-card-mark auth-card-mark-plain">
          {isApp ? <Smartphone size={22} /> : <Mail size={22} />}
        </span>
        <h2>{isApp ? "Kód z aplikace" : "Kód z e-mailu"}</h2>
        <p>
          {isApp
            ? "Otevři svoji ověřovací aplikaci a opiš šestimístný kód pro Prizmu."
            : `Poslal jsem šestimístný kód na ${challenge.email}. Platí deset minut.`}
        </p>
      </header>

      <form className="auth-form" onSubmit={submit} noValidate>
        {useRecovery ? (
          <label className="auth-field">
            <span className="auth-label">Záložní kód</span>
            <div className="auth-input-wrap">
              <LifeBuoy size={16} className="auth-input-icon" aria-hidden="true" />
              <input
                value={recovery}
                onChange={(e) => setRecovery(e.target.value.toUpperCase())}
                placeholder="XXXX-XXXX-XXXX"
                autoComplete="one-time-code"
                spellCheck={false}
                disabled={busy}
                aria-invalid={Boolean(error)}
              />
            </div>
          </label>
        ) : (
          <div className="auth-code" role="group" aria-label="Ověřovací kód">
            {digits.map((digit, index) => (
              <input
                key={index}
                ref={(el) => {
                  boxes.current[index] = el;
                }}
                className="auth-code-box"
                value={digit}
                onChange={(e) => place(index, e.target.value)}
                onKeyDown={(e) => onKeyDown(index, e)}
                onPaste={(e) => onPaste(index, e)}
                onFocus={(e) => e.target.select()}
                inputMode="numeric"
                // `one-time-code` is what makes iOS offer the code straight from
                // the Messages/Mail notification instead of making the user
                // switch apps to read it.
                autoComplete={index === 0 ? "one-time-code" : "off"}
                maxLength={DIGITS}
                disabled={busy}
                aria-label={`Číslice ${index + 1}`}
                aria-invalid={Boolean(error)}
              />
            ))}
          </div>
        )}

        {notice && <p className="auth-hint">{notice}</p>}
        {error && (
          <p className="auth-error" role="alert">
            <TriangleAlert size={15} aria-hidden="true" />
            <span>{error}</span>
          </p>
        )}

        <button type="submit" className="auth-submit" disabled={!complete || busy}>
          {busy ? (
            <>
              <Loader2 size={16} className="spin" />
              <span>Ověřuju…</span>
            </>
          ) : (
            <>
              <span>Ověřit a vstoupit</span>
              <ArrowRight size={16} />
            </>
          )}
        </button>
      </form>

      <div className="auth-code-actions">
        <button type="button" className="auth-link" onClick={onBack}>
          <ArrowLeft size={13} aria-hidden="true" />
          <span>Zpět</span>
        </button>

        {isApp ? (
          <button
            type="button"
            className="auth-link"
            onClick={() => {
              setUseRecovery((v) => !v);
              setError("");
            }}
          >
            {useRecovery ? "Mám kód z aplikace" : "Nemám telefon"}
          </button>
        ) : (
          <button type="button" className="auth-link" onClick={resend} disabled={cooldown > 0 || busy}>
            {cooldown > 0 ? `Poslat znovu za ${cooldown} s` : "Poslat kód znovu"}
          </button>
        )}
      </div>
    </div>
  );
}
