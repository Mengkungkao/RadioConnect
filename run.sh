#!/bin/sh
# Entrypoint. mFruit OS runs this with the working directory set to the
# active version and WHISPLAY_APP_ID / WHISPLAY_OS_APP_DATA set.
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
exec python3 -m app.main "$@"
