import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  AtSign,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  ShieldCheck,
  Split,
  TriangleAlert,
  Wallet,
} from "lucide-react";

import {
  loginWithEmail,
  loginWithPasscode,
  registerEmail,
  setupPasscode,
  startOAuth,
} from "../lib/api";
import type { AuthState, OAuthProviderId, TwoFactorChallenge } from "../lib/types";
import { AppleIcon, GitHubIcon, GoogleIcon, MicrosoftIcon } from "./BrandIcons";
import { PrizmaMark } from "./Logo";
import { TwoFactorStep } from "./TwoFactorStep";

const SOCIAL: Array<{ id: OAuthProviderId; label: string; icon: React.ReactNode }> = [
  { id: "google", label: "Pokračovat přes Google", icon: <GoogleIcon /> },
  { id: "github", label: "Pokračovat přes GitHub", icon: <GitHubIcon /> },
  { id: "apple", label: "Pokračovat přes Apple", icon: <AppleIcon /> },
  { id: "microsoft", label: "Pokračovat přes Microsoft", icon: <MicrosoftIcon /> },
];

const HIGHLIGHTS = [
  {
    icon: <Split size={17} />,
    title: "Jeden prompt, správný model",
    body: "Router dotaz klasifikuje a pošle ho tam, kde na něj má nejlepší model — kód jinam než text.",
  },
  {
    icon: <ShieldCheck size={17} />,
    title: "Klíče zůstávají u tebe",
    body: "Šifrují se AES-256 klíčem, který žije jen v prostředí serveru. Z API se nikdy nevrací zpátky.",
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
  if (message === "jiny_ucet") {
    return "Tenhle účet k appce nepatří — přihlas se tím, kterým jsi ji zakládal.";
  }
  if (message === "neni_vlastnik") {
    return "Tahle adresa nemá k Prizmě přístup. Přihlas se adresou vlastníka.";
  }
  return message;
}

type Mode = "social" | "email" | "passcode";

interface Props {
  state: AuthState;
  onAuthenticated: () => void;
}

export function LoginScreen({ state, onAuthenticated }: Props) {
  const isSetup = !state.has_account;
  const anySocial = SOCIAL.some((p) => state.oauth[p.id]);

  const [challenge, setChallenge] = useState<TwoFactorChallenge | null>(null);
  const [mode, setMode] = useState<Mode>(() =>
    // The default is whichever way in actually exists. On a fresh local install
    // with no OAuth configured that is the access code, which is exactly the
    // flow that has worked all along.
    state.email_enabled ? "email" : state.passcode_enabled ? "passcode" : "social",
  );
  const [error, setError] = useState(() => consumeOAuthError());
  const [lockedFor, setLockedFor] = useState(state.locked_for_seconds);

  // Live countdown, so a locked-out user sees the wait shrinking instead of a
  // static number that looks like the app is stuck.
  useEffect(() => {
    if (lockedFor <= 0) return;
    const timer = window.setInterval(() => setLockedFor((n) => Math.max(0, n - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [lockedFor]);

  /** The backend puts the remaining lockout in the message; pull it out so the
   *  countdown and the disabled state stay in sync with the server. */
  function absorb(message: string) {
    setError(message);
    const seconds = message.match(/za (\d+)\s*s/);
    if (seconds) setLockedFor(Number(seconds[1]));
  }

  if (challenge) {
    return (
      <AuthStage>
        <TwoFactorStep
          challenge={challenge}
          onVerified={onAuthenticated}
          onBack={() => setChallenge(null)}
        />
      </AuthStage>
    );
  }

  return (
    <AuthStage>
      <div className="auth-card">
        <header className="auth-card-head">
          <span className="auth-card-mark">
            <PrizmaMark size={30} />
          </span>
          <h2>{isSetup ? "Nastav si Prizmu" : "Vítej zpátky"}</h2>
          <p>
            {isSetup
              ? "Založ si účet. Prizma je jednouživatelská — tenhle účet bude jediný."
              : "Přihlas se a jsi uvnitř."}
          </p>
        </header>

        {anySocial && (
          <>
            <div className="auth-oauth">
              {SOCIAL.map((provider) => {
                const ready = state.oauth[provider.id];
                return (
                  <button
                    key={provider.id}
                    type="button"
                    className="auth-oauth-btn"
                    onClick={() => startOAuth(provider.id)}
                    disabled={!ready}
                    title={ready ? undefined : "Tenhle provider zatím není nastavený."}
                  >
                    {provider.icon}
                    <span>{provider.label}</span>
                    {!ready && <span className="auth-oauth-tag">nenastaveno</span>}
                  </button>
                );
              })}
            </div>
            {(state.email_enabled || state.passcode_enabled) && (
              <div className="auth-divider">nebo</div>
            )}
          </>
        )}

        {/* Dvě rovnocenné cesty dovnitř, obě vidět naráz. Předtím to byl odkaz
            pod formulářem: druhá možnost existovala, ale bylo ji vidět až na
            konci obrazovky, na kterou se člověk chodí podívat právě proto, že
            neví, kterou z nich má použít. */}
        {state.passcode_enabled && state.email_enabled && (
          <div className="auth-tabs" role="radiogroup" aria-label="Způsob přihlášení">
            {(
              [
                ["email", "E-mail"],
                ["passcode", "Přístupový kód"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={mode === id}
                className="auth-tab"
                tabIndex={mode === id ? 0 : -1}
                onClick={() => {
                  setMode(id);
                  setError("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {state.email_enabled && mode !== "passcode" && (
          <EmailForm
            state={state}
            isSetup={isSetup}
            error={error}
            lockedFor={lockedFor}
            onError={absorb}
            onChallenge={setChallenge}
          />
        )}

        {state.passcode_enabled && mode === "passcode" && (
          <PasscodeForm
            state={state}
            isSetup={isSetup}
            error={error}
            lockedFor={lockedFor}
            onError={absorb}
            onAuthenticated={onAuthenticated}
          />
        )}

        {!state.email_enabled && !state.passcode_enabled && !anySocial && (
          <p className="auth-error" role="alert">
            <TriangleAlert size={15} aria-hidden="true" />
            <span>Není nastavený žádný způsob přihlášení. Zkontroluj proměnné prostředí.</span>
          </p>
        )}
      </div>
    </AuthStage>
  );
}

/* --------------------------------------------------------------- email form */

interface FormProps {
  state: AuthState;
  isSetup: boolean;
  error: string;
  lockedFor: number;
  onError: (message: string) => void;
}

function EmailForm({
  state,
  isSetup,
  error,
  lockedFor,
  onError,
  onChallenge,
}: FormProps & { onChallenge: (challenge: TwoFactorChallenge) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  // Registering is the right default only when there is no account at all.
  // Afterwards this is how a second address gets linked, from inside.
  const [registering, setRegistering] = useState(isSetup);

  const short = registering && password.length > 0 && password.length < state.min_password_length;
  const canSubmit =
    !busy &&
    lockedFor === 0 &&
    email.includes("@") &&
    password.length >= (registering ? state.min_password_length : 1);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    onError("");
    try {
      const challenge = registering
        ? await registerEmail(email, password)
        : await loginWithEmail(email, password);
      setPassword("");
      onChallenge(challenge);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Přihlášení selhalo.";
      onError(
        message === "jiny_ucet"
          ? "Tenhle e-mail k appce nepatří — přihlas se tím, kterým jsi ji zakládal."
          : message,
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="auth-form" onSubmit={submit} noValidate>
        <label className="auth-field">
          <span className="auth-label">E-mail</span>
          <div className="auth-input-wrap">
            <AtSign size={16} className="auth-input-icon" aria-hidden="true" />
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="ty@example.com"
              autoComplete="username"
              spellCheck={false}
              disabled={busy || lockedFor > 0}
              aria-invalid={Boolean(error)}
            />
          </div>
        </label>

        <label className="auth-field">
          <span className="auth-label">Heslo</span>
          <div className="auth-input-wrap">
            <Lock size={16} className="auth-input-icon" aria-hidden="true" />
            <input
              type={reveal ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={
                registering
                  ? `Aspoň ${state.min_password_length} ${znaky(state.min_password_length)}`
                  : "Tvoje heslo"
              }
              autoComplete={registering ? "new-password" : "current-password"}
              spellCheck={false}
              disabled={busy || lockedFor > 0}
              aria-invalid={Boolean(error) || short}
            />
            <button
              type="button"
              className="auth-reveal"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? "Skrýt heslo" : "Zobrazit heslo"}
              tabIndex={-1}
            >
              {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </label>

        {short && (
          <p className="auth-hint">
            Ještě {state.min_password_length - password.length}{" "}
            {znaky(state.min_password_length - password.length)}.
          </p>
        )}
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
              <span>{registering ? "Založit účet" : "Pokračovat"}</span>
              <ArrowRight size={16} />
            </>
          )}
        </button>
      </form>

      <p className="auth-foot">
        {registering
          ? "Na adresu ti přijde šestimístný kód. Bez něj se dovnitř nedostane nikdo, ani s heslem."
          : "Po hesle přijde druhý krok — kód z e-mailu nebo z ověřovací aplikace."}
      </p>

      {!isSetup && (
        <button
          type="button"
          className="auth-link auth-link-block"
          onClick={() => {
            setRegistering((v) => !v);
            onError("");
          }}
        >
          {registering ? "Už mám účet" : "Založit účet e-mailem"}
        </button>
      )}
    </>
  );
}

/* ------------------------------------------------------------ passcode form */

function PasscodeForm({
  state,
  isSetup,
  error,
  lockedFor,
  onError,
  onAuthenticated,
}: FormProps & { onAuthenticated: () => void }) {
  const [passcode, setPasscode] = useState("");
  const [confirm, setConfirm] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [attempt, setAttempt] = useState(0);
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
    onError("");
    try {
      if (isSetup) await setupPasscode(passcode);
      else await loginWithPasscode(passcode);
      setPasscode("");
      setConfirm("");
      onAuthenticated();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Přihlášení selhalo.");
      // Bumped rather than keying off `error`: two identical failures in a row
      // produce the same string, and React would skip the refocus effect.
      setAttempt((n) => n + 1);
      setPasscode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
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
          : "Špatné pokusy se počítají i napříč adresami a čekání po nich roste."}
      </p>
    </>
  );
}

/* --------------------------------------------------------------- the canvas */

/** The aurora background and the marketing half, shared by both steps so
 *  moving to the code screen doesn't visibly rebuild the page. */
function AuthStage({ children }: { children: React.ReactNode }) {
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
            Prizma je tvůj vlastní router mezi free-tier modely. Klíče si drží u sebe, a když jeden
            provider vypadne, ani si toho nevšimneš.
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

        <main className="auth-panel">{children}</main>
      </div>
    </div>
  );
}
