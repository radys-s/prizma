# Progress log

## Časová osa

| Datum čas | Okno/branch | Krok |
|---|---|---|
| 2026-10-27 | bidnej / bidnej/black-october | Bezpečnostní update závislostí po „Black October“ |
| 2026-11-16 | tomas/* | Dokončení Refrakt jako key-holding gateway (PR #7–#11) |

## 2026-10-27 — Bezpečnostní update závislostí (Black October)

**Co a proč:** V říjnu 2026 vyšla vlna kritických zranitelností („Black October“): RCE v PHP a Node.js,
otrávený LiteLLM 1.107.0 na PyPI, kompromitované mezilehlé CA eMudhra (řeší certifi 2026.10.19+),
RCE v Next.js. Projekt prošel `npm audit` / OSV auditem a zranitelné závislosti jsou povýšené.

**Změny:** backend: litellm vyloučena verze 1.107.0 (`!=1.107.0`, otrávený balíček na PyPI 21. 10. — Black October); lock: certifi 2026.10.23, aiohttp 3.14.4 (+yarl 1.25.1), urllib3 2.8.0, multidict 6.9.1, filelock 4.2.4. Frontend: vite 6.4.5, vitest 5, katex 0.19.1.

**Ověření:** backend 156/156 testů v čistém venv z constraints, frontend 20/20 + build, KaTeX render ověřen, npm audit 0.

**Co zůstává:** lokální Node 24.18.0 a PHP 8.5.8 na PC jsou zranitelné — aktualizovat na Node 24.22.1+ a PHP 8.5.12+.

## 2026-11-16 — Dokončení Refrakt jako key-holding gateway (PR #7–#11)

**Co a proč:** Zprovoznění a stabilizace Refraktu (původně Prizma) jako key-holding vrstvy běžící pod AI Bridge Studio a T3/OpenCode na hub2 (`refrakt.service` na `127.0.0.1:8000`). Zpřístupnění OpenAI-kompatibilního `/v1/models` a `/v1/chat/completions`, oprava CI, odstranění prázdných odpovědí u reasoning modelů pod malým tokenovým rozpočtem, ochrana soukromí při nasazení na hub (export klíčů bez konverzací) a kompletní ověření funkčnosti všech registrovaných modelů.

**Změny:**
- **PR #7 (oprava CI & modely):** Odstranění předpokladu 404 pro `/docs` v `test_security.py` tak, aby test procházel se zbuilděným frontendem (SPA catch-all vrací 200, ale bez Swagger UI) i bez něj. Zprovoznění CI pipeline.
- **PR #8 (reasoning modely v gateway):** Oprava prázdných odpovědí při nízkém `max_tokens` (např. 10): modely s reasoningem (`gpt-oss-120b`, Gemini Flash apod.) spotřebovávaly limit na myšlení a odpověď byla prázdná. `max_tokens` < 1024 se u reasoning modelů automaticky navyšuje na bezpečných 1024. Zavedena podpora pro striktní `max_completion_tokens`.
- **PR #9 (hygiena klíčů na hubu):** Nástroj `tools/hub/export_keys.py` vytváří čistou SQLite databázi obsahující pouze tabulku `providerkey` a žádné konverzace, takže na hub2 se nepřenáší osobní chatovací historie. Návod v `setup.sh` i README aktualizován pro Taildrop exportu.
- **PR #10 (audit modelů & probe):** Vytvořen nástroj `tools/probe_gateway.py` a dokument `docs/gateway-models.md` s výsledky testování všech 35 modelů. Z registru vyřazeny 4 definitivně stažené modely (404/410 z upstream katalogů: `cerebras/gemma-4-31b`, `nvidia_nim/moonshotai/kimi-k2.6` včetně `nvidia_kimi`, `nvidia_nim/nvidia/nemotron-3-super-120b-a12b`, `llama-3.1-nemotron-ultra-253b-v1`). Zaregistrován dosud nerozpoznaný reasoning model `zai/glm-4.5-flash`.
- **PR #11 (dokumentace gateway):** Přepsána sekce „Gateway pro další nástroje“ v `README.md` (formát požadavků, sémantika `max_tokens` vs `max_completion_tokens`, formát chyb, timeouty na straně klienta, odkaz na `docs/gateway-models.md` a bezpečný přenos klíčů na hub).

**Čísla:**
- **35 modelů** otestováno: 14 plně funkčních, 1 vyřešen (glm-4.5-flash), 4 stažené modely vyřazeny z registru, 14 modelů s dočasnými chybami (rate limity, neověřené karty nebo timeouty).
- **176 backendových testů** prochází bez chyb (`pytest`).
- **0 otevřených PR** — všechny změny postupně sloučeny do `main`.

**Co zbývá / další krok:**
- Na účtu OpenRouter upravit nastavení soukromí (`openrouter.ai/settings/privacy`), aby se odemkly zbývající bezplatné Nemotron modely.
- Prozkoumat chování GitHub tokenu u GitHub Models (vrací 500).
- V případě potřeby přidat platební kartu k Cerebras pro zprovoznění jejich `gpt-oss-120b`.

