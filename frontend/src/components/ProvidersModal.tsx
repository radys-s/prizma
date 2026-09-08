import {
  BookOpenText,
  CircleCheck,
  CircleX,
  Eye,
  EyeOff,
  ExternalLink,
  KeyRound,
  Loader,
  MessageSquareQuote,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  deleteProviderKey,
  openSignupPage,
  saveProviderKey,
  saveSettings,
  testProviderKey,
} from "../lib/api";
import type { AuthState, Provider } from "../lib/types";
import { AccountSection } from "./AccountSection";
import { LocalModelSection } from "./LocalModelSection";
import { ProviderMark } from "./ProviderMark";

const CAPABILITY_LABELS: Record<string, string> = {
  fast: "rychlé",
  general: "obecné",
  code: "kód",
  creative: "kreativní",
  long_context: "dlouhý kontext",
};

/** Providers that share one account key are shown as a single card. */
interface KeyGroup {
  keyId: string;
  /** The provider whose id the endpoints are called with. */
  primary: Provider;
  name: string;
  models: string[];
  capabilities: string[];
  notes: string[];
  requiresCard: boolean;
  configured: boolean;
}

function groupByKey(providers: Provider[]): KeyGroup[] {
  const groups = new Map<string, KeyGroup>();

  for (const p of providers) {
    const existing = groups.get(p.key_id);
    if (!existing) {
      groups.set(p.key_id, {
        keyId: p.key_id,
        primary: p,
        // "Mistral (Codestral)" and "Mistral (Small)" collapse to "Mistral".
        name: p.display_name,
        models: [p.model],
        capabilities: [...p.capabilities],
        notes: p.notes ? [p.notes] : [],
        requiresCard: p.requires_card,
        configured: p.configured,
      });
      continue;
    }
    existing.name = existing.name.split(" (")[0];
    existing.models.push(p.model);
    for (const c of p.capabilities) {
      if (!existing.capabilities.includes(c)) existing.capabilities.push(c);
    }
    if (p.notes && !existing.notes.includes(p.notes)) existing.notes.push(p.notes);
    existing.requiresCard ||= p.requires_card;
    existing.configured ||= p.configured;
  }

  return [...groups.values()];
}

type Result = { kind: "ok" | "err" | "warn"; text: string };

/** The backend's verdict is about the *key*; "warning" still means it works. */
const RESULT_KIND: Record<string, Result["kind"]> = {
  ok: "ok",
  warning: "warn",
  error: "err",
};

interface Props {
  providers: Provider[];
  /** Whether deep research may fetch whole pages through Jina Reader. */
  readPages: boolean;
  onReadPagesChange: (on: boolean) => void;
  /** Standing system prompt, prepended to every answer. Empty = none. */
  customInstructions: string;
  customInstructionsMax: number;
  onCustomInstructionsChange: (value: string) => void;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
  /** Null while the auth state is still loading; the account section waits. */
  auth: AuthState | null;
  /** Re-fetches `/api/auth/state` after a link, unlink or code change. */
  onAuthChanged: () => void;
}

export function ProvidersModal({
  providers,
  readPages,
  onReadPagesChange,
  customInstructions,
  customInstructionsMax,
  onCustomInstructionsChange,
  onClose,
  onChanged,
  auth,
  onAuthChanged,
}: Props) {
  const groups = useMemo(() => groupByKey(providers), [providers]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, Result>>({});
  // Only set while re-entering a key for an already-configured provider.
  const [editing, setEditing] = useState<Record<string, boolean>>({});
  const dialogRef = useRef<HTMLDivElement>(null);
  const [readError, setReadError] = useState<string | null>(null);
  // Typed locally and pushed on blur. Saving per keystroke would mean a write
  // to the settings table for every character of a 2 000-character prompt.
  const [instructions, setInstructions] = useState(customInstructions);
  const [instructionsState, setInstructionsState] = useState<Result | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const setResult = (keyId: string, result: Result | null) =>
    setResults((prev) => {
      const next = { ...prev };
      if (result) next[keyId] = result;
      else delete next[keyId];
      return next;
    });

  const runBusy = async (keyId: string, label: string, fn: () => Promise<void>) => {
    setBusy((prev) => ({ ...prev, [keyId]: label }));
    try {
      await fn();
    } catch (err) {
      setResult(keyId, { kind: "err", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy((prev) => {
        const next = { ...prev };
        delete next[keyId];
        return next;
      });
    }
  };

  const setEdit = (keyId: string, on: boolean) =>
    setEditing((prev) => ({ ...prev, [keyId]: on }));

  const save = (group: KeyGroup) =>
    runBusy(group.keyId, "save", async () => {
      const key = (drafts[group.keyId] ?? "").trim();
      if (!key) return;
      await saveProviderKey(group.primary.id, key);
      setDrafts((prev) => ({ ...prev, [group.keyId]: "" })); // don't keep it in memory
      setRevealed((prev) => ({ ...prev, [group.keyId]: false }));
      setEdit(group.keyId, false); // collapse the field — the key is in now
      await onChanged();
      // "Saved" proves nothing — only a real call does.
      const test = await testProviderKey(group.primary.id);
      setResult(group.keyId, { kind: RESULT_KIND[test.level] ?? "err", text: test.detail });
    });

  const test = (group: KeyGroup) =>
    runBusy(group.keyId, "test", async () => {
      const result = await testProviderKey(group.primary.id);
      setResult(group.keyId, { kind: RESULT_KIND[result.level] ?? "err", text: result.detail });
    });

  const remove = (group: KeyGroup) =>
    runBusy(group.keyId, "delete", async () => {
      await deleteProviderKey(group.primary.id);
      setResult(group.keyId, null);
      setEdit(group.keyId, false); // configured flips false, so the field returns
      await onChanged();
    });

  const saveInstructions = async () => {
    const next = instructions.slice(0, customInstructionsMax);
    if (next === customInstructions) return;
    setInstructionsState(null);
    try {
      const saved = await saveSettings({ custom_instructions: next });
      onCustomInstructionsChange(saved.custom_instructions);
      setInstructions(saved.custom_instructions);
      setInstructionsState({
        kind: "ok",
        text: saved.custom_instructions
          ? "Uloženo. Platí od další odpovědi."
          : "Instrukce smazané.",
      });
    } catch (err) {
      setInstructionsState({
        kind: "err",
        text: err instanceof Error ? err.message : String(err),
      });
    }
  };

  /**
   * Optimistic, then reconciled: the switch has to feel instant, but the
   * server's answer is the one that decides what actually leaves the machine,
   * so a failed save flips it straight back and says why.
   */
  const toggleReadPages = async () => {
    const next = !readPages;
    onReadPagesChange(next);
    setReadError(null);
    try {
      const saved = await saveSettings({ read_pages: next });
      onReadPagesChange(saved.read_pages);
    } catch (err) {
      onReadPagesChange(!next);
      setReadError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="providers-title"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <div style={{ flex: 1 }}>
            <h2 id="providers-title">Nastavení</h2>
            <p>
              Klíče se šifrují AES-256 a hlavní klíč žije jen v prostředí serveru. Appka je nikdy
              neposílá zpátky do prohlížeče — ani sobě, ani tobě.
            </p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Zavřít">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body">
          {auth && <AccountSection auth={auth} onChanged={onAuthChanged} />}

          <section className="settings-section">
            <h3>Web a rešerše</h3>

            <div className="setting-row">
              <BookOpenText size={16} className="setting-icon" />
              <div className="setting-text">
                <p className="setting-label">Číst obsah stránek (deep research)</p>
                <p className="setting-help">
                  V režimu deep si Prizma nejlepší nalezené stránky stáhne celé přes službu{" "}
                  <a href="https://jina.ai/reader/" target="_blank" rel="noopener noreferrer">
                    Jina Reader
                  </a>{" "}
                  (<code>r.jina.ai</code>) místo toho, aby skládala odpověď z dvouvětných úryvků.
                  Služba nepotřebuje klíč, ale vidí, které adresy Prizma čte. Vypnuté = deep
                  režim se vrátí k úryvkům, nic jiného se nezmění.
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={readPages}
                className="switch"
                onClick={toggleReadPages}
                aria-label="Číst obsah stránek v deep režimu"
              >
                <span className="switch-thumb" />
              </button>
            </div>

            {readError && (
              <div className="provider-result err">
                <CircleX size={14} />
                <span>{readError}</span>
              </div>
            )}
          </section>

          <section className="settings-section">
            <h3>Jak mám odpovídat</h3>

            <div className="setting-row column">
              <div className="setting-head">
                <MessageSquareQuote size={16} className="setting-icon" />
                <div className="setting-text">
                  <p className="setting-label">Vlastní instrukce</p>
                  <p className="setting-help">
                    Připojí se jako první systémová zpráva ke každé odpovědi — před plán
                    routeru i před výsledky z webu. Hodí se na jazyk, délku, formát nebo
                    kdo jsi. Prázdné pole = žádné instrukce, nic se nemění.
                  </p>
                </div>
              </div>

              <textarea
                className="instructions-input"
                value={instructions}
                maxLength={customInstructionsMax}
                onChange={(e) => {
                  setInstructions(e.target.value);
                  setInstructionsState(null);
                }}
                onBlur={saveInstructions}
                onKeyDown={(e) => {
                  // Escape belongs to the dialog everywhere else, but here it
                  // has to mean "undo what I typed" before it means "close".
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    setInstructions(customInstructions);
                    setInstructionsState(null);
                  }
                }}
                rows={4}
                spellCheck={false}
                placeholder="Např.: Odpovídej česky a stručně. Kód piš kompletní, ne po kouscích."
                aria-label="Vlastní instrukce pro odpovědi"
              />

              <p className="instructions-count">
                {instructions.length} / {customInstructionsMax}
              </p>
            </div>

            {instructionsState && (
              <div className={`provider-result ${instructionsState.kind}`}>
                {instructionsState.kind === "ok" ? (
                  <CircleCheck size={14} />
                ) : (
                  <CircleX size={14} />
                )}
                <span>{instructionsState.text}</span>
              </div>
            )}
          </section>

          <LocalModelSection onChanged={onChanged} />

          {groups.map((group) => {
            const draft = drafts[group.keyId] ?? "";
            const prefix = group.primary.key_prefix;
            const prefixMismatch = draft.length > 3 && prefix !== "" && !draft.startsWith(prefix);
            const busyLabel = busy[group.keyId];
            const result = results[group.keyId];
            // Once a key is stored there is nothing to type, so the field goes
            // away until "Změnit klíč" — or until the key is deleted.
            const showInput = !group.configured || editing[group.keyId] === true;

            return (
              <div
                key={group.keyId}
                className={`provider-card${group.configured ? " configured" : ""}`}
              >
                <div className="provider-head">
                  <ProviderMark id={group.primary.id} name={group.name} />
                  <span className="provider-name">{group.name}</span>
                  <span className="provider-dot" aria-hidden="true" />
                  <span className="provider-state">
                    {group.configured ? "Nastaveno" : "Chybí klíč"}
                  </span>
                  <span className="spacer" />
                  <span className={`chip${group.requiresCard ? " warn" : ""}`}>
                    {group.requiresCard ? "vyžaduje kartu" : "bez karty"}
                  </span>
                </div>

                <ul className="provider-models">
                  {group.models.map((model) => (
                    <li key={model} className="model-pill">
                      {model}
                    </li>
                  ))}
                </ul>

                {group.notes.length > 0 && <p className="provider-meta">{group.notes.join(" ")}</p>}

                <div className="provider-tags">
                  {group.capabilities.map((c) => (
                    <span key={c} className="chip">
                      {CAPABILITY_LABELS[c] ?? c}
                    </span>
                  ))}
                </div>

                <div className="key-row">
                  {showInput ? (
                    <>
                      <button
                        type="button"
                        className="btn btn-outline"
                        onClick={() => openSignupPage(group.primary)}
                        title={group.primary.signup_url}
                      >
                        <ExternalLink size={14} />
                        Otevřít stránku s klíčem
                      </button>

                      <div className="key-input">
                        <input
                          type={revealed[group.keyId] ? "text" : "password"}
                          value={draft}
                          autoFocus={editing[group.keyId]}
                          onChange={(e) =>
                            setDrafts((prev) => ({ ...prev, [group.keyId]: e.target.value }))
                          }
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && draft.trim()) save(group);
                            if (e.key === "Escape" && group.configured) {
                              e.stopPropagation(); // cancel the edit, don't close the dialog
                              setDrafts((prev) => ({ ...prev, [group.keyId]: "" }));
                              setEdit(group.keyId, false);
                            }
                          }}
                          placeholder={prefix ? `${prefix}…` : "Vlož API klíč"}
                          autoComplete="off"
                          spellCheck={false}
                          aria-label={`API klíč pro ${group.name}`}
                        />
                        <button
                          type="button"
                          className="icon-btn"
                          onClick={() =>
                            setRevealed((prev) => ({ ...prev, [group.keyId]: !prev[group.keyId] }))
                          }
                          aria-label={revealed[group.keyId] ? "Skrýt klíč" : "Zobrazit klíč"}
                        >
                          {revealed[group.keyId] ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>

                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={() => save(group)}
                        disabled={!draft.trim() || busyLabel !== undefined}
                      >
                        {busyLabel === "save" ? (
                          <Loader size={14} className="spin" />
                        ) : (
                          <KeyRound size={14} />
                        )}
                        Uložit
                      </button>

                      {group.configured && (
                        <button
                          type="button"
                          className="btn"
                          onClick={() => {
                            setDrafts((prev) => ({ ...prev, [group.keyId]: "" }));
                            setEdit(group.keyId, false);
                          }}
                          disabled={busyLabel !== undefined}
                        >
                          Zrušit
                        </button>
                      )}
                    </>
                  ) : (
                    <>
                      <span className="key-stored">
                        <CircleCheck size={14} />
                        Klíč uložený v Credential Manageru
                      </span>
                      <span className="spacer" />
                      <button
                        type="button"
                        className="btn"
                        onClick={() => test(group)}
                        disabled={busyLabel !== undefined}
                      >
                        {busyLabel === "test" && <Loader size={14} className="spin" />}
                        Otestovat
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => setEdit(group.keyId, true)}
                        disabled={busyLabel !== undefined}
                      >
                        Změnit klíč
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger"
                        onClick={() => remove(group)}
                        disabled={busyLabel !== undefined}
                      >
                        {busyLabel === "delete" && <Loader size={14} className="spin" />}
                        Smazat klíč
                      </button>
                    </>
                  )}
                </div>

                {prefixMismatch && (
                  <div className="provider-result warn">
                    <TriangleAlert size={14} />
                    <span>
                      Klíče tohoto providera obvykle začínají na <code>{prefix}</code>. Uložit to
                      můžeš i tak — test ukáže, jestli funguje.
                    </span>
                  </div>
                )}

                {result && (
                  <div className={`provider-result ${result.kind}`}>
                    {result.kind === "ok" && <CircleCheck size={14} />}
                    {result.kind === "warn" && <TriangleAlert size={14} />}
                    {result.kind === "err" && <CircleX size={14} />}
                    <span>{result.text}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
