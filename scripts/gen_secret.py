"""Print one SECRET_KEY and exit.

    python scripts/gen_secret.py

The value is 32 random bytes, base64-encoded — the master key that
`app/crypto.py` uses to encrypt provider API keys at rest. Generate it once,
paste it into Render's environment (or `backend/.env` locally), and never
commit it or send it anywhere.

Losing it is not fatal but is not free either: every stored provider key
becomes undecryptable and has to be entered again. Changing it has exactly the
same effect, so treat a rotation as "re-enter all keys".
"""

import base64
import secrets

if __name__ == "__main__":
    print("SECRET_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())
