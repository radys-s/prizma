# Progress log

## Časová osa

| Datum čas | Okno/branch | Krok |
|---|---|---|
| 2026-10-27 | bidnej / bidnej/black-october | Bezpečnostní update závislostí po „Black October“ |
| 2026-11-16 | tomas/* | Dokončení Refrakt jako key-holding gateway (PR #7–#11) |
| 2026-11-18 | tomas/ux-* | UX pass: audit and five fixes (PR #13–#17) |

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

## 2026-11-18 — UX pass: audit and five fixes (PR #13–#17)

**What and why:** goal was "clearly better for a real person", measured in steps, errors and screen clarity, not features. I walked the app as a new and a returning user on hub2: my own backend on :8020, a fresh DB, and a stub OpenAI-compatible provider that can answer, return 429/401/500, or go down. Journeys: account setup, adding keys, chat, model/effort, conversation menu, rate-limited and failing providers, 360 px, dark mode. Then I fixed the top items that were small or medium.

**Changes**
- **#13 Key dialog opens on the answering keys.** "API klíče" opened on account settings. The first key card was 2,302 px down and the second answering model 4,130 px down, behind seven search-only cards. Now answering keys come first with a "1 ze 2 potřebných" count and search keys get their own optional section. The header and sidebar counters count answering keys only, as the router does; before, a search key hid the "need two" banner while chat still failed. The keyless Wikipedia card shows as always on, environment keys say so, and the stale "Credential Manager" line is gone.
- **#14 Failed answers say what to do next.** "Žádný provider neodpověděl" rendered as one run-on line and stopped there. Now it keeps one failure per line, ends with "Co s tím: …" (fix the rejected key; wait only when every failure was temporary; otherwise add another provider's key), and offers an "API klíče" button.
- **#15 Limit toasts stop claiming another model answers.** The toasts said "Odpovídá jiný model" even when none did, and covered the Send button. Now they say "Router ho přeskakuje…", sit under the top bar, hide after 12 s, and are dropped when the whole turn fails (the error bubble already lists the limits).
- **#16 Touch targets reach 44 px on phones.** Send was 34 px, effort segments 24, pickers 30, key chip 24, icon buttons 40. Under `hover: none` they are now 44 or more; the exceptions are the code-block "Kopírovat" (about 39, clipped by the block) and "Proč tenhle model?" (42). Rows that sit close together get real height, not overlapping hit areas.
- **#17 Login code submits on the sixth digit.** Removes the extra click on every e-mail/TOTP sign-in. After a wrong code the cursor returns to the first box; before, it was lost, which closed the phone keyboard.

**Numbers**
- First key card: 2,302 → 71 px from the top of the dialog. Second answering model: 4,130 → 296 px.
- From a key error to the key dialog: sidebar + click + scroll → 1 click.
- Sign-in code screen: 6 digits + click → 6 digits.
- Phone controls under 44 px: about 15 → 2 (code-block "Kopírovat" about 39, "Proč tenhle model?" 42).
- Tests: frontend 20 → 38, backend 177 → 183. Each new test was written to fail first. The one exception, the timeout-wording test from a review nit, was added together with its fix.

**Review:** a second Claude account reviewed every PR read-only. It found answering-key counters that disagreed with the router, a "wait" hint shown for errors that waiting cannot fix, countdown toasts that could stay up for an hour, chip hit areas overlapping a wrapping row, focus lost after a wrong code, and a tautological test. All were fixed before merge.

**Verified on merged main** (my own instance on :8020): first key card 71 px down, "2 připojené", chip "2/10". A 429 + 401 turn gives one bubble with "Co s tím" and no toasts, and its "API klíče" button opens the dialog.

**What worked:** a stub provider with per-provider modes (`{"groq":"429"}`) made every error journey reproducible without real keys. Serving the built frontend from my own backend kept a single origin and never touched the live instance.

**Next ideas (not done)**
1. Locally, without `RESEND_API_KEY`, the sign-in code goes to the server console while the UI says it was e-mailed, so a new local user is stuck. The backend could report the delivery channel and the UI could say "kód je v okně serveru" (M).
2. On first start, the no-keys banner overlaps the tip and the search-key notice shows even with zero answering keys. Show one notice at a time (S).
3. Escape does not cancel the inline "Smazat …?" confirmation in the sidebar; it stays armed until clicked (S).
4. A failed answer is not saved, so after a reload the question stands unanswered with no error and no retry (M; check `chat.py` persistence first).
5. "Server neodpovídá … uvicorn na portu 8000" hard-codes the port even when the app is served elsewhere (S).


## 2026-11-18 — Security review: six fixes and a write-up (PR #19–#24)

**What and why:** Tomáš asked for the real weaknesses to be found and fixed before anyone else finds them: Refrakt holds every provider key he has, and the repo is public. I checked the areas he listed: public-mode sign-in, the loopback guard, the `/v1` gateway, key storage, the local model, the frontend renderer, dependencies and git history. Everything ran on hub2 with a throwaway database and ports 8030/8031, without touching the live instance or using real keys. The full report, with file:line evidence, is in `docs/security-review-2026-11.md`.

**Fixed (each with a test that fails on the old code)**
- **#19 (High)** The e-mail password worked as the access code: an account created by e-mail registration kept the password in the legacy access-code column, so `/api/auth/login` signed in without the second factor. Before the account was claimed, anyone who knew `OWNER_EMAIL` could take a fresh deployment. Also fixes `scripts/set_passcode.py`, which called a function that does not exist.
- **#20 (Medium)** `/v1/chat/completions` parsed bodies of any size before checking the token. One unauthenticated 50 MB request raised peak memory from 253 to 406 MB; after the fix it grows by 12 kB.
- **#23 (Medium)** The file tools' secret denylist missed `.env.development.local` and the other `.env.*` variants, `.envrc`, and ed25519/ecdsa SSH keys.
- **#21 (Low)** OIDC sign-in now uses the e-mail for claiming the account only when the provider marks it verified.
- **#22 (Low)** A generated `master.key` is now 0600 (it was 0664) and is never overwritten by a second process.
- **#24** litellm 1.98.0 → 1.100.4 for a high-rated advisory in the LiteLLM Proxy server, which Refrakt does not run. Nothing else in the lock changes.

**Written up, not changed:** the owner-claim lockout, `/email/resend` abuse, the `X-Forwarded-For` trust on Render, TOTP reuse, GCM without associated data, and smaller notes (see the report). Secrets scan: gitleaks found nothing in the full history, so no key needs rotating.

**Numbers**
- Backend tests: 182 → 216 (34 new, each seen failing first). CI is green on every PR.
- `npm audit --omit=dev`: 0. `pip-audit`: 1 (litellm, fixed).
- gitleaks: 94 commits from a GitHub mirror (every branch and PR ref) plus 9 commits only in hub2's clone, 0 findings.

**Review:** a second account reviewed all six PRs read-only, and a re-check covered the follow-up commits. They found real bugs in my fixes. The first #20 returned 500 for a body that is not UTF-8 and echoed broken JSON back; the re-check then found a 500 for very deep nesting and the body still echoed in schema errors. The first #22 opened the key file without `O_BINARY`, which on Windows corrupts about one new key in eight. All were fixed and tested before merge. Old 0664 key files are now tightened on read, and the litellm floor in `pyproject.toml` was raised.

**What worked:** calling the app at the ASGI level showed exactly whether the body was read before the token check. A fake OpenAI-compatible server on loopback let me check the litellm bump through the real HTTP path on both versions without any keys.

**Next:** the five Low items in the report need a decision from Tomáš. Also, `tests/test_tool_loop.py` fails when run on its own because it depends on state from other test files. That is not a security issue, but it is worth isolating.
