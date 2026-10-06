#!/usr/bin/env bash
# Проверка подписи собранного Meet для macOS (CI, после sign_macos.sh).
#
#   bash scripts/check_macos_signature.sh <Meet.app> <Meet_….dmg> <версия>
#
# Печатает designated requirement (`codesign -d -r-`) и проверяет:
#  - подпись цела (`codesign --verify --deep --strict`);
#  - при подписи своим сертификатом (MEET_SIGNING_SHA1 от sign_macos.sh):
#    требование не на cdhash, а ровно явное `identifier "<id>" and
#    certificate leaf = H"<sha1>"` — одно и то же у всех сборок (ставит его
#    sign_macos.sh, а не генератор codesign); SHA-1 — закреплённый
#    (MACOS_CERT_SHA1);
#  - помощники в Resources (uv, ffmpeg, meet-audiotap) — ровно
#    `identifier "<id>.<имя>" and certificate leaf = H"<sha1>"`;
#  - путь обновления: образ монтируется так же, как в приложении
#    (`hdiutil attach -nobrowse -readonly -mountpoint`), Meet.app в нём —
#    настоящая папка без карантина, удовлетворяет требованию собранного
#    пакета, а его CFBundleShortVersionString — версия выпуска.
# Без MEET_SIGNING_SHA1 — ad-hoc: по тегу (MEET_REQUIRE_SIGNING=true) сбой,
# иначе печать и предупреждение.
# Что требование одинаково у двух сборок, видно по журналам двух прогонов.

set -euo pipefail

APP="${1:?путь к Meet.app}"
DMG="${2:?путь к образу .dmg}"
VERSION="${3:?версия}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA="$(tr '[:upper:]' '[:lower:]' <<<"${MEET_SIGNING_SHA1:-}")"

fail() { echo "ошибка: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

# Текст требования без «designated => » (и без «# » у неявного).
requirement() {
    codesign -d -r- "$1" 2>/dev/null | sed -n 's/^#* *designated => //p' | head -n 1
}

BUNDLE_ID="$(sed -n 's/^[[:space:]]*"identifier"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$ROOT/app/src-tauri/tauri.conf.json" | head -n 1)"
[[ -n "$BUNDLE_ID" ]] || fail "нет identifier в tauri.conf.json"

step "Подпись $APP"
codesign -d -r- "$APP"
codesign -dvv "$APP" 2>&1 | grep -E '^(Identifier|Authority|Signature|TeamIdentifier|Format)=' || true
codesign --verify --deep --strict --verbose=2 "$APP"
DR="$(requirement "$APP")"
echo "designated requirement: $DR"
[[ -n "$DR" ]] || fail "codesign не показал designated requirement"

if [[ -z "$SHA" ]]; then
    if [[ "${MEET_REQUIRE_SIGNING:-}" == "true" ]]; then
        fail "сборка по тегу подписана ad-hoc (designated requirement: $DR)"
    fi
    echo "::warning title=Подпись macOS::сборка подписана ad-hoc (designated requirement: $DR) — разрешения macOS не переживут обновление"
    exit 0
fi

if [[ -n "${MACOS_CERT_SHA1:-}" ]]; then
    pinned="$(tr '[:upper:]' '[:lower:]' <<<"${MACOS_CERT_SHA1//:/}")"
    [[ "$SHA" == "$pinned" ]] || fail "подписано сертификатом $SHA, закреплён $pinned"
fi
case "$DR" in
    *cdhash*) fail "требование на cdhash, хотя есть сертификат: $DR" ;;
esac
LEAF="identifier \"$BUNDLE_ID\" and certificate leaf = H\"$SHA\""
[[ "$DR" == "$LEAF" ]] || fail "требование не то: $DR (ждали: $LEAF)"
echo "требование — явное, сертификат $SHA: совпадает"
codesign --verify -R "=$LEAF" "$APP"
echo "codesign --verify -R \"=$LEAF\": ок"

step "Помощники в Resources"
for helper in uv ffmpeg meet-audiotap; do
    helper_dr="$(requirement "$APP/Contents/Resources/resources/$helper")"
    echo "  $helper: $helper_dr"
    expected="identifier \"$BUNDLE_ID.$helper\" and certificate leaf = H\"$SHA\""
    [[ "$helper_dr" == "$expected" ]] || fail "$helper: требование не то (ждали: $expected)"
done

step "Образ: как проверяет обновление"
MOUNT="$(mktemp -d)/mnt"
mkdir -p "$MOUNT"
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" "$DMG" >/dev/null
trap 'hdiutil detach "$MOUNT" -quiet || hdiutil detach "$MOUNT" -force -quiet || true' EXIT
[[ -d "$MOUNT/Meet.app" && ! -L "$MOUNT/Meet.app" ]] || fail "в образе нет Meet.app (или это ссылка)"
echo "  в образе: $(requirement "$MOUNT/Meet.app")"
codesign --verify --deep --strict -R "=$DR" "$MOUNT/Meet.app"
echo "Meet.app из образа удовлетворяет требованию собранного пакета: ок"
IMAGE_VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$MOUNT/Meet.app/Contents/Info.plist")"
echo "  версия в образе: $IMAGE_VERSION"
[[ "$IMAGE_VERSION" == "$VERSION" ]] || fail "версия в образе $IMAGE_VERSION, выпуск $VERSION"
if xattr -p com.apple.quarantine "$MOUNT/Meet.app" >/dev/null 2>&1; then
    fail "у Meet.app в образе карантин"
fi
