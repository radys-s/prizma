import {
  CircleCheck,
  CircleX,
  Cpu,
  Loader,
  Plug,
  RadioTower,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  clearLocal,
  fetchLocalStatus,
  probeLocal,
  saveLocal,
  testProviderKey,
} from "../lib/api";
import type { LocalHardware, LocalStatus } from "../lib/types";

/** Addresses the three common tools listen on, so nobody has to look them up. */
const PRESETS = [
  { url: "http://localhost:11434/v1", tool: "Ollama" },
  { url: "http://localhost:1234/v1", tool: "LM Studio" },
  { url: "http://localhost:8080/v1", tool: "llama.cpp server" },
];

type Result = { kind: "ok" | "warn" | "err"; text: string };

/** Colour of the hardware banner. `unknown` reads as a caveat, not a failure. */
const HW_KIND: Record<LocalHardware["level"], Result["kind"]> = {
  unknown: "warn",
  no: "err",
  tight: "warn",
  ok: "ok",
  good: "ok",
};

const TEST_KIND: Record<string, Result["kind"]> = {
  ok: "ok",
  warning: "warn",
  error: "err",
};

/**
 * Connecting a model that runs on this computer.
 *
 * The whole section disappears when the backend says `available: false`, which
 * it does on a public server: these controls make the backend fetch a URL the
 * user typed, which is the point on a laptop and a request-forgery primitive
 * in a datacentre. Hiding it beats showing a form whose save always 403s.
 *
 * The hardware line is deliberately a measurement and not encouragement. A 7B
 * model at 4-bit needs roughly 4 GB of RAM and answers at a few tokens per
 * second on a CPU; the same model unquantized needs 14 GB and effectively does
 * not run. Someone who is given the number can decide. Someone who is told
 * "this may be slow" downloads 14 GB and finds out the hard way.
 */
export function LocalModelSection({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [label, setLabel] = useState("");
  const [found, setFound] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<"" | "probe" | "save" | "clear" | "test">("");
  const [result, setResult] = useState<Result | null>(null);

  const load = async () => {
    try {
      const next = await fetchLocalStatus();
      setStatus(next);
      setBaseUrl(next.base_url);
      setModel(next.model);
      setLabel(next.label);
    } catch {
      // A backend that cannot answer this is a backend that cannot run the
      // feature either, so staying hidden is the honest outcome.
      setStatus(null);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const run = async (kind: typeof busy, fn: () => Promise<void>) => {
    setBusy(kind);
    setResult(null);
    try {
      await fn();
    } catch (err) {
      setResult({ kind: "err", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  };

  const probe = () =>
    run("probe", async () => {
      const models = await probeLocal(baseUrl);
      setFound(models);
      if (models.length === 0) {
        setResult({
          kind: "warn",
          text: "Endpoint odpověděl, ale nehlásí žádný model. Nejdřív si nějaký stáhni.",
        });
        return;
      }
      // Only fill the field when it is empty — overwriting a name typed on
      // purpose would be the tool arguing with the person using it.
      if (!model.trim()) setModel(models[0]);
      setResult({ kind: "ok", text: `Endpoint běží a nabízí ${models.length} model(ů).` });
    });

  const save = () =>
    run("save", async () => {
      await saveLocal(baseUrl.trim(), model.trim(), label.trim());
      await load();
      await onChanged();
      // "Uloženo" proves only that a row was written. The completion below is
      // what proves the model answers.
      const outcome = await testProviderKey("local");
      setResult({ kind: TEST_KIND[outcome.level] ?? "err", text: outcome.detail });
    });

  const test = () =>
    run("test", async () => {
      const outcome = await testProviderKey("local");
      setResult({ kind: TEST_KIND[outcome.level] ?? "err", text: outcome.detail });
    });

  const disconnect = () =>
    run("clear", async () => {
      await clearLocal();
      setFound(null);
      await load();
      await onChanged();
      setResult({ kind: "ok", text: "Odpojeno. Router se vrátil k cloudovým providerům." });
    });

  if (!status || !status.available) return null;

  const hw = status.hardware;
  const canSave = baseUrl.trim().length > 0 && model.trim().length > 0 && busy === "";

  return (
    <section className="settings-section">
      <h3>Vlastní model na tomhle počítači</h3>

      <div className={`provider-result ${HW_KIND[hw.level]}`}>
        {HW_KIND[hw.level] === "ok" ? <Cpu size={14} /> : <TriangleAlert size={14} />}
        <span>
          {hw.ram_gb !== null
            ? `${hw.ram_gb} GB RAM, ${hw.cpu_cores} jader. `
            : "Velikost paměti se nepodařilo změřit. "}
          {hw.verdict}
          {hw.ceiling ? (
            <>
              {" "}
              Realistický strop: <strong>{hw.ceiling}</strong>.
            </>
          ) : null}{" "}
          {hw.gpu_note}
        </span>
      </div>

      <div className="setting-row column">
        <div className="setting-head">
          <RadioTower size={16} className="setting-icon" />
          <div className="setting-text">
            <p className="setting-label">Adresa endpointu</p>
            <p className="setting-help">
              Prizma umí mluvit s čímkoli, co má OpenAI-kompatibilní API — <strong>Ollama</strong>,{" "}
              <strong>LM Studio</strong>, <strong>llama.cpp server</strong> nebo vLLM. Model si
              spusť v jednom z nich; holý PyTorch skript zavolat nejde, musel by nejdřív dostat
              stejné <code>/v1/chat/completions</code>. Konverzace pak neopustí počítač a nestojí
              žádné kredity — zaplatíš rychlostí.
            </p>
          </div>
        </div>

        <div className="key-input">
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && baseUrl.trim()) probe();
              if (e.key === "Escape") e.stopPropagation();
            }}
            placeholder="http://localhost:11434/v1"
            autoComplete="off"
            spellCheck={false}
            aria-label="Adresa lokálního endpointu"
          />
        </div>

        <div className="provider-tags">
          {PRESETS.map((preset) => (
            <button
              key={preset.url}
              type="button"
              className="chip chip-btn"
              onClick={() => setBaseUrl(preset.url)}
              title={preset.url}
            >
              {preset.tool}
            </button>
          ))}
        </div>
      </div>

      <div className="setting-row column">
        <div className="setting-head">
          <Plug size={16} className="setting-icon" />
          <div className="setting-text">
            <p className="setting-label">Model</p>
            <p className="setting-help">
              Přesný název tak, jak ho hlásí ten nástroj (např. <code>llama3.2:3b</code>). Tlačítko
              „Zjistit modely“ se ho zeptá a název doplní, ať ho nemusíš hádat.
            </p>
          </div>
        </div>

        <div className="key-input">
          <input
            type="text"
            value={model}
            list="local-models"
            onChange={(e) => setModel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSave) save();
              if (e.key === "Escape") e.stopPropagation();
            }}
            placeholder="llama3.2:3b"
            autoComplete="off"
            spellCheck={false}
            aria-label="Název lokálního modelu"
          />
        </div>

        {found && found.length > 0 && (
          <>
            <datalist id="local-models">
              {found.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <div className="provider-tags">
              {found.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={`chip chip-btn${name === model ? " ok" : ""}`}
                  onClick={() => setModel(name)}
                >
                  {name}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="setting-row column">
        <div className="setting-head">
          <div className="setting-text">
            <p className="setting-label">Jak se má jmenovat v seznamu</p>
            <p className="setting-help">
              Nepovinné. Prázdné pole znamená „Lokálně: {model || "název modelu"}“.
            </p>
          </div>
        </div>

        <div className="key-input">
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") e.stopPropagation();
            }}
            placeholder="Můj notebook"
            maxLength={80}
            autoComplete="off"
            aria-label="Vlastní název lokálního modelu"
          />
        </div>
      </div>

      <div className="key-row">
        <button
          type="button"
          className="btn btn-outline"
          onClick={probe}
          disabled={!baseUrl.trim() || busy !== ""}
        >
          {busy === "probe" ? <Loader size={14} className="spin" /> : <RadioTower size={14} />}
          Zjistit modely
        </button>

        <button type="button" className="btn btn-primary" onClick={save} disabled={!canSave}>
          {busy === "save" ? <Loader size={14} className="spin" /> : <Plug size={14} />}
          {status.configured ? "Uložit změnu" : "Připojit"}
        </button>

        {status.configured && (
          <>
            <span className="spacer" />
            <button type="button" className="btn" onClick={test} disabled={busy !== ""}>
              {busy === "test" && <Loader size={14} className="spin" />}
              Otestovat
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={disconnect}
              disabled={busy !== ""}
            >
              {busy === "clear" && <Loader size={14} className="spin" />}
              Odpojit
            </button>
          </>
        )}
      </div>

      {result && (
        <div className={`provider-result ${result.kind}`}>
          {result.kind === "ok" && <CircleCheck size={14} />}
          {result.kind === "warn" && <TriangleAlert size={14} />}
          {result.kind === "err" && <CircleX size={14} />}
          <span>{result.text}</span>
        </div>
      )}
    </section>
  );
}
