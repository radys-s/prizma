import { useState } from "react";
import {
  CircleCheck,
  CircleX,
  KeyRound,
  Link2,
  Loader,
  ShieldCheck,
  Smartphone,
  Unlink,
} from "lucide-react";

import {
  confirmTotp,
  disableTotp,
  setAccessCode,
  startOAuth,
  startTotpSetup,
  unlinkIdentity,
  type TotpSetup,
} from "../lib/api";
import type { AuthState, OAuthProviderId } from "../lib/types";
import { AppleIcon, GitHubIcon, GoogleIcon, MicrosoftIcon } from "./BrandIcons";

const PROVIDER_LABELS: Record<string, string> = {
  local: "Přístupový kód",
  email: "E-mail a heslo",
  google: "Google",
  github: "GitHub",
  apple: "Apple",
  microsoft: "Microsoft",
};

const PROVIDER_ICONS: Partial<Record<OAuthProviderId, React.ReactNode>> = {
  google: <GoogleIcon size={15} />,
  github: <GitHubIcon size={15} />,
  apple: <AppleIcon size={15} />,
  microsoft: <MicrosoftIcon size={15} />,
};

type Result = { kind: "ok" | "err"; text: string };

interface Props {
  auth: AuthState;
  /** Re-fetches `/api/auth/state` so the list reflects what the server now has. */
  onChanged: () => void;
}

/**
 * "Připojené účty" — the settings half of the login screen.
 *
 * Everything here needs an existing session, which is what makes it safe: this
 * is where a second way in gets added, and adding one from *outside* would be
 * the definition of an account takeover.
 */
export function AccountSection({ auth, onChanged }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [code, setCode] = useState("");
  const [totp, setTotp] = useState<TotpSetup | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);

  const linked = new Set(auth.identities.map((i) => i.provider));
  const last = auth.identities.length <= 1;

  async function run(key: string, action: () => Promise<void>) {
    setBusy(key);
    setResult(null);
    try {
      await action();
    } catch (err) {
      setResult({ kind: "err", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="settings-section">
      <h3>Připojené účty</h3>

      <div className="identity-list">
        {auth.identities.map((identity) => (
          <div key={identity.id} className="identity-row">
            <span className="identity-icon">
              {PROVIDER_ICONS[identity.provider as OAuthProviderId] ?? <KeyRound size={15} />}
            </span>
            <div className="setting-text">
              <p className="setting-label">
                {PROVIDER_LABELS[identity.provider] ?? identity.provider}
              </p>
              <p className="setting-help">{identity.email ?? "Bez adresy"}</p>
            </div>
            <button
              type="button"
              className="btn btn-outline"
              disabled={last || busy !== null}
              // Spelled out rather than left to a disabled button the user has
              // to guess about: "why can't I click this" is the whole question.
              title={last ? "Tohle je poslední způsob přihlášení — odpojit ho nejde." : undefined}
              onClick={() =>
                run(`unlink-${identity.id}`, async () => {
                  await unlinkIdentity(identity.id);
                  setResult({ kind: "ok", text: "Odpojeno." });
                  onChanged();
                })
              }
            >
              {busy === `unlink-${identity.id}` ? <Loader size={14} className="spin" /> : <Unlink size={14} />}
              <span>Odpojit</span>
            </button>
          </div>
        ))}
      </div>

      {/* Providers that are configured but not yet linked. Clicking one leaves
          the app for the provider and comes back signed in — the same round
          trip as the login screen, which is why it links instead of switching
          accounts: a session already exists. */}
      {(Object.keys(auth.oauth) as OAuthProviderId[])
        .filter((id) => auth.oauth[id] && !linked.has(id))
        .map((id) => (
          <button key={id} type="button" className="auth-oauth-btn" onClick={() => startOAuth(id)}>
            {PROVIDER_ICONS[id]}
            <span>Připojit {PROVIDER_LABELS[id]}</span>
            <Link2 size={14} style={{ marginLeft: "auto" }} />
          </button>
        ))}

      {/* The access code, set from inside. This is the only path on a deployed
          server: the account is claimed with Google or e-mail, and the code is
          added afterwards by someone already signed in. */}
      <div className="setting-row column">
        <div className="setting-head">
          <KeyRound size={16} className="setting-icon" />
          <div className="setting-text">
            <p className="setting-label">Přístupový kód</p>
            <p className="setting-help">
              Rychlý vstup na telefonu. Aspoň {auth.min_passcode_length} znaky. Nastavením se
              přepíše ten stávající.
            </p>
          </div>
        </div>
        <div className="identity-inline">
          <input
            type="password"
            className="instructions-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={`Aspoň ${auth.min_passcode_length} znaky`}
            autoComplete="new-password"
            spellCheck={false}
          />
          <button
            type="button"
            className="btn btn-outline"
            disabled={code.length < auth.min_passcode_length || busy !== null}
            onClick={() =>
              run("passcode", async () => {
                await setAccessCode(code);
                setCode("");
                setResult({ kind: "ok", text: "Kód uložen." });
                onChanged();
              })
            }
          >
            {busy === "passcode" ? <Loader size={14} className="spin" /> : <CircleCheck size={14} />}
            <span>Uložit</span>
          </button>
        </div>
      </div>

      {/* Two-factor. Optional on purpose — turning it on with no recovery codes
          written down is how people lock themselves out, so the codes are
          shown immediately and only once. */}
      <div className="setting-row column">
        <div className="setting-head">
          <Smartphone size={16} className="setting-icon" />
          <div className="setting-text">
            <p className="setting-label">Ověřovací aplikace</p>
            <p className="setting-help">
              {auth.user?.totp_confirmed
                ? `Zapnutá. Zbývá ${auth.user.recovery_codes_left} záložních kódů.`
                : "Nahradí kód z e-mailu při přihlášení. Funguje i bez připojení k internetu."}
            </p>
          </div>
          {auth.user?.totp_confirmed ? (
            <button
              type="button"
              className="btn btn-outline"
              disabled={busy !== null}
              onClick={() =>
                run("totp-off", async () => {
                  await disableTotp();
                  setTotp(null);
                  setRecovery(null);
                  setResult({ kind: "ok", text: "Vypnuto." });
                  onChanged();
                })
              }
            >
              <CircleX size={14} />
              <span>Vypnout</span>
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-outline"
              disabled={busy !== null}
              onClick={() =>
                run("totp-on", async () => {
                  setTotp(await startTotpSetup());
                  setRecovery(null);
                })
              }
            >
              {busy === "totp-on" ? <Loader size={14} className="spin" /> : <ShieldCheck size={14} />}
              <span>Nastavit</span>
            </button>
          )}
        </div>

        {totp && !auth.user?.totp_confirmed && (
          <div className="totp-setup">
            <p className="setting-help">
              Otevři odkaz na telefonu, nebo klíč opiš ručně. Pak potvrď kódem, který aplikace
              ukáže — bez toho se nic nezapne.
            </p>
            <a className="totp-link" href={totp.uri}>
              Otevřít v ověřovací aplikaci
            </a>
            {/* Selectable, not a QR image: a QR code shown on the same phone
                that would scan it is useless, and an encoder is a dependency
                whose only job is drawing a string. */}
            <code className="totp-secret">{totp.secret}</code>
            <div className="identity-inline">
              <input
                className="instructions-input"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="Šestimístný kód"
                inputMode="numeric"
                autoComplete="one-time-code"
              />
              <button
                type="button"
                className="btn btn-outline"
                disabled={totpCode.length !== 6 || busy !== null}
                onClick={() =>
                  run("totp-confirm", async () => {
                    setRecovery(await confirmTotp(totpCode));
                    setTotpCode("");
                    setTotp(null);
                    onChanged();
                  })
                }
              >
                {busy === "totp-confirm" ? <Loader size={14} className="spin" /> : <CircleCheck size={14} />}
                <span>Potvrdit</span>
              </button>
            </div>
          </div>
        )}

        {recovery && (
          <div className="recovery-codes">
            <p className="setting-help">
              <strong>Ulož si je teď.</strong> Každý funguje jednou a znovu se už nezobrazí. Bez
              nich a bez telefonu se do účtu nedostaneš.
            </p>
            <div className="recovery-grid">
              {recovery.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-outline"
              onClick={() => navigator.clipboard?.writeText(recovery.join("\n"))}
            >
              <span>Zkopírovat všechny</span>
            </button>
          </div>
        )}
      </div>

      {result && (
        <div className={`provider-result ${result.kind}`}>
          {result.kind === "ok" ? <CircleCheck size={14} /> : <CircleX size={14} />}
          <span>{result.text}</span>
        </div>
      )}
    </section>
  );
}
