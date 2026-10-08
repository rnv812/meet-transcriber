#!/usr/bin/env bash
# Смоук Meet на macOS-раннере (0.5): живого Mac у разработки нет — проверяем, что
# собранное приложение и движок оживают. Звук и разрешения не проверяются
# (у раннера нет устройств и окна TCC) — только запуск, ответ резидента и окно.
#
#   scripts/macos_smoke.sh <python> <Meet.app> <папка снимков>
#
# 1. Резидент из движка CI (`meet-tray --headless`) в пустой папке данных:
#    поднимается, пишет daemon.json и отвечает на GET /state своей версией.
# 2. Meet.app с той же папкой данных: процесс окна живёт 20 с и не падает;
#    снимок экрана — в артефакты (что показало окно: мастер, установка движка…).
# Сбой любого шага — код ≠ 0 и журналы в той же папке.
set -euo pipefail

PY="$1"
APP="$2"
OUT="$3"
mkdir -p "$OUT"
DATA="$(mktemp -d)"
export MEET_DATA_DIR="$DATA"
LOG="$OUT/resident.log"

cleanup() {
  [[ -n "${RES_PID:-}" ]] && kill "$RES_PID" 2>/dev/null || true
  [[ -n "${APP_PID:-}" ]] && kill "$APP_PID" 2>/dev/null || true
  cp "$DATA"/logs/*.log "$OUT"/ 2>/dev/null || true
}
trap cleanup EXIT

echo "== резидент (headless) в $DATA"
"$PY" -c "from meet.tray import main; import sys; sys.argv=['meet-tray','--headless']; main()" >"$LOG" 2>&1 &
RES_PID=$!
for _ in $(seq 1 60); do
  [[ -f "$DATA/daemon.json" ]] && break
  kill -0 "$RES_PID" 2>/dev/null || { echo "резидент вышел:"; cat "$LOG"; exit 1; }
  sleep 1
done
[[ -f "$DATA/daemon.json" ]] || { echo "нет daemon.json за 60 с"; cat "$LOG"; exit 1; }
PORT="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['port'])" "$DATA/daemon.json")"
TOKEN="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get('token') or '')" "$DATA/daemon.json")"
STATE="$(curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/state")"
echo "$STATE" | "$PY" -c "import json,sys; s=json.load(sys.stdin); assert s['status']=='idle', s; print('резидент отвечает: версия', s.get('version'), '· запуск', s.get('started_at'))"
curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/settings" >/dev/null
echo "настройки читаются"

echo "== окно: $APP"
BIN="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
"$APP/Contents/MacOS/$BIN" >"$OUT/app.log" 2>&1 &
APP_PID=$!
sleep 20
kill -0 "$APP_PID" 2>/dev/null || { echo "окно вышло раньше времени:"; cat "$OUT/app.log"; exit 1; }
screencapture -x "$OUT/screen.png" || echo "снимок экрана не вышел (раннер без экрана?)"
echo "окно живёт 20 с; снимок — $OUT/screen.png"
