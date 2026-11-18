# Security review, November 2026

Reviewed on 2026-11-18 against `main` at `75d3b3a`. Line numbers below refer to that commit. Testing used a throwaway database and loopback ports 8030/8031 on hub2. It never touched the live instance and used no real keys.

Method: I read the backend auth, gateway, key storage, local-model and tool code, the frontend renderer and its CSP, and the hub scripts. I ran `pip-audit` on `backend/requirements.lock` and `npm audit --omit=dev`, and scanned the git history with gitleaks 8.30.1 plus a pattern grep. Each fixed finding has a test that fails on `75d3b3a`.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | The e-mail password opened the access-code login (`/api/auth/login`) with no second factor. Before the account was claimed, anyone who knew `OWNER_EMAIL` could register and sign in | Fixed, #19 |
| 2 | Medium | `/v1/chat/completions` read and parsed a body of any size before checking the gateway token | Fixed, #20 |
| 3 | Medium | The file tools' secret denylist missed `.env.*` variants, `.envrc` and non-RSA SSH keys | Fixed, #23 |
| 4 | Low | OIDC sign-in used an unverified `email` claim to decide who may claim the account | Fixed, #21 |
| 5 | Low | `master.key` was created world-readable (umask) | Fixed, #22 |
| 6 | High (advisory) | litellm 1.98.0: GHSA-7hp6-4w63-5g45. It affects only the LiteLLM Proxy server, which Refrakt does not run | Fixed, #24 |
| 7 | Low | Before the account is claimed, an e-mail registration for `OWNER_EMAIL` locks the owner out | Written up below |
| 8 | Low | `/email/resend` revives expired challenges, extends them, resets the guess counter and spends the mail quota | Written up below |
| 9 | Low | On Render, uvicorn trusts the client-written `X-Forwarded-For` entry, so per-IP limits are client-chosen | Written up below |
| 10 | Low | A TOTP code can be used twice within its window | Written up below |
| 11 | Low | Stored keys are not bound to their row (AES-GCM without associated data) | Written up below |
| 12 | Info | The 4-digit access code is still enabled on Render | Written up below |
| 13 | Info | A gateway token holder can make litellm fetch image URLs from the server | Written up below |
| 14 | Info | Provider error text, sometimes with a masked key, lands in the server log | Written up below |
| 15 | Info | Apple's `form_post` callback can never complete (fails closed) | Written up below |
| 16 | Info | A personal-looking e-mail address appears in a docstring | Written up below |
| 17 | Info | Dev-only npm advisories (picomatch ReDoS via vite/vitest) | Written up below |

## Fixed

**1. The e-mail password as the access code.** `identities.resolve()` created an account from an e-mail registration with `password_hash=secret_hash` (`backend/app/auth/identities.py:142-148`). That column is where `local_secret()` looks for the access code when no `local` identity exists (`identities.py:205-219`). So `POST /api/auth/login` accepted the e-mail password and issued a session without the mailed code or the authenticator app. Before the account was claimed, an attacker could register `OWNER_EMAIL` with their own password, even with no mail configured, and sign straight in. #19 stops writing that column for non-`local` secrets and never falls back to it on an e-mail-created account, so databases written before the fix are covered too. Their old hash stays in the column, but nothing reads it. `scripts/set_passcode.py` depended on the fallback and called a function that does not exist; it now goes through the `local` identity.

**2. Gateway body before token.** The endpoint declared `body: ChatRequest` (`backend/app/api/gateway.py:169-170`). FastAPI reads and parses a declared body before router dependencies run, and the body cap in `HardeningMiddleware` covers `/api` only (`backend/app/auth/hardening.py:190`). Measured on a local server: one unauthenticated 50 MB POST raised peak RSS from 253 to 406 MB; after #20 it grows by 12 kB. On the 512 MB Render instance a few such requests end the process. #20 parses JSON before validating it. A body that does not parse is reported as FastAPI reports it, without echoing the body. The review caught that a first version returned 500 for a body that is not UTF-8.

**3. Tool denylist.** `DENIED_NAMES` matched exact names (`backend/app/tools/sandbox.py:37-54`). `.env.development.local`, `.env.test`, `.envrc`, `id_ed25519` and `id_ecdsa` were read and sent to the model provider like any text file. #23 denies every `.env.<x>` except the templates, the same rule `.dockerignore` uses, and adds the other SSH key names. A name list is never complete. These are still readable: `*.env` files (`prod.env`, docker-compose env files), `.netrc`, `.pgpass`, `.dev.vars`, an AWS `credentials` file, and SSH keys with custom names (`id_ed25519_github`). `run_command` gets past any list by design (`python` and `git show` are allowed), which is why it is switched on per message.

**4. OIDC `email_verified`.** `oauth.exchange()` returned the id_token's `email` as is (`backend/app/auth/oauth.py:314-320`), and `owner_allows()` decides by it who creates the account. Microsoft's claim is unverified for other tenants, and the `common` endpoint takes any tenant. Microsoft is not enabled in `render.yaml`. #21 keeps the address only when `email_verified` is true.

**5. `master.key` mode.** `_local_key()` used `write_bytes` (`backend/app/crypto.py:92-94`), so the file was 0664 under hub2's umask. hub2 itself is not affected (`setup.sh` installs the key with mode 600) and Render uses `SECRET_KEY`. #22 creates it 0600 with `O_EXCL`, and tightens an existing loose key file when it is read. The review caught that the first version opened the file without `O_BINARY`. On Windows that writes every 0x0A byte as CR LF, so about one fresh install in eight would have lost its keys at the next start.

**6. litellm.** CVSS 9.9 for the LiteLLM Proxy (internal user to proxy admin). Refrakt imports litellm as a library only. #24 bumps the pin to 1.100.4, which resolves with every other pin unchanged, and raises the `pyproject.toml` floor to the same version. The suite and a local smoke run against a fake OpenAI-compatible server behave the same on both versions.

## Written up, not changed

**7. Owner-claim squat (Low).** Rule 1 in `identities.resolve()` (`identities.py:137-151`) creates the account from the first registration whose address equals `OWNER_EMAIL`. The address is not proven at that point. `/email/register` creates it before the code is checked, and keeps it even when the mail fails (`backend/app/api/auth.py:398-404`, `428-474`). After #19 this is no longer a way in, but it still locks the owner out: re-registering fails on the password, and Google/GitHub sign-in gets `jiny_ucet` because an account exists. The fix is a design choice, so it is not made here. Options: create the account only in `/email/verify`, keeping the pending password hash on the challenge (needs a schema change); or, in public mode, require the first account to be claimed through a provider that verifies the address. Until then, claim a new deployment before sharing its URL. To recover a squatted account (with `PASSCODE_LOGIN_ENABLED` on), run `scripts/set_passcode.py` against that database. It sets an access code on the existing account and signs out every session. Sign in with the code, then remove the squatter's e-mail identity in settings. The other way is to delete the `appuser` and `authidentity` rows.

**8. `/email/resend` (Low).** The route (`auth.py:604-628`) takes a sequential challenge id, has no throttle, and does not check expiry. `rotate_code()` (`backend/app/auth/challenges.py:162-175`) extends the expiry and resets `attempts`. Anyone can therefore revive an abandoned mailed challenge and keep it alive indefinitely, spending Resend's 100 mails a day so that the owner's codes stop arriving. They can also reset the five-guess limit (the global throttle still bounds guessing) and read the masked address. Suggested fix: refuse expired or consumed challenges, cap resends per challenge, do not extend past the original expiry, and apply `_guard_throttle` and a per-IP limit.

**9. `X-Forwarded-For` on Render (Low).** The Dockerfile starts uvicorn with `--forwarded-allow-ips='*'` (`Dockerfile:72`). With `*`, uvicorn 0.52 takes the leftmost entry, which the client writes (`uvicorn/middleware/proxy_headers.py`, the `always_trust` branch). Per-IP buckets in `throttle.py` and `ratelimit.py` are therefore client-chosen. The global throttle bucket still bounds access-code guessing. The per-IP limit on `/api/auth/oauth/{id}/start` (`auth.py:713`) can be sidestepped to flush the 256 pending handshakes (`oauth.py:164-176`), which breaks a sign-in in progress until it is retried. Suggested fix: trust only Render's proxy range, so that uvicorn picks the rightmost untrusted entry. This needs Render's actual addressing, which I could not see from here.

**10. TOTP replay (Low).** `totp.verify()` (`backend/app/auth/totp.py:67-84`) does not remember the last accepted time step. A code seen together with the password can be used again within about 90 s. Suggested fix: store the last accepted step on `AppUser` and refuse steps at or below it.

**11. GCM without associated data (Low).** `encrypt`/`decrypt` pass `None` as AAD (`crypto.py:115`, `128`). Someone who can write the database but lacks the master key can swap ciphertexts between `providerkey` rows. The impact is small: hosted providers have fixed endpoints, and a local `api_base` provider never receives a stored key (`keystore.endpoint`). Suggested fix: use `credential_id` as AAD, with a fallback that re-encrypts old rows on first read.

**12. Access code on Render (Info).** It is four digits (`backend/app/auth/core.py:33`). Under the global bucket an attacker gets roughly 12 guesses a day after the first burst, which is Tomáš's documented tradeoff. `render.yaml:60` still enables it. Set `PASSCODE_LOGIN_ENABLED=0` once Google or GitHub sign-in works, as the comment there says.

**13. Image URLs through the gateway (Info).** For Gemini and Anthropic slugs, litellm downloads `http(s)` image URLs in `messages` on the server (`convert_to_anthropic_image_obj` → `convert_url_to_base64`). A holder of `GATEWAY_TOKEN` can therefore make the server send blind GET requests, including to internal addresses. Today the token holders are Tomáš's own tools. If the token is ever shared, reject non-`https` or private-address image URLs in the gateway.

**14. Provider errors in logs (Info).** Several paths log `str(exc)`: `backend/app/routing/titles.py:119`, `backend/app/providers/client.py:142`, and the planning, decomposition, compaction and research modules. Some providers put a masked key (prefix and last 4 characters) in their 401 text. The gateway logs only the provider id and wait time, and returns only the exception class.

**15. Apple sign-in (Info).** Apple posts the callback cross-site (`auth.py:818-834`). The `SameSite=Lax` state cookie is not sent on that request, and the Origin check refuses `appleid.apple.com`, so the flow cannot complete. It fails closed. Apple is not configured.

**16. Personal data (Info).** The `_mask` docstring (`auth.py:415`) uses a personal-looking e-mail address as its example in this public repository. If it is a real address, replace it with one under `example.com`.

**17. Dev dependencies (Info).** `npm audit` with dev dependencies reports picomatch ≤ 4.0.7 ReDoS (high) through vite, vitest, tinyglobby and fdir. These are build and test tools and are not in the shipped bundle; `npm audit --omit=dev` is clean. Bump vite and vitest when convenient.

## Checked, no issue

- **Loopback guard.** The TCP peer must be `127.0.0.1` or `::1` (`backend/app/auth/guard.py:102-108`). uvicorn honours `X-Forwarded-For` only from `127.0.0.1` by default, and hub2 starts it without proxy flags (`tools/hub/setup.sh:65`). A dual-stack `::ffff:127.0.0.1` peer is refused, so that case fails closed. Through `tailscale serve`, the tailnet `Host` gets 421 (`hardening.py:151`) and the tailnet address in `X-Forwarded-For` gets 403. One caveat: a plain TCP forwarder in front of a local-mode instance (socat, `ssh -L` from another machine) makes remote callers look local, and then only the access code protects it.
- **Host header.** It is checked against the allowlist, including ports and bracketed IPv6. A request with an empty Host passes, but only a non-browser client can send one, and such a client can send any Host anyway.
- **Gateway.**
  - No token configured: 404 (`gateway.py:45`).
  - Token comparison is constant-time (`gateway.py:52`); only the length can leak.
  - Only `/v1/models` and `/v1/chat/completions` exist.
  - A model id must be a slug of a configured provider (`gateway.py:77`), so it cannot choose a host. Fields such as `api_base` are dropped (`gateway.py:87`).
  - Tools are forwarded to the provider, never executed.
  - Errors carry only `provider: ExceptionClass`, in streams as well (`gateway.py:144-146`).
- **Key storage.**
  - A fresh 12-byte random nonce per encryption, the 32-byte key length enforced, and the GCM tag checked.
  - No route returns a key; the provider list returns booleans.
  - `export_keys.py` opens the source read-only and creates its output 0600 with `O_EXCL` (`tools/hub/export_keys.py:31`, `39`).
  - `setup.sh` installs `app.db`, `master.key` and `.env` with mode 600 and writes the token files under umask 077 (`setup.sh:30-34`, `45-50`).
- **Local model.** Fetching a user-typed URL is the feature in local mode, and only the signed-in owner can use it (loopback + session + CSRF). It is refused in public mode (`backend/app/api/providers.py:184-189`, `backend/app/providers/local_llm.py:107-109`). httpx does not follow redirects by default.
- **Frontend.**
  - Model output cannot inject markup: there is no raw HTML (no `rehype-raw`, no `dangerouslySetInnerHTML`), react-markdown 9's default URL filter drops `javascript:` links, and KaTeX's `trust` stays off (`frontend/src/components/Markdown.tsx:33`).
  - External links get `noopener noreferrer` (`Markdown.tsx:245`).
  - The CSP's `img-src 'self' data:` and `connect-src 'self'` stop model output from leaking data through images or requests (`frontend/vite.config.ts:29-44`). Framing is denied by a response header.
  - localStorage holds only the theme, picker choices and drafts, and drafts are cleared on logout (`frontend/src/lib/storage.ts:40`).
  - The service worker never touches `/api` (`frontend/public/sw.js:90`).
- **Sessions.**
  - The session cookie is HttpOnly, Secure in public mode, and SameSite=Lax (`auth.py:99-116`).
  - The CSRF header is compared in constant time on every state-changing route (`guard.py:138-141`, `auth.py:156-159`).
  - Logout deletes the server-side row; sessions expire after 30 days and are pruned.
  - A credential change revokes the other sessions.
- **OAuth.**
  - `state` is single-use and bound to an HttpOnly cookie and to the signed-in user.
  - PKCE uses S256 (`oauth.py:202`), and `aud`, `iss` and `exp` are checked (`oauth.py:249`).
  - The redirect always goes to `FRONTEND_URL`.
- **Secrets in git history.** gitleaks 8.30.1 scanned 94 commits from a mirror of the GitHub repository (every branch and PR ref) plus the 9 commits that exist only in hub2's clone, and found nothing. A grep of every diff for 20 provider key formats and for `SECRET_KEY=`, `GATEWAY_TOKEN=` and `PRIZMA_KEY_*=` with values found nothing. No `.env`, `master.key` or database file was ever committed. **No key needs rotating.**
- **Dependencies.** `npm audit --omit=dev`: 0 vulnerabilities. `pip-audit`: only litellm, fixed in #24.

## For Tomáš

- **Deploy.** Merges to `main` redeploy Render. hub2 needs a pull in `~/projects/refrakt-live` and a `refrakt.service` restart.
- **If the Render account was created by e-mail registration and never got an access code,** the e-mail password alone could sign in until #19 is live. After the deploy, set an access code once in the app: that also signs out every other session.
- **Turn off the access code on Render.** Set `PASSCODE_LOGIN_ENABLED=0` once Google or GitHub sign-in works.
- **Decide on items 7–11.** Each needs a design choice or checking something on Render.
