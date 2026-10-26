#!/bin/sh
TASK_RUNTIME="/Users/polinasibaeva/.cache/codex-runtimes/codex-primary-runtime/dependencies"
if [ -d "$TASK_RUNTIME/node/bin" ]; then export PATH="$TASK_RUNTIME/node/bin:$TASK_RUNTIME/bin/fallback:$PATH"; fi
if ! command -v docker >/dev/null 2>&1 && [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
  export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi
