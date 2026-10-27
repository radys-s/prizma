# Progress log

## Časová osa

| Datum čas | Okno/branch | Krok |
|---|---|---|
| 2026-10-27 | bidnej / bidnej/black-october | Bezpečnostní update závislostí po „Black October“ |

## 2026-10-27 — Bezpečnostní update závislostí (Black October)

**Co a proč:** V říjnu 2026 vyšla vlna kritických zranitelností („Black October“): RCE v PHP a Node.js,
otrávený LiteLLM 1.107.0 na PyPI, kompromitované mezilehlé CA eMudhra (řeší certifi 2026.10.19+),
RCE v Next.js. Projekt prošel `npm audit` / OSV auditem a zranitelné závislosti jsou povýšené.

**Změny:** backend: litellm vyloučena verze 1.107.0 (`!=1.107.0`, otrávený balíček na PyPI 21. 10. — Black October); lock: certifi 2026.10.23, aiohttp 3.14.4 (+yarl 1.25.1), urllib3 2.8.0, multidict 6.9.1, filelock 4.2.4. Frontend: vite 6.4.5, vitest 5, katex 0.19.1.

**Ověření:** backend 156/156 testů v čistém venv z constraints, frontend 20/20 + build, KaTeX render ověřen, npm audit 0.

**Co zůstává:** lokální Node 24.18.0 a PHP 8.5.8 na PC jsou zranitelné — aktualizovat na Node 24.22.1+ a PHP 8.5.12+.
