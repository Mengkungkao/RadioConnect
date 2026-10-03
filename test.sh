#!/bin/sh
# Smoke test ("test" in manifest.json): runs after install, before activation;
# a non-zero exit keeps the previous version. Imports the app and renders every
# screen offline. Never touches the display, the radio or the network.
set -e
export PYTHONDONTWRITEBYTECODE=1   # leave the installed version folder as shipped
cd "$(dirname "$0")"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
python3 -c "import app.main"
python3 tools/preview.py --out "$out" >/dev/null
echo "radioconnect: test passed"
