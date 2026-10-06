#!/usr/bin/env bash
# Проверка подписи собранного Meet для macOS (CI, после build_release_macos.sh).
#
#   bash scripts/check_macos_signature.sh <Meet.app> <Meet_….dmg>
#
# Печатает designated requirement (`codesign -d -r-`) и проверяет:
#  - подпись цела (`codesign --verify --deep`);
#  - при подписи своим сертификатом (MEET_SIGNING_IDENTITY — SHA-1 от
#    macos_keychain.sh): требование не на cdhash, а ровно
#    `identifier "<id>" and certificate leaf|root = H"<sha1>"` — у
#    самоподписанного сертификата из одного звена leaf и root — один и тот же
#    сертификат; такое требование одинаково у всех сборок;
#  - то же требование явно в форме leaf (`codesign --verify -R`);
#  - «вторая сборка»: копия пакета, подписанная заново той же личностью,
#    даёт то же требование;
#  - помощники в Resources (uv, ffmpeg, meet-audiotap) подписаны тем же
#    сертификатом;
#  - путь обновления: образ монтируется так же, как в приложении
#    (`hdiutil attach -nobrowse -readonly -mountpoint`), и Meet.app в нём
#    удовлетворяет требованию собранного пакета.
# Без MEET_SIGNING_IDENTITY (секрета нет) — ad-hoc: только печать и
# предупреждение.

set -euo pipefail

APP="${1:?путь к Meet.app}"
DMG="${2:?путь к образу .dmg}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IDENTITY="${MEET_SIGNING_IDENTITY:-}"

fail() { echo "ошибка: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

# Текст требования без «designated => » (и без «# » у неявного).
requirement() {
    codesign -d -r- "$1" 2>/dev/null | sed -n 's/^#* *designated => //p' | head -n 1
}

BUNDLE_ID="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["identifier"])' \
    "$ROOT/app/src-tauri/tauri.conf.json")"

step "Подпись $APP"
codesign -d -r- "$APP"
codesign -dvv "$APP" 2>&1 | grep -E '^(Identifier|Authority|Signature|TeamIdentifier|Format)=' || true
codesign --verify --deep --verbose=2 "$APP"
DR="$(requirement "$APP")"
echo "designated requirement: $DR"
[[ -n "$DR" ]] || fail "codesign не показал designated requirement"

if [[ -z "$IDENTITY" ]]; then
    echo "::warning title=Подпись macOS::сборка подписана ad-hoc (designated requirement: $DR) — разрешения macOS не переживут обновление"
    exit 0
fi

SHA="$(tr '[:upper:]' '[:lower:]' <<<"$IDENTITY")"
case "$DR" in
    *cdhash*) fail "требование на cdhash, хотя есть сертификат: $DR" ;;
esac
LEAF="identifier \"$BUNDLE_ID\" and certificate leaf = H\"$SHA\""
ROOTREQ="identifier \"$BUNDLE_ID\" and certificate root = H\"$SHA\""
if [[ "$DR" != "$LEAF" && "$DR" != "$ROOTREQ" ]]; then
    fail "требование не то: $DR (ждали: $LEAF — или то же с certificate root)"
fi
echo "требование — сертификат $SHA: совпадает"
codesign --verify -R "=$LEAF" "$APP"
echo "codesign --verify -R \"=$LEAF\": ок"

step "Вторая подпись той же личностью даёт то же требование"
SECOND="$(mktemp -d)"
ditto "$APP" "$SECOND/Meet.app"
codesign --force -s "$IDENTITY" "$SECOND/Meet.app"
DR2="$(requirement "$SECOND/Meet.app")"
echo "первая: $DR"
echo "вторая: $DR2"
rm -rf "$SECOND"
[[ "$DR" == "$DR2" ]] || fail "требования двух подписей различаются"

step "Помощники в Resources"
for helper in uv ffmpeg meet-audiotap; do
    path="$APP/Contents/Resources/resources/$helper"
    helper_dr="$(requirement "$path")"
    echo "  $helper: $helper_dr"
    [[ "$helper_dr" == *"H\"$SHA\""* ]] || fail "$helper подписан не нашим сертификатом"
done

step "Образ: как проверяет обновление"
MOUNT="$(mktemp -d)/mnt"
mkdir -p "$MOUNT"
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" "$DMG" >/dev/null
trap 'hdiutil detach "$MOUNT" -quiet || hdiutil detach "$MOUNT" -force -quiet || true' EXIT
[[ -d "$MOUNT/Meet.app" ]] || fail "в образе нет Meet.app"
echo "  в образе: $(requirement "$MOUNT/Meet.app")"
codesign --verify --deep -R "=$DR" "$MOUNT/Meet.app"
echo "Meet.app из образа удовлетворяет требованию собранного пакета: ок"
if xattr -p com.apple.quarantine "$MOUNT/Meet.app" >/dev/null 2>&1; then
    fail "у Meet.app в образе карантин"
fi
