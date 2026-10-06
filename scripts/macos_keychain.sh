#!/usr/bin/env bash
# shellcheck disable=SC2034  # SIGNING_* читает sign_macos.sh (source)
# Сертификат подписи Meet для macOS во временной связке ключей (CI).
#
# Выпуски подписываются своим самоподписанным сертификатом (codeSigning, 10
# лет), одним и тем же из выпуска в выпуск: designated requirement Meet.app —
# «identifier "<id>" and certificate leaf = H"<sha1>"» — не меняется между
# сборками, и macOS (TCC) сохраняет разрешения «Микрофон» и «Запись экрана»
# после обновления. Подпись ad-hoc привязана к хэшу кода и меняется с каждой
# сборкой.
#
# Ключ живёт в связке минуты: scripts/sign_macos.sh импортирует его уже
# после всей сборки (чужой код — npm, crates, configure/make — к этому времени
# отработал), подписывает и сразу удаляет связку.
#
# Как библиотека (`source`): keychain_import, keychain_cleanup; после
# keychain_import — SIGNING_IDENTITY (SHA-1) и SIGNING_KEYCHAIN.
# Как команда:
#   bash scripts/macos_keychain.sh import   — то же, без подписи (проверка);
#   bash scripts/macos_keychain.sh cleanup  — убрать связку и доверие
#                                              (страховка в шаге always()).
#
# Среда:
#   MACOS_CERT_P12        — base64 файла .p12 (сертификат и ключ);
#   MACOS_CERT_PASSWORD   — пароль .p12;
#   MACOS_CERT_SHA1       — (необязательно) ожидаемый SHA-1 сертификата;
#   MEET_REQUIRE_SIGNING  — `true` (сборка по тегу): без сертификата — сбой,
#                           иначе — предупреждение и ad-hoc.
#
# Ключ, .p12 и пароли в журнал не выводятся; .p12 удаляется сразу после
# импорта, ключ импортируется неизвлекаемым (-x).

KEYCHAIN_PATH="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/meet-signing.keychain-db"
TRUST_PEM="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/meet-signing-cert.pem"
SIGNING_IDENTITY=""
SIGNING_KEYCHAIN=""

signing_required() { [[ "${MEET_REQUIRE_SIGNING:-}" == "true" ]]; }

signing_warn() {
    echo "::warning title=Подпись macOS::$*"
    printf '\n!!! ВНИМАНИЕ: %s\n\n' "$*" >&2
}

keychain_fail() { echo "ошибка: $*" >&2; exit 1; }

# Команда не дольше N секунд (доверие к сертификату может ждать окна
# авторизации, которого на раннере никто не нажмёт). Гасится и её потомок
# (sudo → security).
limited() {
    local seconds="$1"
    shift
    "$@" &
    local pid=$!
    (
        sleep "$seconds"
        pkill -P "$pid" 2>/dev/null
        kill "$pid" 2>/dev/null
    ) &
    local watcher=$!
    local code=0
    wait "$pid" || code=$?
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return "$code"
}

# Подписывает ли codesign этой личностью из этой связки.
can_sign() {
    local dir
    dir="$(mktemp -d)"
    cp /usr/bin/true "$dir/probe"
    codesign --force -s "$1" --keychain "$2" --timestamp=none "$dir/probe" >/dev/null 2>&1
    local code=$?
    rm -rf "$dir"
    return "$code"
}

# Нет секрета: на теге — сбой, иначе — предупреждение.
no_secret() {
    if signing_required; then
        keychain_fail "сборка по тегу без сертификата подписи (секрет MACOS_CERT_P12 пуст): выпуск, подписанный ad-hoc, установленный Meet не примет, а разрешения macOS сбросятся у всех"
    fi
    signing_warn "секрет MACOS_CERT_P12 не задан — сборка будет подписана ad-hoc. Такую сборку macOS после обновления не узнаёт: разрешения «Микрофон» и «Запись экрана» спросит заново, а обновление на месте с подписанной версии её не примет."
}

# Импорт: личность готова — SIGNING_IDENTITY и SIGNING_KEYCHAIN заданы;
# секрета нет и он не обязателен — пусты; любая ошибка — выход. Звать не в
# условии (`if keychain_import`): там bash не прерывается на ошибках внутри.
keychain_import() {
    SIGNING_IDENTITY=""
    SIGNING_KEYCHAIN=""
    if [[ -z "${MACOS_CERT_P12:-}" ]]; then
        no_secret
        return 0
    fi
    [[ -n "${MACOS_CERT_PASSWORD:-}" ]] || keychain_fail "есть MACOS_CERT_P12, но нет MACOS_CERT_PASSWORD"

    local keychain_password p12dir
    keychain_password="$(openssl rand -hex 24)"
    p12dir="$(mktemp -d)"
    (
        umask 077
        printf '%s' "$MACOS_CERT_P12" \
            | python3 -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))' \
            > "$p12dir/meet-signing.p12"
    )
    security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1 || true
    security create-keychain -p "$keychain_password" "$KEYCHAIN_PATH"
    # Блокируется сама через 10 минут: подпись занимает секунды.
    security set-keychain-settings -lut 600 "$KEYCHAIN_PATH"
    security unlock-keychain -p "$keychain_password" "$KEYCHAIN_PATH"
    if ! security import "$p12dir/meet-signing.p12" -k "$KEYCHAIN_PATH" -f pkcs12 -x \
        -P "$MACOS_CERT_PASSWORD" -T /usr/bin/codesign >/dev/null; then
        rm -rf "$p12dir"
        keychain_fail "security import не принял .p12 (пароль? формат: нужен legacy PKCS#12 — RC2/3DES и MAC SHA-1)"
    fi
    rm -rf "$p12dir"
    # Без этого codesign спросил бы доступ к ключу окном.
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" \
        "$KEYCHAIN_PATH" >/dev/null
    # Связка — в список поиска пользователя (codesign ищет личность и там).
    local existing=() line
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line#\"}"
        line="${line%\"}"
        [[ -n "$line" && "$line" != "$KEYCHAIN_PATH" ]] && existing+=("$line")
    done < <(security list-keychains -d user)
    security list-keychains -d user -s "$KEYCHAIN_PATH" ${existing[@]+"${existing[@]}"}

    local identity
    identity="$(security find-identity -p codesigning "$KEYCHAIN_PATH" | grep -Eo '[0-9A-F]{40}' | head -n 1 || true)"
    [[ -n "$identity" ]] || keychain_fail "в .p12 нет личности для подписи кода"
    echo "Личность подписи: $identity" >&2
    if [[ -n "${MACOS_CERT_SHA1:-}" ]]; then
        local pinned
        pinned="$(tr '[:lower:]' '[:upper:]' <<<"${MACOS_CERT_SHA1//:/}")"
        [[ "$identity" == "$pinned" ]] \
            || keychain_fail "сертификат в MACOS_CERT_P12 ($identity) не тот, что закреплён в MACOS_CERT_SHA1 ($pinned): со сменой сертификата macOS забудет разрешения у всех"
    fi

    # Самоподписанный сертификат не доверен системе. codesign подписывает и
    # так (личность указана хэшем); если нет — доверие для подписи кода.
    if ! can_sign "$identity" "$KEYCHAIN_PATH"; then
        echo "codesign не подписывает недоверенной личностью — добавляю доверие (codeSign)" >&2
        security find-certificate -a -p "$KEYCHAIN_PATH" > "$TRUST_PEM"
        limited 60 sudo -n security add-trusted-cert -d -r trustRoot -p codeSign \
            -k /Library/Keychains/System.keychain "$TRUST_PEM" \
            || signing_warn "доверие к сертификату не добавилось"
        can_sign "$identity" "$KEYCHAIN_PATH" || keychain_fail "codesign не подписывает личностью $identity"
    fi
    SIGNING_IDENTITY="$identity"
    SIGNING_KEYCHAIN="$KEYCHAIN_PATH"
    return 0
}

keychain_cleanup() {
    if [[ -f "$TRUST_PEM" ]]; then
        sudo -n security remove-trusted-cert -d "$TRUST_PEM" >/dev/null 2>&1 || true
        local identity
        identity="$(openssl x509 -in "$TRUST_PEM" -noout -fingerprint -sha1 2>/dev/null \
            | sed 's/.*=//; s/://g' || true)"
        if [[ -n "$identity" ]]; then
            sudo -n security delete-certificate -Z "$identity" /Library/Keychains/System.keychain \
                >/dev/null 2>&1 || true
        fi
        rm -f "$TRUST_PEM"
    fi
    if [[ -f "$KEYCHAIN_PATH" ]]; then
        security delete-keychain "$KEYCHAIN_PATH" || true
        echo "Связка ключей подписи удалена" >&2
    fi
    SIGNING_IDENTITY=""
    SIGNING_KEYCHAIN=""
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    case "${1:-}" in
        import)
            keychain_import
            [[ -z "$SIGNING_IDENTITY" ]] || echo "$SIGNING_IDENTITY"
            ;;
        cleanup) keychain_cleanup ;;
        *) keychain_fail "использование: bash scripts/macos_keychain.sh import|cleanup" ;;
    esac
fi
