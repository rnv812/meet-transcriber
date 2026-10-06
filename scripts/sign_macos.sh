#!/usr/bin/env bash
# Подпись и образ диска Meet для macOS (CI, после build_release_macos.sh).
#
#   bash scripts/sign_macos.sh <Meet.app> <версия x.y.z>
#
# Порядок — ради ключа: вся сборка (npm, crates, configure/make — чужой код)
# уже прошла без него. Здесь только программы Apple:
#  1. Сертификат из секрета — во временную связку (scripts/macos_keychain.sh).
#  2. Помощники в ресурсах (uv, ffmpeg, meet-audiotap), затем Meet.app —
#     `codesign` с явным requirement: `identifier "<id>" and certificate leaf
#     = H"<sha1>"`. Он одинаков у всех сборок: macOS сохраняет разрешения
#     после обновления, обновление на месте сверяет с ним новую версию.
#  3. Связка удаляется сразу после подписи.
#  4. Образ Meet_<версия>_aarch64.dmg — `hdiutil create` из подписанного
#     пакета и ссылки на «Программы»; рядом SHA256SUMS.txt. Сам образ не
#     подписывается: недоверенная подпись образа лишь добавила бы вопрос
#     Gatekeeper при первой установке, а обновление проверяет пакет внутри.
#
# Нет секрета: по тегу (MEET_REQUIRE_SIGNING=true) — сбой, иначе ad-hoc-подпись
# сборки остаётся как есть, с предупреждением.
# В $GITHUB_ENV — MEET_SIGNING_SHA1 (открытый хэш сертификата; его ждёт
# check_macos_signature.sh); в $GITHUB_OUTPUT — dmg, sums, dmg_name.

set -euo pipefail

APP="${1:?путь к Meet.app}"
VERSION="${2:?версия}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/macos_keychain.sh
source "$ROOT/scripts/macos_keychain.sh"

fail() { echo "ошибка: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

[[ -d "$APP/Contents" ]] || fail "нет пакета $APP"
BUNDLE_ID="$(sed -n 's/^[[:space:]]*"identifier"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$ROOT/app/src-tauri/tauri.conf.json" | head -n 1)"
[[ -n "$BUNDLE_ID" ]] || fail "нет identifier в tauri.conf.json"

# Подписать своей личностью с явным requirement.
sign() {  # sign <путь> <идентификатор>
    codesign --force --sign "$SIGNING_IDENTITY" --keychain "$SIGNING_KEYCHAIN" \
        --timestamp=none --identifier "$2" \
        --requirements "=designated => identifier \"$2\" and certificate leaf = H\"$SHA\"" \
        "$1"
}

step "Подпись"
trap keychain_cleanup EXIT
keychain_import
if [[ -n "$SIGNING_IDENTITY" ]]; then
    SHA="$(tr '[:upper:]' '[:lower:]' <<<"$SIGNING_IDENTITY")"
    for helper in uv ffmpeg meet-audiotap; do
        sign "$APP/Contents/Resources/resources/$helper" "$BUNDLE_ID.$helper"
    done
    # Пакет — последним (изнутри наружу): его подпись запечатывает ресурсы.
    sign "$APP" "$BUNDLE_ID"
    keychain_cleanup
    codesign -d -r- "$APP"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
        echo "MEET_SIGNING_SHA1=$SHA" >> "$GITHUB_ENV"
    fi
else
    echo "Подпись остаётся ad-hoc (от tauri build)"
fi
trap - EXIT

step "Образ диска"
OUT="$(cd "$(dirname "$APP")/.." && pwd)/dmg"
mkdir -p "$OUT"
DMG_NAME="Meet_${VERSION}_aarch64.dmg"
DMG="$OUT/$DMG_NAME"
STAGING="$(mktemp -d)/Meet"
mkdir -p "$STAGING"
ditto "$APP" "$STAGING/Meet.app"
ln -s /Applications "$STAGING/Applications"
rm -f "$DMG"
# hdiutil на раннерах изредка отвечает «Resource busy» — повтор.
for attempt in 1 2 3 4 5; do
    if hdiutil create -volname Meet -srcfolder "$STAGING" -fs HFS+ -format UDZO -ov "$DMG"; then
        break
    fi
    [[ $attempt -lt 5 ]] || fail "hdiutil create не сработал"
    echo "hdiutil create: повтор через 5 с" >&2
    sleep 5
done
rm -rf "$(dirname "$STAGING")"
HASH="$(shasum -a 256 "$DMG" | awk '{print $1}')"
SUMS="$OUT/SHA256SUMS.txt"
printf '%s  %s\n' "$HASH" "$DMG_NAME" > "$SUMS"
echo "  образ:   $DMG"
echo "  SHA-256: $HASH"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
        echo "dmg=$DMG"
        echo "sums=$SUMS"
        echo "dmg_name=$DMG_NAME"
    } >> "$GITHUB_OUTPUT"
fi
