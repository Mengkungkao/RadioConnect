#!/bin/sh
# Install hook (mFruit OS runs it on install, reinstall, update and downgrade).
# It only checks: the system packages, the serial port, Codec2 and the radio
# setup come from mFruit OS's one-time radio setup, run once over SSH:
#   bash ~/.whisplay-os/system/current/scripts/setup-radio.sh
# It never registers with whisplay-daemon (mFruit OS does) and needs no sudo.
set -e
cd "$(dirname "$0")"
python3 - <<'CHECK'
import ctypes.util, sys
missing = []
for module in ("serial", "yaml", "PIL", "numpy", "cryptography"):
    try:
        __import__(module)
    except ImportError:
        missing.append(module)
if not ctypes.util.find_library("codec2"):
    missing.append("libcodec2")
if missing:
    print("RadioConnect needs: " + ", ".join(missing) + ". Run mFruit OS's radio setup once "
          "over SSH: bash ~/.whisplay-os/system/current/scripts/setup-radio.sh")
    sys.exit(1)
print("radioconnect: dependencies ok")
CHECK
