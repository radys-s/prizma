# Prizma — BYOK router nad free LLM API

Lokální single-user chat appka (rozhraní jako Claude/ChatGPT), která rozloží
každý dotaz podle typu a pošle ho na nejlepší dostupný **free** model podle
toho, jaké API klíče máš nastavené. Žádný proxy účet, žádné sdílení klíčů —
každý klíč je tvůj vlastní, uložený jen u tebe v OS credential store.

Jméno je popis chování: hranol vezme jeden paprsek a rozloží ho na spektrum.
Appka vezme jeden prompt a rozloží ho mezi modely. Značka to říká doslova —
jeden vstup zleva, hranol, pět barevných výstupů vpravo.

## Architektura

Lokálně dvě služby, na serveru jedna: FastAPI tam servíruje i postavenou SPA,
takže je jeden origin a žádný CORS.

```
lokálně:  frontend (React + Vite, :5173)  →  backend (FastAPI, :8000)
na Renderu:            jeden proces — FastAPI servíruje /api i frontend/dist
                                         │
                                    1) plánovač    →  LiteLLM  →  provider API
                                    2) vykonavatel →  LiteLLM  →  provider API
                                    3) revize (jen Max)
                                         │
                          SQLite lokálně / Neon Postgres na serveru
                          (konverzace, účet, identity, session, API klíče)
```

- **Plánovač** (`backend/app/routing/plan.py`): první model si zprávu přečte a
  vrátí JSON — *capability tag*, náročnost, cíl jednou větou a instrukce pro
  druhý model. Detaily v [Dvoufázové routování](#dvoufázové-routování).
- **Klasifikátor** (`backend/app/routing/classifier.py`): heuristika podle
  obsahu zprávy (kód / kreativní psaní / dlouhý kontext / krátký rychlý dotaz /
  obecné). Je to **fallback** — běží, když plánovač selže nebo když je zvolená
  náročnost `Light`.
- **Selector** (`backend/app/routing/selector.py`): z providerů, co mají daný
  tag, vybere prvního nakonfigurovaného a ne-v-cooldownu. Po 429 (rate limit)
  providera na minutu „uspí" a zkusí dalšího — místo spoléhání na natvrdo
  zadrátovaná čísla limitů, která se u free tierů mění bez ohlášení. Náročnost
  `hard` z plánovače pořadí přeskládá podle síly modelu, `trivial` podle rychlosti.
- **Kandidátní sloty** (`registry.py`): každý provider nemá jeden model, ale
  **seřazený seznam slugů**. Když provider model zruší (LiteLLM vrátí
  `NotFoundError`), slug se zapíše do registru mrtvých slugů s TTL a router
  jde tiše na další v pořadí. Tohle byla příčina původního
  `litellm.NotFoundError` — natvrdo zadrátovaný slug, který u providera přestal
  existovat.
- **API klíče leží v DB zašifrované AES-256-GCM**; hlavní klíč je jen v
  prostředí serveru a `PRIZMA_KEY_<ID>` v prostředí přebíjí řádek v DB. Proč se
  to změnilo oproti OS credential store a co to znamená, je v
  [Bezpečnost](#bezpečnost).

## Dvoufázové routování

Odpověď nevzniká jedním voláním. Nejdřív **plánovač** (rychlý model s JSON
módem) přečte dotaz a vrátí strukturu:

```json
{"capability": "code", "complexity": "moderate",
 "goal": "Napsat funkci v Pythonu, která iterativně vrátí n-té Fibonacciho číslo",
 "directives": "Vrať čistý kód s docstringem a ošetři záporný vstup."}
```

Podle `capability` + `complexity` se vybere **vykonavatel** a plánovačovy
`directives` mu dorazí jako *system* zpráva.

> **Tvoje zpráva se nikdy nepřepisuje.** Plánovač jen přidává kontext před ni —
> přeformulovaný dotaz, který ujede od toho, cos napsal, je horší než žádný
> plánovač. Je to natvrdo popsané v docstringu `plan.py`, ať to přežije budoucí
> úpravy.

Plánovač **nikdy neshodí odpověď**: timeout, rozbitý JSON, neznámý tag i „žádný
vhodný provider" končí heuristickým plánem z `classifier.py`. Celá fáze má
strop 18 s. Kdo smí plánovat, se neurčuje seznamem, ale dotazem
`litellm.get_supported_openai_params()` — bez JSON módu se provider plánovačem
stát nemůže (proto např. Cohere plánovat neumí, ale odpovídat ano).

### Náročnost

V liště pod textovým polem, uloženo v `localStorage`. Tabulka je v
`backend/app/routing/effort.py` a UI si ji tahá z `/api/routing/options`, takže
se nemůže rozejít.

| tier | plánovač | `reasoning_effort` | `max_tokens` | revize |
|---|---|---|---|---|
| Light | ne (heuristika) | `low` | 1024 | ne |
| Medium | krátký plán | `medium` | 2048 | ne |
| Pro | plný plán | `high` | 4096 | ne |
| Max | plný plán | `high` | 8192 | **ano** |

`reasoning_effort` se posílá jen tam, kde ho slug opravdu umí (Groq gpt-oss,
Gemini Flash, Cerebras) — filtruje se per-call přes
`get_supported_openai_params()`, ne globálním `litellm.drop_params`, který by
potichu spolkl i `response_format` u plánovače.

**Max** pouští po dokončení odpovědi druhý průchod: stejný model dostane dotaz,
plán a svůj draft a přepíše ho. Selhání revize **není chyba** — draft, který
jsi už přečetl, zůstane a v bublině se objeví jen poznámka. Povedená revize
přidá odznak `po revizi`.

### Volba modelu

Vedle náročnosti je picker modelu. **Auto** je výchozí a nejlepší volba —
projde celý řetěz podle toho, kdo se na daný úkol hodí. Ruční volba providera
ho postaví na začátek řetězu, ale **nenahradí ho**: když zvolený provider
nemůže odpovědět, odpoví někdo další a bublina to řekne
(„Cerebras nedostupný, odpověděl Groq"). Provider bez klíče je v seznamu
neaktivní a je u něj napsané proč.

### Minimálně dva klíče

Appka odmítne odpovídat, dokud nemáš **aspoň dva** nakonfigurované credential
sloty (`selector.MIN_CONFIGURED_SLOTS`). Router totiž stojí na tom, že má kam
uhnout — s jediným klíčem je z něj jen tenká vrstva nad jedním API. Počítají se
sloty, ne položky registru: oba Mistral modely sdílí jeden klíč a počítají se
jako jeden.

## První spuštění

**Backend:**
```bash
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn app.main:app --reload
```

**Frontend** (v druhém terminálu):
```bash
cd frontend
npm install
npm run dev
```

Otevři `http://localhost:5173` a zadej přístupový kód. Při úplně prvním
spuštění (prázdná DB) tě obrazovka nechá si ho zvolit.

> Frontend má `strictPort: true` — když je 5173 obsazený, Vite **spadne**
> místo tichého přeskoku na 5174. Je to schválně: backend důvěřuje jen
> Originu `http://localhost:5173`, takže tichá změna portu by zablokovala
> ukládání klíčů s 403 a vypadalo by to jako chyba appky.

### Jedním kliknutím: `aplikace.exe`

V kořeni projektu leží `aplikace.exe`. Po rozkliknutí nastartuje backend, počká,
až naběhne, a otevře prohlížeč na běžící appce. Vite se přitom nespouští —
`backend/app/spa.py` servíruje `frontend/dist` ze stejného procesu, takže API
i frontend jedou na jednom portu a není co udržovat naživu dvakrát.

Co musí být hotové (jednorázově):

- `backend\.venv` s nainstalovanými závislostmi — bez něj .exe vypíše přesně ty
  tři příkazy, které chybí, a skončí;
- `frontend\dist` — když chybí, .exe zkusí `npm install` a `npm run build` sám.

**Okno nechej otevřené: zavřením se server vypne.** Není to úklid v handleru,
protože ten se při zavření křížkem nespustí — uvicorn běží v **job objectu**
s `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, takže ho ukončí kernel, ať okno skončí
jakkoli. Osiřelý server držící starý build je horší chyba než pád: všechno
vypadá v pořádku.

Druhé rozkliknutí nespustí druhý server. První instance si port zapsala do
`%LOCALAPPDATA%\Prizma\instance.port`, druhá ho ověří a jen otevře prohlížeč.

**Rebuild** po zásahu do `tools/launcher/Launcher.cs`:

```bash
powershell -ExecutionPolicy Bypass -File tools\launcher\build.ps1
```

Kompiluje se `csc.exe`, který je součástí Windows, takže build nic nestahuje
a výsledek je jeden ~11 kB soubor bez runtime. PyInstaller tady použít nešlo —
pip na tomhle stroji padá na `RecursionError` v setuptools, a build, který jde
udělat jenom jednou, není build. Cenou je **C# 5**: žádná interpolace řetězců,
žádné expression-bodied členy, všude `string.Format`.

> Připravenost serveru se **nezjišťuje HTTP dotazem** na `/api/health`, ale TCP
> connectem. Norton na tomhle stroji spojení otevře a odpověď nepodepsané .NET
> binárce nedoručí — curl i prohlížeč přitom se stejným serverem mluví normálně.
> Ani dvacetisekundový timeout nestačil. Ze stejného důvodu se neskenuje rozsah
> portů: zavřený port Norton zahazuje místo odmítnutí, takže jedenáct sond
> znamenalo dvacet sekund černého okna, než se server vůbec začal spouštět.

> **První spuštění po přebuildu se může zaseknout.** Pozorované chování na tomhle
> stroji: hned po `build.ps1` skončil první pokus s procesem `python.exe`, který
> měl jedno vlákno ve stavu *Suspended*, nulový čas na procesoru a 2 MB paměti —
> tedy nikdy se nerozeběhl. Zavřít okno a kliknout znovu stačilo, další spuštění
> už naběhla normálně. Vypadá to na kontrolu nové binárky bezpečnostním
> softwarem; jistotu bez pohledu do jeho logu nemám, takže to sem píšu jako
> pozorování, ne jako diagnózu. Tebe to potká leda po rebuildu — na hotovém
> `aplikace.exe` se to neopakovalo.

## Přihlášení

Appka je pořád **single-user** — účet je jeden a nedá se z něj udělat víc. Co se
změnilo: cest dovnitř k tomu jednomu účtu je teď několik a všechny končí u
stejného `AppUser`. Každá je řádek v tabulce `AuthIdentity`.

| cesta | co potřebuje | kdy dává smysl |
|---|---|---|
| **Přístupový kód** | 4+ znaky | rychlý vstup na telefonu |
| **E-mail + heslo + kód z mailu** | 8+ znaků heslo, Resend klíč | hlavní cesta na serveru |
| **E-mail + heslo + ověřovací aplikace** | to samé + TOTP | když nechceš čekat na mail |
| **Google / GitHub** | zaregistrovaná OAuth appka | jeden klik, žádné heslo |

Apple a Microsoft jsou naprogramované a v UI se ukážou, jakmile je nastavíš.
U Apple to bez placeného developer účtu ($99/rok) a ověřené domény nejde —
tlačítko zůstane jako „nenastaveno".

### Dvoukrokové ověření

`POST /api/auth/email/login` ověří heslo a **session nevydá**. Vrátí
`{"stage": "2fa", "challenge_id": …}`. Cookies vydá až `/email/verify` nebo
`/totp/verify`. To je celý smysl toho rozdělení: znalost hesla sama o sobě
nikoho nepřihlásí.

Challenge má `purpose`, a ten obě cesty svazuje dohromady:

- `verify` a `login` — šestimístný kód, který odejde mailem. Platí 10 minut,
  jedno použití, pět pokusů a je spálený. V DB je jen SHA-256 hash.
- `totp` — kód se **nikdy neposílá a nikdy nekontroluje**. Řádek existuje jen
  proto, aby byl krok s ověřovací aplikací navázaný na proběhlou kontrolu
  hesla. Bez něj by `/totp/verify` byla cesta dovnitř, které stačí šest číslic
  a žádné heslo.

Každý endpoint cizí `purpose` odmítá — mailový kód neprojde TOTP routou a
naopak, a `totp` challenge nejde nechat poslat znovu.

### Ověřovací aplikace a záložní kódy

V **Nastavení → Připojené účty** je TOTP (RFC 6238, ručně napsané v
`app/auth/totp.py`, žádná závislost navíc). Ukáže se base32 klíč jako
označitelný text a `otpauth://` odkaz — žádný QR kód, protože QR na tom samém
telefonu, který by ho měl naskenovat, je k ničemu, a enkodér je závislost, jejíž
jediná práce by bylo nakreslit řetězec.

Potvrzení vrátí **8 záložních kódů, jednou a naposledy**. Každý funguje jednou.
Vypnutí TOTP je smaže.

### Zámek proti hádání

Počítadlo neúspěchů je v databázi, ne v paměti procesu, a má **dva kýble**: jeden
per IP a jeden globální. Ten globální je ten důležitý — bez něj by útočník
rotující IP adresy resetoval limit každým requestem. Backoff je exponenciální:
5 s → 30 s → 5 min → 30 min → 2 h.

Čtyřznakový kód je na veřejné URL 10 000 možností. Co ho drží obhajitelný je
právě ten globální kýbl a `PASSCODE_LOGIN_ENABLED=0`, kterým se ta cesta dá po
připojení Googlu nebo e-mailu vypnout úplně. Hesla k e-mailu mají vlastní,
vyšší práh (8 znaků).

Zapomenutý kód nejde přečíst, jde ho jen přepsat — z UI, nebo:

```bash
python scripts/set_passcode.py
```

### Kdo účet vlastní

Na veřejné URL by jinak účet zabral první, kdo ji najde. Proto `OWNER_EMAIL`:
když je nastavený, smí účet založit jen ta adresa. Pravidla jsou čtyři a platí
pro všechny cesty stejně:

- účet neexistuje → první přihlášení ho založí, ale jen když `OWNER_EMAIL` sedí
  (nebo není nastavený)
- identita sedí → přihlásí
- identita neznámá a **nikdo přihlášený** → 403 `jiny_ucet`
- identita neznámá a **jsi přihlášený** → připojí se jako další cesta dovnitř

Poslední zbývající identitu nejde odpojit — to by účet zamklo napořád.

### Nastavení OAuth

Do `backend/.env` (lokálně) nebo do proměnných prostředí na serveru:

```
PUBLIC_BASE_URL=https://prizma.onrender.com
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
```

Redirect URI musí sedět znak po znaku:
`{PUBLIC_BASE_URL}/api/auth/oauth/{google|github|microsoft|apple}/callback`.

| provider | kde | co potřebuješ |
|---|---|---|
| Google | Google Cloud Console → APIs & Services → Credentials → OAuth client ID (typ *Web application*) | client id; secret jen pokud nechceš PKCE |
| GitHub | Settings → Developer settings → OAuth Apps → New OAuth App | client id **i** secret — OAuth Apps neumí PKCE |
| Microsoft | Entra ID → App registrations → nová registrace, platforma *Web* | jen client id |
| Apple | Apple Developer → Certificates, IDs & Profiles → Services ID | **placený účet + ověřená doména**; `client_secret` je ES256 JWT |

GitHub není OIDC, ale obyčejné OAuth 2.0 — nevrací `id_token`. Identita se tahá
z `GET /user` a e-mail z `GET /user/emails`, kde se bere jen ten **primary a
zároveň verified**. Neúspěšná výměna kódu vrací HTTP 200 s `{"error": …}` v
těle, takže se kontroluje tělo, ne status.

OIDC flow používá PKCE (S256) a jednorázový `state` s TTL 10 minut. `id_token`
se tahá serverem přímo z token endpointu přes TLS, nikdy přes prohlížeč — což je
přesně případ, kdy OIDC Core §3.1.3.7 dovoluje ověřit TLS server místo podpisu
JWT. Validuje se `iss`, `aud`, `exp` a `sub`. Kdyby token někdy začal chodit
front-channelem, tahle úvaha přestává platit a ověření podpisu se stává povinným.

Apple posílá odpověď jako `form_post`, takže callback má i POST variantu.
Tělo se parsuje ručně přes `urllib.parse.parse_qs` — `Form(...)` z FastAPI by
si vyžádalo `python-multipart` navíc.

### E-mail

Odesílá se přes [Resend](https://resend.com) (free tier: 3 000 mailů/měsíc,
100/den, bez karty). Bez ověřené domény posílá z `onboarding@resend.dev` a
**jen na adresu, kterou jsi účet zakládal** — což je přesně ten jeden příjemce,
kterého tahle appka má.

Když `RESEND_API_KEY` není nastavený a `PUBLIC_MODE` je vypnutý, kód se vypíše
do konzole, takže lokální vývoj nepotřebuje mailový účet. V `PUBLIC_MODE` je
chybějící klíč tvrdá chyba — logovat živý přihlašovací kód na veřejném serveru
je únik credentialu.

## Přidání API klíčů

**Z UI (doporučeno):** v sidebaru klikni na **API klíče**. U každého providera je
tlačítko, které otevře **výchozí prohlížeč** rovnou na jeho stránce s klíči —
klíč zkopíruješ, vložíš, uložíš. Po uložení se pole s klíčem zavře a hned se
pošle **reálný testovací request**, takže víš, jestli klíč opravdu funguje, ne
jen že se uložil. Když klíč smažeš, pole se zase objeví.

Žádný z těchhle providerů nenabízí OAuth ani „connect" tlačítko, takže
plně automatické připojení není možné — deep-link + auto-test je maximum.

Mistral má jeden klíč pro oba modely (Codestral i Small) — vkládá se jednou.

**Z terminálu (offline cesta):**
```bash
python scripts/setup_keys.py
```

Klíč vkládej vždy sám, přímo do appky nebo skriptu — nikdy ho neposílej do
chatu s AI asistentem.

### Vlastní model na tomhle počítači

Nahoře v dialogu **API klíče** je sekce *Vlastní model u tebe v počítači*. Když
si pustíš Ollamu, LM Studio nebo `llama.cpp server`, vloží se sem jeho adresa
(`http://localhost:11434/v1`, `:1234/v1`, `:8080/v1` jsou presety na jedno
kliknutí), **Zjistit modely** se endpointu zeptá, co doopravdy nabízí, a
**Připojit** volbu uloží a hned na ni pošle reálný požadavek — „Uloženo" nic
nedokazuje.

Router se kvůli tomu měnit nemusel. Lokální server mluví OpenAI protokolem,
takže je z jeho pohledu jen další `api_base`: dá se připnout v pickeru, platí
pro něj cooldowny i model health, a když spadne, chain pokračuje dál. Do
seznamu karet s klíči se ale vědomě nedostane — nemá klíč, registraci ani
kartu, takže by tam stála karta, na které lže každý ovládací prvek.

Nahoře v sekci je verdikt o hardwaru, ne reklama:

| RAM | Realistický strop | Co to znamená |
|---|---|---|
| < 8 GB | ~1B | Na chat model to není. |
| 8–16 GB | ~7B v Q4 | Vejde se, na procesoru poběží pomalu. |
| 16–32 GB | ~13B v Q4 | Paměti dost; rychlost řeší grafika. |
| ≥ 32 GB | 30B+ v Q4 | Prostor i na velké modely. |

Rozhoduje **kvantizace**, ne počet parametrů: 7B v fp16 chce ~14 GB, ten samý
model v Q4 zhruba 4 GB. Proto se v celé sekci nikde nemluví o „7B" bez toho,
v čem je zkvantizovaný.

**Na veřejném serveru je celá funkce vypnutá.** `local_llm.available()` vrací
`False`, když běží `PUBLIC_MODE`, a všechny tři zapisující routy se předem ptají
`_local_or_403()`. Nechat backend stáhnout URL, kterou někdo zadal do políčka,
je na notebooku ta funkce samotná — na Renderu je to SSRF do jejich privátní
sítě.

## Bezpečnost

API klíč se ukládá **zašifrovaný AES-256-GCM** do databáze. Šifruje se
`SECRET_KEY`, který žije jen v prostředí serveru — v DB není, v souboru není, a
API klíč nikdy nevrací zpátky: `GET /api/providers` posílá pouze
`configured: true/false`.

> Do verze 3 byl klíč jen ve Windows Credential Manageru. Tohle je **vědomé
> uvolnění toho pravidla**, ne opomenutí: `keyring` nemá v Linuxovém kontejneru
> funkční backend — každé čtení tam buď spadne, nebo tiše vrátí `None`, což
> vypadá úplně stejně jako „klíč není nastavený". Bez tyhle změny by deploy
> nešel udělat vůbec.

Šifrování je autentizované, takže špatný `SECRET_KEY` **selže nahlas** místo
aby vrátil nesmysl. Ztráta klíče znamená, že se všechny uložené API klíče musí
vložit znovu — data konverzací se jí netýkají.

Kdo nechce klíč v databázi ani zašifrovaný, může ho dát rovnou do prostředí:
`PRIZMA_KEY_<CREDENTIAL_ID>` (velkými písmeny) **přebíjí** řádek v DB. Zápis
klíče, který je takhle zastíněný, vrátí 409 místo aby tiše neudělal nic.

Obrana je vrstvená; každá vrstva chytá něco, co ta předchozí neumí:

| vrstva | co blokuje |
|---|---|
| peer musí být `127.0.0.1` / `::1` — **jen když `PUBLIC_MODE=0`** | přístup z jiného stroje v síti |
| hlavička `Origin`, když je přítomná, musí být v `settings.cors_origins` | cizí web, který by ti na localhost poslal request z prohlížeče |
| **session cookie** (`ma_session`, HttpOnly, SameSite=Lax) | jiný lokální proces bez `Origin` hlavičky — tohle byla reálná díra: `PUT /key` bez session vracelo 200, teď vrací 401 |
| **double-submit CSRF token** (`ma_csrf` cookie + `x-csrf-token` hlavička) | zápis vyvolaný cizí stránkou, která na cookie dosáhne, ale na její obsah ne |
| **allowlist hostnames** (`Host` musí být loopback, jinak 421) | DNS rebinding — útočník nasměruje svou doménu na 127.0.0.1, aby prohlížeč považoval request za same-origin |
| bezpečnostní hlavičky na každé odpovědi | sniffing, framing, únik referreru; `CSP: default-src 'none'` navíc znamená, že se z API nedá nic načíst ani spustit |

Session token se v DB drží jen jako SHA-256 hash, porovnává se přes
`hmac.compare_digest`, a odhlášení ho okamžitě zneplatní.

Ověření, že se to nerozbilo — obojí musí vrátit **401**, ne 200:
```bash
curl -i -X PUT http://127.0.0.1:8000/api/providers/groq/key -H "Content-Type: application/json" -d "{\"api_key\":\"test\"}"
```
```bash
curl -i http://127.0.0.1:8000/api/providers -H "Origin: https://evil.example"
```

`POST /{id}/open-signup` nebere URL od klienta — jen `provider_id`, adresa se
čte z registry. Nejde přes něj otevřít libovolnou stránku.

Odpovědi se renderují `react-markdown` **bez** `rehype-raw` a bez
`dangerouslySetInnerHTML`. Raw HTML v odpovědi modelu se tedy zobrazí jako text;
`javascript:` a `data:` odkazy skončí s prázdným `href`. Ověřeno podvrženou
odpovědí s `<script>`, `<img onerror>`, `<svg onload>` a `<iframe>` — nic z toho
se nespustilo.

**V `PUBLIC_MODE=1` peer check odpadá** — na serveru je každý návštěvník
non-loopback a kontrola by odmítla všechny. Zbytek tabulky platí beze změny a
zpřísní se: cookies dostanou `Secure`, `ALLOWED_HOSTS` musí obsahovat skutečnou
doménu (jinak 421) a `allowed_origins()` se zúží na origin nasazené appky. Že
tohle všechno opravdu drží, ověřuje `verify_public_mode.py` (20/20): veřejná IP
projde, cizí `Origin` dostane 403, chybějící CSRF hlavička 403, neznámý `Host`
421, a cookies se vrací se `Secure`, `HttpOnly` i `SameSite=Lax`.

Peer check nikdy nebyl jediná vrstva — byl ta nejvzdálenější.

**Co to nechrání:** jiný program běžící pod tvým účtem. Ten si credential store
přečte i bez téhle appky a dokáže si přečíst i cookies, takže endpointy threat
model nerozšiřují. Plná krádež obou cookies přihlášení obejde — proti tomu je
obrana jen na úrovni OS účtu.

## Providery

Ceny/limity free tierů se mění často — ověřeno k 2026-08-29, ověř si aktuální
stav před spoléháním na konkrétní čísla:

| Provider | Model | Karta? | Free limit (orientačně) | Stránka s klíčem | Prefix |
|---|---|---|---|---|---|
| Groq | GPT-OSS 120B | Ne | 30 req/min, 8 000 tokenů/min | [console.groq.com/keys](https://console.groq.com/keys) | `gsk_` |
| Groq · Web (Compound) | `groq/compound-mini` | Ne | stejný klíč jako Groq, 250 hledání/den | — | `gsk_` |
| Google Gemini | 3.5 Flash | Ne (dokud je billing vypnutý) | velký kontext; **grounding zvlášť** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `AIza` |
| Mistral | Codestral + Small | Ne | ~1B tokenů/měsíc (evaluační tier) | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) | — |
| OpenRouter | rotující pool `:free` modelů | Ne | 20 req/min, 50–1000 req/den | [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) | `sk-or-` |
| Cerebras | Llama 3.3 70B | **Ano** (od 08/2026) | 1M tokenů/den po ověření karty | [cloud.cerebras.ai](https://cloud.cerebras.ai) | `csk-` |
| GitHub Models | GPT-4.1 / o4-mini | Ne | limit podle GitHub plánu | [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens) | `github_pat_` |
| NVIDIA · Kimi K3 | `moonshotai/kimi-k3` | Ne | ~1000 kreditů po registraci | [build.nvidia.com/settings/api-keys](https://build.nvidia.com/settings/api-keys) | `nvapi-` |
| NVIDIA · DeepSeek | `deepseek-ai/deepseek-r1` | Ne | stejný klíč jako Kimi | — | `nvapi-` |
| NVIDIA · Nemotron / Qwen | `nemotron-3-ultra` / `qwen3.5-397b` | Ne | stejný klíč jako Kimi | — | `nvapi-` |
| Z.AI | GLM 4.7 Flash | Ne | zdarma bez expirace | [z.ai/manage-apikey/apikey-list](https://z.ai/manage-apikey/apikey-list) | — |
| Cohere | Command A+ / Command R+ | Ne | 1000 volání/měsíc, nekomerčně | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) | — |
| Exa · Hledání | nativní search API | Ne | $10 kreditů **každý měsíc** | [dashboard.exa.ai/api-keys](https://dashboard.exa.ai/api-keys) | — |
| Linkup · Hledání | nativní search API | Ne | 4000 dotazů při registraci + $5/měsíc | [app.linkup.so](https://app.linkup.so) | — |
| Tavily · Hledání | nativní search API | Ne | 1000 kreditů/měsíc | [app.tavily.com](https://app.tavily.com) | `tvly-` |
| Serper · Hledání | Google SERP | Ne | 2500 dotazů jednorázově | [serper.dev](https://serper.dev) | — |
| Firecrawl · Hledání | search + scrape | Ne | 500 kreditů jednorázově | [firecrawl.dev](https://firecrawl.dev) | `fc-` |

**Tři NVIDIA řádky sdílejí jeden klíč.** Stejně jako oba Mistraly: v registru
mají společné `key_id`, takže se `nvapi-` klíč vkládá jednou a odemkne všechny
tři. Klíč ber z **build.nvidia.com/settings/api-keys** — klíč, který nabízí
stránka konkrétního modelu, platí jen pro ten jeden model.

**Kde není DeepSeek a Moonshot přímo:** DeepSeek dává jednorázový grant, který
vyprší za 30 dnů, Moonshot free tier nemá vůbec. Přes NVIDIA NIM jsou oba
trvale zdarma — proto jsou v tabulce takhle. Cloudflare Workers AI chybí,
protože potřebuje account ID *i* token, a keystore umí jeden secret na slot.
SambaNova chce kartu.

> **Neověřené slugy.** Groq, Groq Compound, Gemini, Mistral, OpenRouter a
> Cerebras mají slugy potvrzené reálným completionem. GitHub Models, obě NVIDIA
> rodiny, Z.AI a Cohere ne, protože k nim zatím není klíč. Vlož klíč a zmáčkni
> v UI **Otestovat** — co vrátí 404, patří z `registry.py` pryč, ne tam nechat
> jako dekoraci. Cohere ani Z.AI nemají JSON mód, takže nikdy nebudou
> plánovač, jen vykonavatel.

Prefix slouží jen k okamžitému odhalení překlepu — neshoda je **varování, ne
blokace**, protože providery formát klíčů občas mění.

Doporučený start: **Groq + Gemini** — oba bez karty, pokryjí „fast", „general"
i „long_context" tagy, a rovnou splní minimum dvou klíčů.

## Hledání na webu a deep research

Bez tohohle odpovídá každý model jen z vah, takže cokoli po jeho cutoffu je
odhad. Globus v liště pod textovým polem má čtyři polohy:

| Režim | Co udělá |
|---|---|
| **Auto** (výchozí) | Plánovač u každé zprávy rozhodne, jestli je potřeba web. Vrátí `needs_search`, a jen když je `true`, proběhne hledání. |
| **Web** | Vždycky jedno hledání před odpovědí. |
| **Deep research** | Rozloží dotaz na podotázky, každou vyhledá zvlášť, výsledky sloučí a odpoví z nich. |
| **Bez webu** | Nehledá nikdy, ani když si o to plánovač řekne. |

### Jak to je poskládané

**Hledání je předfáze, ne součást streamu.** Běží nestreamovaně, vrátí
strukturovaná data a ta vjedou do vykonavatele jako systémová zpráva — přesně
jako plán z `plan.py`. Není to estetické rozhodnutí: litellm zahazuje Gemini
`groundingMetadata`, když je `stream=True`
([BerriAI/litellm#10237](https://github.com/BerriAI/litellm/issues/10237)), a
Groq `executed_tools` je uprostřed streamu stejně nespolehlivý. Citace tak jsou
reálná data, ne něco vyškrábaného z proudu tokenů.

**Nálezy doplňují, nenahrazují.** Zpráva uživatele jde dál beze změny a digest
je kontext, který smí model použít, popřít i ignorovat. Výsledky hledání bývají
často špatně a model, který jim musí věřit, je horší než ten, co může říct, že
si zdroje protiřečí.

**Hledání nikdy nevyhodí výjimku.** Stejný kontrakt jako `make_plan`: mrtvý
searcher stojí poznámku, nikdy odpověď. Když nevrátí nic, odpověď se napíše z
vah a UI to napíše na rovinu.

### Kdo hledá

1. **Groq Compound** (`groq/groq/compound-mini`, pak `compound`) — hledá na
   webu serverově přes Tavily a rozhoduje se sám, kdy na to sáhnout. Není co
   drátovat, ten slug *je* ta funkce. Zdvojený prefix je správně: litellm
   ukousne první `groq/` jako providera a model se u Groqu jmenuje
   `groq/compound-mini`.
2. **Gemini grounding** jako záloha, přes `web_search_options`.
   Ověřeno 2026-08-29: na tvém současném Gemini klíči vrací grounding
   `429 RESOURCE_EXHAUSTED`, zatímco obyčejný chat na tomtéž klíči jede.
   Řetězec proto reálně stojí na Groqu a Gemini je zatím jen rotační slot.

Obojí jede na klíči, který už v appce je.

3. **Nativní search API** — Exa, Linkup, Tavily, Serper a Firecrawl. Nejdou
   přes litellm vůbec: `ProviderModel.search_backend` řekne `websearch.py`,
   který endpoint zavolat, a výsledek se převede na stejné `Source` objekty
   jako u zbytku. V řetězci stojí **před** LLM searchery, protože neplatí
   tokeny za hledání — dotaz je dotaz. Pořadí v registru je záměrné:
   opakující se měsíční kvóty (Exa, Linkup, Tavily) před jednorázovými
   granty (Serper, Firecrawl).

Žádné z nich nechce kartu.

### Kolik toho deep research reálně stihne

Free tiery měří **tokeny za minutu**, ne požadavky. Jedno Compound hledání
stojí ~4 000 tokenů a Groq free tier má strop 8 000 tokenů/min, tedy **zhruba
dvě hledání za minutu na jeden klíč**. Proto:

- počet podotázek se odvíjí od počtu připojených účtů, které umí hledat
  (`_SUBQUESTIONS_PER_SLOT = 2`) — s jedním klíčem se ptá na dvě, ne na pět,
  protože pět podotázek na jeden klíč znamená jednu odpověď a čtyři 429;
- souběh je omezený semaforem na počet účtů, aby dotazy dopadaly ve vlnách;
- každý dotaz startuje na jiném místě řetězce searcherů, takže se zátěž
  rozloží místo aby se nakupila na prvním slugu.

**Chceš pořádný deep research → připoj druhý klíč, co umí hledat.** Na jednom
Groq klíči doběhne realisticky jedna podotázka a odpověď dostane `partial`
poznámku, že zbytek se nedohledal.

### Čtení stránek (jen deep režim)

Free Perplexity skladá odpověď z dvouvětných úryvků a právě to je na něm
špatně. V deep režimu si proto Prizma tři nejlepší nalezené stránky stáhne
celé přes **[Jina Reader](https://jina.ai/reader/)** (`https://r.jina.ai/<url>`),
který vrátí čistý markdown. Text se přilepí k digestu pod hlavičku toho
samého `[n]` zdroje, takže číslování citací zůstává nedotčené. Přidá to zhruba
10–15 s, proto má deep rozpočet 120 s místo 90.

> **Third-party disclosure.** Jina Reader nepotřebuje klíč, ale **vidí, které
> adresy Prizma čte**. Proto je to viditelný přepínač, ne skrytá závislost:
> *API klíče → Web a rešerše → Číst obsah stránek*. Výchozí je zapnuto,
> vypnuté znamená, že se deep režim vrátí k úryvkům — nic jiného se nezmění.
> Volba se ukládá na serveru (`app/settings_store.py`), ne v prohlížeči: rozhoduje
> o tom, co opustí tenhle počítač, takže ji nemá držet `localStorage`.

### Co se vědomě nepřipojilo

- **Brave Search API** — free tier zrušený 02/2026, metered plan chce kartu.
- **Veřejné SearXNG instance** — většina má vypnutý JSON formát a zbytek
  rate-limituje po IP po pár dotazech. Není to zdarma v praxi, jen na papíře.
- **DuckDuckGo scraping (`ddgs`)** — nemá oficiální API, blokuje po IP a je to
  proti jejich podmínkám. Providera, který porušuje ToS, tam nedávám.
- **Perplexity Sonar, placené tiery Serperu, Jina `s.jina.ai`** — placené, nebo
  (u Jiny) postavené na Brave, tedy stejně kartou omezené výše v řetězci.

### Zdroje

Do čtyř zdrojů se pod odpovědí vykreslí rovnou jako proužek kartiček — to je
běžný případ a schovat ho za rozkliknutí nemělo smysl. Víc jich sbalí zpátky do
**Zdroje**. Číslování odpovídá `[n]` citacím v textu, takže se pořadí nikdy
nepřerovnává; kliknutí na `[2]` odroluje na zdroj 2 a probliknuť ho. Ukládá se do
sloupce `sources` (JSON text) u zprávy, takže to přežije reload.

Favicon se bere z toho, co vrátí search API (Exa a Linkup ho posílají). **Žádná
favicon CDN se nevolá** — to by třetí straně řeklo každou doménu, ze které je
odpověď poskládaná. Kdo favicon nepošle, dostane monogram z hostname.

## Co appka umí

- **Volba modelu, náročnosti a webu** přímo v liště pod textovým polem, uložená
  mezi spuštěními. Podrobně v [Dvoufázové routování](#dvoufázové-routování) a
  [Hledání na webu](#hledání-na-webu-a-deep-research).
- **Odznak u odpovědi** říká, co se stalo: model, který odpověděl, plán
  (`kód · složité`, s cílem v tooltipu), poznámka o náhradě providera a `po
  revizi` u Max, a modrý `web` / `deep research`, když odpověď stojí na
  hledání. Když plánovač neběžel, žádný odznak plánu není — verdikt regexu
  není tvrzení, co by appka uměla obhájit. Když hledání spadne, chip zmizí a
  místo něj je poznámka: appka nikdy netvrdí hledání, které neproběhlo.
- **Zdroje** pod odpovědí, číslované shodně s `[n]` citacemi v textu — a ty
  citace jsou klikací. Přežijí reload.
- **Postup během hledání** jako žebříček *Plánuji → Hledám N podotázek → Čtu N
  stránek → Píšu odpověď*. Každý krok stojí na SSE události, která opravdu
  přišla, takže žebříček nikdy netvrdí fázi, která neběží.
- **Cena odpovědi** (`2,4 s · 1 180 tokenů`) v řádku akcí. Tokeny jen tam, kde je
  provider hlásí — vymyšlené číslo by z routeru dělalo vševěda.
- **Markdown a zvýrazněný kód** v odpovědích, s tlačítkem Kopírovat u každého
  bloku. Uživatelské zprávy zůstávají plain text.
- **Stop** uprostřed generování — rozepsaná odpověď se **uloží** do historie,
  přerušení se nebere jako chyba.
- **Přegenerovat** poslední odpověď. Dává smysl hlavně proto, že router může
  sáhnout po jiném modelu.
- **Upravit vlastní zprávu** a pustit vlákno znovu od ní. Všechno pod ní se
  zahodí — na serveru i na obrazovce — a UI to řekne předem, protože zpět to
  vzít nejde.
- **Přejmenování, hledání a mazání s potvrzením** v sidebaru; konverzace jsou
  seskupené podle data. Hledání ignoruje diakritiku i velikost písmen.
- **Tip na prázdné obrazovce** — jeden náhodný, se šipkou na další. Každý
  jmenuje ovládací prvek, který jde najít, a říká i cenu, ne jen přínos („Max
  přidá plán a revizi, ale trvá zhruba dvakrát tak dlouho"). Seznam je
  v `frontend/src/lib/tips.ts` a to pravidlo je jeho vstupenka: co neukazuje na
  konkrétní ovládání nebo slibuje výhodu zadarmo, tam nepatří.
- Pod 900 px se sidebar mění na výsuvný drawer se scrimem, přihlašovací
  obrazovka schová hero sloupec a zůstane jen karta.

### Světlý, tmavý a podle systému

Přepínač je v patčce sidebaru (a v paletě). Celá paleta barev je jeden blok
`:root` v `styles/tokens.css`, kde každý token je `light-dark(světlá, tmavá)` —
ne dva zdvojené bloky, které se časem rozejdou. Režim *podle systému* proto
neřeší žádný JavaScript: CSS čte `color-scheme` a sleduje OS živě.

Vynucený režim razítkuje inline skript v `<head>` ještě před prvním vykreslením,
takže ve světlém režimu neproblikuťne tmavé pozadí. Volba se drží v
`localStorage` pod `prizma.theme`; když je úložiště zablokované, spadne se na
systém a nic se nerozbije.

### Klávesnice

| Zkratka | Co udělá |
|---|---|
| `Ctrl/⌘ + K` | Paleta příkazů — konverzace, model, náročnost, web, motiv, klíče |
| `Ctrl/⌘ + Shift + O` | Nová konverzace |
| `Esc` | Zastaví generání (rozepsaná odpověď se uloží) |
| `↑` v prázdném poli | Otevře úpravu poslední vlastní zprávy |

`Esc` a `↑` zkratku **odmítnou**, když nemá co dělat — nic neběží, pole není
prázdné — a klávesa se chová normálně. Zkratka, která ukradne šipku uprostřed
psaní, je horší než žádná.

## Deploy

Cíl: appka dostupná z telefonu, aniž by musel běžet počítač. Sestava je
**Render Free** (web service) + **Neon Free** (Postgres) + **Resend Free**
(maily). Žádná karta, nic z toho po měsíci nevyprší.

### Proč zrovna tohle

| kandidát | verdikt |
|---|---|
| **Render Free** | 750 instance-hodin/měsíc, HTTPS na `*.onrender.com`, Docker, dlouhoběžící proces (SSE funguje). Cena: usne po **15 min** nečinnosti a probouzí se ~minutu, a **nemá persistentní disk**. |
| **Neon Free** | 0,5 GB, 100 CU-hodin/měsíc, **trvalé, ne trial, bez karty**. Uspí se po 5 min, probouzí se pod sekundu. |
| **Resend Free** | 3 000 mailů/měsíc, 100/den, bez karty. |
| Render Free Postgres | ❌ vyprší 30 dní po vytvoření. Neon ne. |
| Fly.io | ❌ free tier pro nové účty zrušený, vyžaduje kartu. |
| Koyeb | ❌ free Starter zavřený pro nové registrace. |
| Hugging Face Spaces | ❌ Docker i Gradio Spaces vyžadují PRO; zadarmo jsou jen statické. |
| Oracle Cloud Always Free | ⏸ opravdu vždy zapnuté (2 OCPU / 12 GB ARM), ale karta, ruční správa a Oracle si nečinné instance bere zpět. Cesta na později. |
| Google Cloud Run | ⏸ 2M requestů/měsíc zdarma a rychlý cold start, ale karta a riziko přečerpání. Cesta na později. |
| Vercel / Netlify / Cloudflare Pages | ❌ jen SPA; SSE backend se do limitů funkcí nevejde a rozdělení originů by vrátilo CORS. |

### Jak to nasadit

1. **Neon** — založ projekt, zkopíruj connection string a přepiš schéma na
   `postgresql+psycopg://…?sslmode=require`.

2. **`SECRET_KEY`** — vygeneruj si vlastní a nikam ho neposílej:

   ```bash
   python scripts/gen_secret.py
   ```

3. **Render** — nový Blueprint z tohohle repa. `render.yaml` popisuje službu
   celou; doplní se jen hodnoty označené `sync: false`:
   `SECRET_KEY`, `DATABASE_URL`, `OWNER_EMAIL`, `RESEND_API_KEY`,
   `GOOGLE_CLIENT_*`, `GITHUB_CLIENT_*`, `CONTACT_URL`.

   Je-li doména jiná než `prizma.onrender.com`, přepiš `ALLOWED_HOSTS`,
   `PUBLIC_BASE_URL` a `FRONTEND_URL`.

4. **Data z lokálu** (volitelné, obojí čte zdroj read-only):

   ```bash
   python scripts/migrate_sqlite_to_postgres.py
   ```
   ```bash
   python scripts/migrate_keys_to_db.py
   ```

   Druhý přečte klíče z Windows Credential Manageru a zapíše je zašifrované
   tam, kam ukazuje `DATABASE_URL` — klíč se přitom nikde nevypíše ani nezaloguje.

5. **OAuth** — u Googlu a GitHubu zaregistruj redirect URI
   `https://prizma.onrender.com/api/auth/oauth/<provider>/callback`.

6. **Resend** — vygeneruj klíč a vlož ho jako `RESEND_API_KEY`.

Server v `PUBLIC_MODE=1` **odmítne nastartovat**, když chybí `SECRET_KEY` nebo
`ALLOWED_HOSTS`, nebo když `PUBLIC_BASE_URL` není `https://`. Je to schválně:
obojí selhává jinak tiše a pozdě — bez `SECRET_KEY` by appka vypadala v pořádku
až do prvního chatu, bez `ALLOWED_HOSTS` by každý request skončil na 421.

### Co je na tom nepříjemné

**Cold start.** Render službu po 15 minutách nečinnosti uspí a probuzení trvá
zhruba minutu. 750 hodin zdarma proti ~730 hodinám v měsíci znamená, že jedna
trvale běžící free služba se technicky vejde, takže by ji desetiminutový ping na
`/api/health` udržel vzhůru. Render tohle výslovně nedoporučuje, takže to tady
zůstává **popsané jako možnost i s tím rizikem**, ne zapnuté.

**Neon se uspává po 5 minutách.** Proto `pool_pre_ping=True` a
`pool_recycle=300` — connection do uspané compute je mrtvá hned při převzetí z
poolu, a bez pre-pingu by se to projevilo jako náhodná chyba prvního requestu.

**Postgres je pro hledání v konverzacích lepší než SQLite.** Jeho `ILIKE` skládá
i Unicode, takže `Čas` konečně najde `čas` — SQLite skládá jen ASCII.

**Jeden origin.** FastAPI servíruje i postavené `frontend/dist`, takže se
nasazuje jedna služba a CORS z deploye mizí úplně. `/api/*` má striktní
`default-src 'none'` CSP, dokument tu svou z `<meta>` — kdyby dostal tu API,
zobrazí se prázdná stránka.

## Na telefonu (PWA)

Není to appka z obchodu, ale nainstalovaná zástupce na plochu, která se spouští
bez adresního řádku. V prohlížeči na telefonu: *Přidat na plochu*.

- `manifest.webmanifest`, ikony 192/512 + maskable 512 + `apple-touch-icon` 180.
  Generuje je `npm run icons` z `frontend/scripts/make-icons.mjs` (`sharp`) —
  výstup je commitnutý, aby build nezávisel na tom, že `sharp` půjde nainstalovat.
- Service worker (`frontend/public/sw.js`, ručně psaný, bez `vite-plugin-pwa`).
  Hashované `/assets/*` cache-first, dokument network-first s offline fallbackem.
  **`/api/*` neodchytává vůbec** — zastaralý seznam konverzací by mátl, ale
  přehraný POST by utratil kvótu nebo poslal zprávu dvakrát.
- Nová verze se **neinstaluje potichu**. Worker čeká a appka nabídne „Je k
  dispozici nová verze" — reload uprostřed streamované odpovědi by ji zahodil.
- `viewport-fit=cover` + `env(safe-area-inset-*)` na topbaru, composeru a
  sidebaru, aby výřez a home indikátor nepřekrývaly ovládání.
- Na dotykových zařízeních (`@media (hover: none)`) jsou akce u zprávy trvale
  viditelné a mají 40×40 px. Bez toho by na telefonu nešly vyvolat vůbec —
  odkrýval je `:hover`, který na dotyku neexistuje.

Offline se načte shell z cache a appka řekne, že je offline; odpovídat bez
připojení neumí a nepředstírá to.

## Co chybí (vědomě, mimo MVP scope)

- Multi-user — účet je jeden, appka je zamčená, ale ne sdílená.
- Apple login — kód je hotový, ale bez placeného developer účtu ($99/rok) a
  ověřené domény ho nejde zapnout. Tlačítko zůstane jako „nenastaveno".
- Nativní appka (App Store / Play) — PWA na plochu ano, obchod ne. Znamenalo by
  to Capacitor, developer účty a review proces pro jednoho uživatele.
- Push notifikace — service worker je na ně připravený, ale bez serveru, který
  je posílá, by to byla jen kostra.
- Trénovaný router — routuje LLM plánovač, ne natrénovaný klasifikátor;
  heuristika na klíčová slova/délku zůstala jako fallback.
- Paměť volby modelu per konverzace — picker je globální a drží se v
  `localStorage`, ne u jednotlivé konverzace.
- Vlastní cache stažených stránek nebo vektorová databáze — deep research čte,
  co vrátí search tool, a syntetizuje to v rámci requestu. Ukládat si web je
  jiný projekt.
- Search API, které chtějí kartu nebo porušení ToS — Brave, SearXNG, DuckDuckGo,
  Perplexity Sonar. Důvody v
  [Co se vědomě nepřipojilo](#co-se-vědomě-nepřipojilo).
