#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export FLUTTER_STORAGE_BASE_URL="${FLUTTER_STORAGE_BASE_URL:-https://mirrors.cloud.tencent.com/flutter}"
export PUB_HOSTED_URL="${PUB_HOSTED_URL:-https://mirrors.tuna.tsinghua.edu.cn/dart-pub}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/homebrew/share/android-commandlinetools}"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"
for flutter_bin in /opt/homebrew/share/flutter/bin "$HOME/development/flutter/bin"; do
  if [ -x "$flutter_bin/flutter" ]; then
    export PATH="$flutter_bin:$PATH"
    break
  fi
done
docker compose up -d
exec "$ROOT/.venv/bin/uvicorn" app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port "${API_PORT:-18110}"
