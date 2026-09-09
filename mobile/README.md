# Prizma na telefonu

Krátká odpověď: **funguje, a nepotřebuje k tomu druhou aplikaci.** Prizma je
hotová PWA — telefon si ji nainstaluje z prohlížeče, dostane ikonu na ploše,
otevře se bez adresního řádku a shell běží i offline.

Jediná tvrdá podmínka je **veřejná adresa přes HTTPS**. Není to volba designu,
je to pravidlo prohlížeče: service worker i nabídka „Přidat na plochu" žijí jen
v secure contextu. `http://192.168.…` secure context není, takže se přes LAN
appka otevře a bude fungovat, ale nenainstaluje se a offline nepojede.

---

## Co je pro mobil hotové

Ověřeno v kódu, ne odhadem — čísla řádků odpovídají stavu při psaní:

| věc | kde | stav |
|---|---|---|
| manifest, `display: standalone` | `frontend/public/manifest.webmanifest` | ✅ |
| ikony 192 / 512 / maskable | `frontend/public/icon-*.png` | ✅ |
| iOS (manifest ignoruje celý) | `frontend/index.html` — `apple-touch-icon`, `apple-mobile-web-app-capable`, titulek, status bar | ✅ |
| výřez a home indicator | `viewport-fit=cover` + `env(safe-area-inset-*)` | ✅ |
| barva systémové lišty podle tématu | dvě `theme-color` s `prefers-color-scheme` | ✅ |
| service worker | `frontend/public/sw.js` | ✅ |
| layout na 375 px | složen na jeden sloupec, bez vodorovného přetečení | ✅ |

Na service workeru je jedno rozhodnutí, které stojí za zapamatování:
**`/api/*` se neintercepuje vůbec.** Žádné cachování, žádné retry, žádná fronta.
Zastaralý seznam konverzací by mátl; přehraný `POST` by utratil kvótu providera
nebo poslal zprávu dvakrát — a to už je skutečná chyba. Offline tedy funguje
_otevření_ appky, ne odpovídání.

## Co pro mobil hotové není

- **Deploy.** Bez něj nic z toho výše nedostaneš na telefon jinak než přes LAN
  náhled níž. Blokuje to na účtech (Render, Neon, Resend) — viz `README.md`.
- **Push notifikace.** Nejsou a zatím nedávají smysl: odpověď přijde do pár
  sekund, zatímco okno je otevřené.
- **Sdílení do appky** (Android share target). Přidalo by „poslat text do
  Prizmy" ze systémového menu. Chce to `share_target` v manifestu a jednu
  obsluhu — malá věc, ale bez deploye se nedá vyzkoušet.

---

## Instalace na telefon (po deployi)

**Android / Chrome.** Otevři adresu → menu ⋮ → *Přidat na plochu* nebo
*Instalovat aplikaci*. Chrome nabídku ukáže sám až po pár návštěvách; položka
v menu je tam vždycky.

**iOS / Safari.** Musí to být **Safari**, ne Chrome — na iOS umí instalovat jen
on. Tlačítko *Sdílet* → *Přidat na plochu*. iOS z manifestu nečte nic, proto ty
`apple-*` metatagy v `index.html`; bez nich by ikona byla screenshot stránky.

**Odinstalace** je jako u běžné aplikace, podržet ikonu → odebrat. Data
(přihlášení, cache shellu) zmizí s ní.

## Co ověřit hned po deployi

Tohle je seznam věcí, které na `localhost` vypadají v pořádku a na serveru se
teprve ukáže, že nejsou:

1. **`/manifest.webmanifest` vrací `200`** a `application/manifest+json`.
   Servíruje ho `backend/app/spa.py` z `ROOT_FILES`.
2. **`/sw.js` vrací `200 text/javascript`** a **z kořene**, ne z `/assets/`.
   Service worker má scope podle složky, ze které se servíruje — `/assets/sw.js`
   by neuměl řídit celou appku.
3. **Service worker se opravdu zaregistroval.** V DevTools konzoli telefonu
   nebo přes vzdálené ladění:
   `navigator.serviceWorker.getRegistrations().then(r => console.log(r.length))`
   Musí být `1`. Nula znamená, že buď není HTTPS, nebo `/sw.js` nesedí.
4. **CSP nehlásí porušení.** Inline skript s tématem v `index.html` se hashuje
   při buildu; kdyby hash nesedl, téma na první snímek problikne a v konzoli
   je chyba.
5. **Ikona na ploše je ikona**, ne screenshot — pokud je, chybí
   `apple-touch-icon` (iOS) nebo se nenačetl manifest (Android).
6. **Otoč telefon.** Manifest má `orientation: portrait-primary`, takže
   nainstalovaná appka na šířku nepřepne. V prohlížeči ano — rozdíl je
   očekávaný, ne chyba.

---

## LAN náhled: podívat se na to na telefonu ještě dnes

`lan-preview.ps1` pustí Prizmu tak, aby na ni telefon ve stejné Wi-Fi dosáhl.
Je to **náhled, ne instalace** — uvidíš layout, písma, dotyková cíle a
klávesnici. Neuvidíš instalaci na plochu ani offline režim, protože to je ta
HTTPS podmínka výš.

```powershell
powershell -ExecutionPolicy Bypass -File .\mobile\lan-preview.ps1
```

Skript najde adresu v síti, dovolí ji v `ALLOWED_HOSTS` a spustí server na
`0.0.0.0`. Adresu vypíše — tu otevři na telefonu.

**Než to spustíš, vědět tohle:** appku tím zpřístupníš všemu, co je ve stejné
síti, a jediné, co ji brání, je čtyřmístný kód. V tvé domácí Wi-Fi je to
v pořádku; na veřejné síti to nedělej. Skript běží **v popředí** a končí
`Ctrl+C` — schválně, aby nešlo omylem nechat běžet zpřístupněnou instanci.
Naplánovaná úloha `Prizma AutoStart` se nemění a dál poslouchá jen na
`127.0.0.1`; když zrovna běží, skript port nepřebírá a řekne, co s tím.

### Co si to vyžádalo v kódu

Samotné `--host 0.0.0.0` nestačilo. `enforce_origin` v
`backend/app/auth/guard.py` pouští v lokálním režimu **jen loopback**, takže
telefon dostal `403 Povoleno jen z tohoto počítače` dřív, než se stihl zeptat
na kód. Je to záměr — appka na notebooku nemá důvod poslouchat síť.

Přibyla proto co nejtěsnější výjimka, `LAN_PREVIEW`:

- **vypnutá**, dokud ji někdo výslovně nezapne — dělá to jen tenhle skript,
- povolí navíc **jen privátní rozsahy** (`ipaddress.is_private`, tedy 10/8,
  172.16/12, 192.168/16 a `fe80::`), veřejná adresa neprojde ani zapnutá,
- **se `PUBLIC_MODE` se nesmí potkat**: `check_public_mode` v takovém případě
  server vůbec nespustí, aby to v konfiguraci nasazené appky nestálo napsané
  jako záruka, kterou to nedává,
- platí po dobu procesu, ne trvale — nikde se neukládá.

Pokrývá to `backend/tests/test_lan_preview.py`: vypnutá nepustí ani sousední
telefon, zapnutá pustí LAN a nepustí `8.8.8.8`, loopback si nenárokuje a
na veřejném serveru shodí start.

Windows Firewall příchozí port 8000 ve výchozím stavu zahodí. Skript to pozná
a nabídne jedno z dvojího:

```powershell
# povolit — jen privátní profil, jen port 8000, jen na dobu testu
powershell -ExecutionPolicy Bypass -File .\mobile\lan-preview.ps1 -OpenFirewall

# uklidit po sobě
powershell -ExecutionPolicy Bypass -File .\mobile\lan-preview.ps1 -CloseFirewall
```

Obojí chce **admin** PowerShell a obojí je vratné: `-CloseFirewall` smaže přesně
to pravidlo, které `-OpenFirewall` vytvořil, podle jména `Prizma LAN preview`.

---

## Proč ne nativní appka

Zvažoval se Capacitor — obal, ze kterého vypadne `.apk`. Odmítnuto, protože by
přinesl druhý buildovací systém (JDK 17, Android SDK), vlastní podpisové klíče
a údržbu navíc, a jediné, co by proti PWA přidal, je instalace bez deploye.
Až deploy bude, PWA dá totéž: ikonu, celou obrazovku, offline shell.

Pokud by se to změnilo — třeba kvůli push notifikacím nebo sdílení souborů —
je to přírůstek do téhle složky, ne přepis appky. Capacitor obaluje ten samý
`frontend/dist`.
