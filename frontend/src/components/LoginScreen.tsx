import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  ShieldCheck,
  Split,
  TriangleAlert,
  Wallet,
} from "lucide-react";

import { loginWithPasscode, setupPasscode } from "../lib/api";
import type { AuthState } from "../lib/types";
import { PrizmaMark } from "./Logo";

// Google/Apple/Microsoft buttons are deliberately not rendered yet. The whole
// OIDC flow is still wired up on the backend (`app/auth/oauth.py`) and the
// icons live in `BrandIcons.tsx` — this screen just doesn't offer them while
// the app is loopback-only. They come back with the real login forms at deploy.

const HIGHLIGHTS = [
  {
    icon: <Split size={17} />,
    title: "Jeden prompt, správný model",
    body: "Router dotaz klasifikuje a pošle ho tam, kde na něj má nejlepší model — kód jinam než text.",
  },
  {
    icon: <ShieldCheck size={17} />,
    title: "Klíče zůstávají u tebe",
    body: "Ukládají se do Windows Credential Manageru. Ne do souboru, ne do databáze, nikdy zpátky přes API.",
  },
  {
    icon: <Wallet size={17} />,
    // Deliberately no number: the registry gains entries every few weeks and a
    // hard count here goes stale silently. The live figure is the key chip in
    // the topbar, which counts unique `credential_id` values for real.
    title: "Víc providerů, nula faktur",
    body: "Vyčerpaný limit nebo zrušený model router tiše obejde a zkusí další v pořadí.",
  },
];

/** Czech counts three ways: 1 znak, 2–4 znaky, 5+ znaků. */
function znaky(count: number): string {
  if (count === 1) return "znak";
  return count >= 2 && count <= 4 ? "znaky" : "znaků";
}

/** Reads `?auth_error=…` left behind by a failed OAuth round trip, then wipes
 *  it from the address bar so a refresh doesn't resurrect the message. */
function consumeOAuthError(): string {
  const params = new URLSearchParams(window.location.search);
  const message = params.get("auth_error");
  if (!message) return "";
  params.delete("auth_error");
  const query = params.toString();
  window.history.replaceState({}, "", window.location.pathname + (query ? `?${query}` : ""));
  return message === "jiny_ucet"
    ? "Tenhle účet k appce nepatří — přihlas se tím, kterým jsi ji zakládal."
    : message;
}

interface Props {
  state: AuthState;
  onAuthenticated: () => void;
}

export function LoginScreen({ state, onAuthenticated }: Props) {
  const isSetup = !state.has_account;

  const [passcode, setPasscode] = useState("");
  const [confirm, setConfirm] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() => consumeOAuthError());
  const [capsLock, setCapsLock] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [lockedFor, setLockedFor] = useState(state.locked_for_seconds);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Refocus after a rejected attempt. It can't happen inside the catch: the
  // input is still `disabled` while `busy` is true, and focusing a disabled
  // element is a no-op — so wait until the re-enable has rendered.
  useEffect(() => {
    if (attempt > 0 && !busy && lockedFor === 0) inputRef.current?.focus();
  }, [attempt, busy, lockedFor]);

  // Live countdown, so a locked-out user sees the wait shrinking instead of a
  // static number that looks like the app is stuck.
  useEffect(() => {
    if (lockedFor <= 0) return;
    const timer = window.setInterval(() => setLockedFor((n) => Math.max(0, n - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [lockedFor]);

  const tooShort = passcode.length > 0 && passcode.length < state.min_passcode_length;
  const mismatch = isSetup && confirm.length > 0 && confirm !== passcode;
  const canSubmit =
    !busy &&
    lockedFor === 0 &&
    passcode.length >= (isSetup ? state.min_passcode_length : 1) &&
    (!isSetup || confirm === passcode);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    try {
      if (isSetup) await setupPasscode(passcode);
      else await loginWithPasscode(passcode);
      setPasscode("");
      setConfirm("");
      onAuthenticated();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Přihlášení selhalo.";
      setError(message);
      // Bumped rather than keying off `error`: two identical failures in a row
      // produce the same string, and React would skip the refocus effect.
      setAttempt((n) => n + 1);
      // The backend puts the remaining lockout in the message; pull it out so
      // the countdown and the disabled state stay in sync with the server.
      const seconds = message.match(/za (\d+)\s*s/);
      if (seconds) setLockedFor(Number(seconds[1]));
      setPasscode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-stage">
      <div className="auth-aurora" aria-hidden="true">
        <span className="auth-blob auth-blob-a" />
        <span className="auth-blob auth-blob-b" />
        <span className="auth-blob auth-blob-c" />
      </div>
      <div className="auth-veil" aria-hidden="true" />

      <div className="auth-shell">
        <section className="auth-hero">
          <div className="auth-hero-mark">
            <PrizmaMark size={56} />
          </div>
          <h1 className="auth-hero-title">
            Jeden prompt.
            <br />
            <span className="auth-hero-accent">Ten správný model.</span>
          </h1>
          <p className="auth-hero-lede">
            Prizma je tvůj vlastní router mezi free-tier modely. Běží lokálně, klíče si drží u sebe
            a když jeden provider vypadne, ani si toho nevšimneš.
          </p>
          <ul className="auth-highlights">
            {HIGHLIGHTS.map((item) => (
              <li key={item.title}>
                <span className="auth-highlight-icon">{item.icon}</span>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <main className="auth-panel">
          <div className="auth-card">
            <header className="auth-card-head">
              <span className="auth-card-mark">
                <PrizmaMark size={30} />
              </span>
              <h2>{isSetup ? "Nastav si Prizmu" : "Vítej zpátky"}</h2>
              <p>
                {isSetup
                  ? "Zvol si přístupový kód. Odemyká tuhle appku na tomhle počítači — nikam se neposílá."
                  : "Zadej přístupový kód a jsi uvnitř."}
              </p>
            </header>

            <form className="auth-form" onSubmit={submit} noValidate>
              <label className="auth-field">
                <span className="auth-label">Přístupový kód</span>
                <div className="auth-input-wrap">
                  <KeyRound size={16} className="auth-input-icon" aria-hidden="true" />
                  <input
                    ref={inputRef}
                    type={reveal ? "text" : "password"}
                    value={passcode}
                    onChange={(e) => setPasscode(e.target.value)}
                    onKeyUp={(e) => setCapsLock(e.getModifierState?.("CapsLock") ?? false)}
                    placeholder={
                      isSetup
                        ? `Aspoň ${state.min_passcode_length} ${znaky(state.min_passcode_length)}`
                        : "•".repeat(state.min_passcode_length)
                    }
                    autoComplete={isSetup ? "new-password" : "current-password"}
                    spellCheck={false}
                    disabled={busy || lockedFor > 0}
                    aria-invalid={Boolean(error) || tooShort}
                  />
                  <button
                    type="button"
                    className="auth-reveal"
                    onClick={() => setReveal((v) => !v)}
                    aria-label={reveal ? "Skrýt kód" : "Zobrazit kód"}
                    tabIndex={-1}
                  >
                    {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </label>

              {isSetup && (
                <label className="auth-field">
                  <span className="auth-label">Kód pro kontrolu</span>
                  <div className="auth-input-wrap">
                    <KeyRound size={16} className="auth-input-icon" aria-hidden="true" />
                    <input
                      type={reveal ? "text" : "password"}
                      value={confirm}
                      onChange={(e) => setConfirm(e.target.value)}
                      placeholder="Napiš ho ještě jednou"
                      autoComplete="new-password"
                      spellCheck={false}
                      disabled={busy}
                      aria-invalid={mismatch}
                    />
                  </div>
                </label>
              )}

              {capsLock && <p className="auth-hint">Zapnutý Caps Lock.</p>}
              {tooShort && (
                <p className="auth-hint">
                  Ještě {state.min_passcode_length - passcode.length}{" "}
                  {znaky(state.min_passcode_length - passcode.length)}.
                </p>
              )}
              {mismatch && <p className="auth-hint auth-hint-bad">Kódy se neshodují.</p>}
              {error && (
                <p className="auth-error" role="alert">
                  <TriangleAlert size={15} aria-hidden="true" />
                  <span>{error}</span>
                </p>
              )}

              <button type="submit" className="auth-submit" disabled={!canSubmit}>
                {busy ? (
                  <>
                    <Loader2 size={16} className="spin" />
                    <span>Ověřuju…</span>
                  </>
                ) : lockedFor > 0 ? (
                  <span>Zkus to za {lockedFor} s</span>
                ) : (
                  <>
                    <span>{isSetup ? "Vytvořit a vstoupit" : "Vstoupit"}</span>
                    <ArrowRight size={16} />
                  </>
                )}
              </button>
            </form>

            <p className="auth-foot">
              {isSetup
                ? "Kód se ukládá jako scrypt hash. Zapomenutý kód nejde obnovit — appka o něm ví jen tolik, aby ho poznala."
                : "Po pěti špatných pokusech se přihlášení na pět minut zamkne."}
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}
