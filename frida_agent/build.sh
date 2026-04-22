#!/usr/bin/env bash
# Build the Frida agent bundle (dist/agent.js).
# Requires: Node.js + npm. Installs dependencies on first run.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
    echo "[build] installing dependencies"
    npm install --silent
fi

echo "[build] compiling src/agent.ts -> dist/agent.js"
npx frida-compile src/agent.ts -o dist/agent.js

echo "[build] done: $(wc -c < dist/agent.js) bytes"
