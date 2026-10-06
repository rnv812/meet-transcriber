#!/usr/bin/env bash
# Сертификат подписи Meet для macOS во временной связке ключей (CI, job
# `macos` в release.yml).
#
# Выпуски подписываются своим самоподписанным сертификатом (codeSigning, 10
# лет), одним и тем же из выпуска в выпуск: designated requirement Meet.app —
# «identifier "<id>" and certificate … = H"<sha1>"» — не меняется между
# сборками, и macOS (TCC) сохраняет разрешения «Микрофон» и «Запись экрана»
# после обновления. Подпись ad-hoc привязана к хэшу кода и меняется с каждой
# сборкой.
#
#   bash scripts/macos_keychain.sh import   — секреты из среды:
#       MACOS_CERT_P12      — base64 файла .p12 (сертификат и ключ);
#       MACOS_CERT_PASSWORD — пароль .p12;
#       MACOS_CERT_SHA1     — (необязательно) ожидаемый SHA-1 сертификата.
#     Нет MACOS_CERT_P12 (форк, ветка без секретов) — не ошибка: громкое
#     предупреждение, сборка подпишется ad-hoc, как раньше.
#     Есть — в $GITHUB_ENV уходят MEET_SIGNING_IDENTITY (SHA-1) и
#     MEET_SIGNING_KEYCHAIN; их читают build_release_macos.sh и
#     check_macos_signature.sh.
#   bash scripts/macos_keychain.sh cleanup  — убрать связку и доверие.
#
# Ключ, .p12 и пароли в журнал не выводятся; .p12 удаляется сразу после
# импорта, ключ импортируется неизвлекаемым (-x).

set -euo pipefail

KEYCHAIN="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/meet-signing.keychain-db"
PEM="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/meet-signing-cert.pem"

warn() {
    echo "::warning title=Подпись macOS::$*"
    printf '\n!!! ВНИМАНИЕ: %s\n\n' "$*" >&2
}
fail() { echo "ошибка: $*" >&2; exit 1; }

# Команда не дольше N секунд (доверие к сертификату может ждать окна
# авторизации, которого на раннере никто не нажмёт).
limited() {
    local seconds="$1"
    shift
    "$@" &
    local pid=$!
    (sleep "$seconds"; kill "$pid" 2>/dev/null) &
    local watcher=$!
    local code=0
    wait "$pid" || code=$?
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$code"
}

# Подписывает ли codesign этой личностью — так же, как Tauri (`--force -s`,
# без --keychain: связка — в списке поиска).
can_sign() {
    local probe
    probe="$(mktemp -d)/probe"
    cp /usr/bin/true "$probe"
    codesign --force -s "$1" "$probe" >/dev/null 2>&1
    local code=$?
    rm -rf "$(dirname "$probe")"
    return "$code"
}

import() {
    if [[ -z "${MACOS_CERT_P12:-}" ]]; then
        warn "секрет MACOS_CERT_P12 не задан — сборка будет подписана ad-hoc. Такую сборку macOS после обновления не узнаёт: разрешения «Микрофон» и «Запись экрана» спросит заново, а обновление на месте с подписанной версии её не примет."
        return 0
    fi
    [[ -n "${MACOS_CERT_PASSWORD:-}" ]] || fail "есть MACOS_CERT_P12, но нет MACOS_CERT_PASSWORD"

    local keychain_password p12
    keychain_password="$(openssl rand -hex 24)"
    p12="$(mktemp -d)/meet-signing.p12"
    (
        umask 077
        printf '%s' "$MACOS_CERT_P12" \
            | python3 -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))' \
            > "$p12"
    )
    security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
    security create-keychain -p "$keychain_password" "$KEYCHAIN"
    security set-keychain-settings -lut 21600 "$KEYCHAIN"
    security unlock-keychain -p "$keychain_password" "$KEYCHAIN"
    if ! security import "$p12" -k "$KEYCHAIN" -f pkcs12 -x -P "$MACOS_CERT_PASSWORD" \
        -T /usr/bin/codesign -T /usr/bin/security >/dev/null; then
        rm -rf "$(dirname "$p12")"
        fail "security import не принял .p12 (пароль? формат: нужен legacy PKCS#12 — RC2/3DES и MAC SHA-1)"
    fi
    rm -rf "$(dirname "$p12")"
    # Без этого codesign спросил бы доступ к ключу окном.
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" \
        "$KEYCHAIN" >/dev/null
    # Связка — в список поиска пользователя (там её найдёт codesign из Tauri).
    local existing=()
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line#\"}"
        line="${line%\"}"
        [[ -n "$line" && "$line" != "$KEYCHAIN" ]] && existing+=("$line")
    done < <(security list-keychains -d user)
    security list-keychains -d user -s "$KEYCHAIN" ${existing[@]+"${existing[@]}"}

    local identity
    identity="$(security find-identity -p codesigning "$KEYCHAIN" | grep -Eo '[0-9A-F]{40}' | head -n 1 || true)"
    [[ -n "$identity" ]] || fail "в .p12 нет личности для подписи кода"
    echo "Личность подписи: $identity"
    if [[ -n "${MACOS_CERT_SHA1:-}" ]]; then
        local pinned
        pinned="$(tr '[:lower:]' '[:upper:]' <<<"${MACOS_CERT_SHA1//:/}")"
        [[ "$identity" == "$pinned" ]] \
            || fail "сертификат в MACOS_CERT_P12 ($identity) не тот, что закреплён в MACOS_CERT_SHA1 ($pinned): со сменой сертификата macOS забудет разрешения у всех"
    fi

    # Самоподписанный сертификат не доверен системе. codesign обычно подписывает
    # и так (личность указана хэшем); если нет — доверие для подписи кода.
    if ! can_sign "$identity"; then
        echo "codesign не подписывает недоверенной личностью — добавляю доверие (codeSign)"
        security find-certificate -a -p "$KEYCHAIN" > "$PEM"
        limited 60 sudo -n security add-trusted-cert -d -r trustRoot -p codeSign \
            -k /Library/Keychains/System.keychain "$PEM" \
            || warn "доверие к сертификату не добавилось"
        can_sign "$identity" || fail "codesign не подписывает личностью $identity"
    fi
    security find-identity -p codesigning "$KEYCHAIN"

    if [[ -n "${GITHUB_ENV:-}" ]]; then
        {
            echo "MEET_SIGNING_IDENTITY=$identity"
            echo "MEET_SIGNING_KEYCHAIN=$KEYCHAIN"
        } >> "$GITHUB_ENV"
    fi
}

cleanup() {
    if [[ -f "$PEM" ]]; then
        sudo -n security remove-trusted-cert -d "$PEM" >/dev/null 2>&1 || true
        local identity
        identity="$(openssl x509 -in "$PEM" -noout -fingerprint -sha1 2>/dev/null \
            | sed 's/.*=//; s/://g' || true)"
        if [[ -n "$identity" ]]; then
            sudo -n security delete-certificate -Z "$identity" /Library/Keychains/System.keychain \
                >/dev/null 2>&1 || true
        fi
        rm -f "$PEM"
    fi
    if [[ -f "$KEYCHAIN" ]]; then
        security delete-keychain "$KEYCHAIN" || true
    fi
}

case "${1:-}" in
    import) import ;;
    cleanup) cleanup ;;
    *) fail "использование: bash scripts/macos_keychain.sh import|cleanup" ;;
esac
