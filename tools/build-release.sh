#!/usr/bin/env bash
# Build the release archive MFruit OS installs and updates RadioConnect from.
#
#   tools/build-release.sh                 -> dist/radioconnect-<version>.tar.gz
#                                             and dist/SHA256SUMS
#
# Then publish it as a GitHub release whose tag is the manifest version:
#   gh release create v<version> dist/radioconnect-<version>.tar.gz dist/SHA256SUMS
# The Fruit Store offers it as an update (app page > Update / Updates and
# versions), verifies the checksum, and keeps the previous version for
# Roll back. See MFruit OS docs/apps/PUBLISHING.md.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
name="radioconnect-$version"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$name" dist
# The package: code, vendored SDK, hooks and docs. No tests, caches, local
# data or git metadata (MFruit OS docs/apps/PACKAGING.md, release contents).
tar --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=dist \
    --exclude=tests --exclude='*.pyc' --exclude=.venv -cf - . | tar -xf - -C "$stage/$name"
checker="${MFRUIT_OS:-$HOME/MFruitOS}/scripts/check-app.py"
if [ -f "$checker" ]; then
    python3 "$checker" "$stage/$name"
else
    echo "warning: $checker not found; package not checked" >&2
fi
(cd "$stage/$name" && bash test.sh)
tar -czf "dist/$name.tar.gz" -C "$stage" "$name"
(cd dist && sha256sum "$name.tar.gz" > SHA256SUMS)
echo "built dist/$name.tar.gz"
cat dist/SHA256SUMS
