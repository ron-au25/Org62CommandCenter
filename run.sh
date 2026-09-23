#!/usr/bin/env bash
# Org62 Command Center — one-shot setup + run.
# Usage: ./run.sh          (from the repo root, or from anywhere)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5057}"
ORG62_ALIAS="${ORG62_ALIAS:-org62}"

# Rebuild the venv if it's missing, or if it's stale (copied from elsewhere —
# its scripts keep the old absolute interpreter path baked in).
if [ -d .venv ] && ! .venv/bin/python3 -c "" >/dev/null 2>&1; then
  echo "Stale .venv (broken interpreter path) — rebuilding..."
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

# Org62 CLI auth — log in once if this alias has no session yet.
if ! sf org display --target-org "$ORG62_ALIAS" >/dev/null 2>&1; then
  echo "No CLI session for alias '$ORG62_ALIAS' — opening browser login..."
  sf org login web --alias "$ORG62_ALIAS"
fi

echo "Starting Org62 Command Center on http://127.0.0.1:${PORT}  (Ctrl-C to stop)"
exec env PORT="$PORT" .venv/bin/python web/server.py
