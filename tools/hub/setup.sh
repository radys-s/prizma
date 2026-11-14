#!/usr/bin/env bash
# Run Refrakt on a hub as a systemd --user service, with the keys from Tomáš's notebook.
#
# On the notebook, send the database and its key to this hub with Tailscale:
#   tailscale file cp backend\data\app.db backend\data\master.key <hub>:
# (send backend\.env as well, or instead of master.key, if SECRET_KEY is set there). Then run
# this script. It moves them into place without displaying them, adds a gateway token for
# Studio and the hub agents, and starts the service on 127.0.0.1:8000. Safe to run again.
set -euo pipefail

backend="$(cd "$(dirname "$0")/../../backend" && pwd)"
env_file="$backend/.env"
data="$backend/data"
token_file="$HOME/.config/refrakt/gateway.env"

if [[ ! -x "$backend/.venv/bin/uvicorn" ]]; then
  echo "Installing the backend (one-time)..."
  (cd "$backend" && uv venv -q && uv pip install -q -e . -c requirements.lock)
fi

if [[ ! -f "$data/app.db" ]]; then
  inbox="$(mktemp -d)"
  echo "Waiting for app.db and master.key (or .env) from Taildrop..."
  until [[ -f "$inbox/app.db" ]] && { [[ -f "$inbox/master.key" ]] || [[ -f "$inbox/.env" ]]; }; do
    tailscale file get --wait=true "$inbox" >/dev/null
  done
  mkdir -p "$data"
  install -m 600 "$inbox/app.db" "$data/app.db"
  # The key that decrypts the database: a file next to it (the default on a PC), or
  # SECRET_KEY in .env when one was set up.
  if [[ -f "$inbox/master.key" ]]; then install -m 600 "$inbox/master.key" "$data/master.key"; fi
  if [[ -f "$inbox/.env" ]]; then install -m 600 "$inbox/.env" "$env_file"; fi
  rm -rf "$inbox"
  echo "Keys installed (not displayed)."
fi
touch "$env_file"
chmod 600 "$env_file"

if ! grep -q '^GATEWAY_TOKEN=.' "$env_file"; then
  printf '\nGATEWAY_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$env_file"
fi
mkdir -p "$(dirname "$token_file")"
umask 077
grep '^GATEWAY_TOKEN=' "$env_file" | tail -1 | sed 's/^/REFRAKT_/' > "$token_file"
chmod 600 "$token_file"
# The bare token, for clients that read a file instead of the environment (OpenCode's
# `{file:...}` in opencode.json).
sed 's/^REFRAKT_GATEWAY_TOKEN=//' "$token_file" > "${token_file%.env}.token"

unit="$HOME/.config/systemd/user/refrakt.service"
mkdir -p "$(dirname "$unit")"
cat > "$unit" <<UNIT
[Unit]
Description=Refrakt (BYOK router, OpenAI-compatible /v1 for Studio and the agents)
After=network-online.target

[Service]
WorkingDirectory=$backend
# A notebook .env may point at Windows paths or the LAN preview; these win over it.
Environment=DB_PATH=$data/app.db
Environment=PUBLIC_MODE=false
Environment=LAN_PREVIEW=false
ExecStart=$backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
MemoryMax=700M

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable -q refrakt.service
systemctl --user restart refrakt.service

for _ in $(seq 30); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
set -a; . "$token_file"; set +a
count="$(curl -fsS http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $REFRAKT_GATEWAY_TOKEN" \
  | python3 -c 'import json, sys; print(len(json.load(sys.stdin)["data"]) - 1)')"
echo "Refrakt runs on 127.0.0.1:8000 with $count models. Token for clients: $token_file"

# OpenCode (and through it T3 Code's model picker) gets the models as `refrakt/<id>`.
# Run this script again after adding a key in Refrakt to refresh the list.
if command -v opencode >/dev/null; then
  curl -fsS http://127.0.0.1:8000/v1/models -H "Authorization: Bearer $REFRAKT_GATEWAY_TOKEN" \
    | TOKEN_PATH="${token_file%.env}.token" python3 -c '
import json, os, sys
path = os.path.expanduser("~/.config/opencode/opencode.json")
config = json.load(open(path)) if os.path.exists(path) else {"$schema": "https://opencode.ai/config.json"}
config.setdefault("provider", {})["refrakt"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Refrakt",
    "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "{file:" + os.environ["TOKEN_PATH"] + "}",
    },
    "models": {m["id"]: {"name": m["id"]} for m in json.load(sys.stdin)["data"]},
}
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(config, open(path, "w"), indent=2)
print("OpenCode: refrakt/* models written to", path)
'
fi
