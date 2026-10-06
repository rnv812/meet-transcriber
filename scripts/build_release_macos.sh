#!/usr/bin/env bash
# Сборка Meet для macOS (Apple Silicon, экспериментально): Meet.app с
# подписью ad-hoc (от tauri build) и исходники FFmpeg и Opus рядом.
#
# Ключ подписи эта сборка не видит: здесь работает много чужого кода (npm,
# crates и их build.rs, configure/make). Подпись своим сертификатом и образ
# диска Meet_<версия>_aarch64.dmg с SHA256SUMS.txt — следующим шагом,
# scripts/sign_macos.sh (только программы Apple); проверка —
# scripts/check_macos_signature.sh. Целиком локально:
#   bash scripts/build_release_macos.sh 0.3.4
#   bash scripts/sign_macos.sh app/src-tauri/target/release/bundle/macos/Meet.app 0.3.4
#
# Зеркало scripts/build_release.ps1 (Windows) с отличиями macOS:
#  1. Сверяет версию в pyproject.toml, tauri.conf.json, Cargo.toml,
#     package.json и Cargo.lock с аргументом (проставлять не умеет —
#     для этого build_release.ps1 -SetVersion).
#  2. Колесо meet и точные версии движка профиля «Apple Silicon»
#     (constraints-mac.txt: torch с PyPI, extra engine-mac и gigaam).
#  3. uv (aarch64) — закреплённый релиз с проверкой SHA-256
#     (scripts/macos.sha256).
#  4. ffmpeg — собирается здесь же из закреплённых исходников FFmpeg и Opus
#     (SHA-256 там же) в LGPL-конфигурации: готовой статической LGPL-сборки
#     для arm64 из надёжного источника нет. Результат кэшируется в
#     build/cache и проверяется: без GPL, зависимости — только системные.
#  5. Помощник meet-audiotap (Swift, ScreenCaptureKit) — swiftc и
#     `--self-test`.
#  6. Ресурсы приложения (uv, ffmpeg, meet-audiotap, лицензии, колесо,
#     ограничения версий) и tauri build: только .app (tauri.macos.conf.json
#     Tauri подмешивает сам; подпись — ad-hoc, signingIdentity "-").
#
# Запуск: bash scripts/build_release_macos.sh 0.3.0
# Любой сбой — код выхода 1.

set -euo pipefail

VERSION="${1:-}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Использование: bash scripts/build_release_macos.sh <версия x.y.z>" >&2
    exit 1
fi

# --- Закреплённые версии (смена — вместе с хэшами в scripts/macos.sha256) ----
UV_VERSION="0.11.23"  # как $UvVersion в build_release.ps1 (тест сверяет)
UV_ASSET="uv-aarch64-apple-darwin.tar.gz"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ASSET}"
FFMPEG_VERSION="8.1.3"
FFMPEG_ASSET="ffmpeg-${FFMPEG_VERSION}.tar.xz"
FFMPEG_URL="https://ffmpeg.org/releases/${FFMPEG_ASSET}"
OPUS_VERSION="1.6.1"
OPUS_ASSET="opus-${OPUS_VERSION}.tar.gz"
OPUS_URL="https://downloads.xiph.org/releases/opus/${OPUS_ASSET}"
# torch — как TORCH_SPECS в src/meet/engine.py (тест сверяет); для Apple
# Silicon — с PyPI, без индекса PyTorch.
TORCH_SPECS=("torch==2.11.*" "torchaudio==2.11.*")
export MACOSX_DEPLOYMENT_TARGET="13.0"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/app"
TAURI_DIR="$APP_DIR/src-tauri"
RESOURCES="$TAURI_DIR/resources"
CACHE="$ROOT/build/cache"
WHEEL_OUT="$ROOT/build/wheel"
CONSTRAINTS_OUT="$ROOT/build/constraints"
HELPER_OUT="$ROOT/build/audiotap"
STARTED=$(date +%s)

step() { printf '\n==> %s\n' "$*"; }
fail() { echo "ошибка: $*" >&2; exit 1; }

# pkg-config нужен configure ffmpeg (libopus). Он есть в образе macos-14;
# ставить его на лету (brew — незакреплённая загрузка) не будем.
for tool in uv npm npx cargo swiftc shasum curl tar make clang python3 pkg-config; do
    command -v "$tool" >/dev/null || fail "$tool не найден в PATH"
done
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || fail "сборка — только на macOS arm64"

sha256() { shasum -a 256 "$1" | awk '{print $1}'; }

# Tauri подписывает только ad-hoc (signingIdentity "-"): чужие личности и
# сертификаты из среды ему не передаём.
unset APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD APPLE_SIGNING_IDENTITY

# --- 1. Версия -----------------------------------------------------------------
step "Версия $VERSION"
python3 - "$ROOT" "$VERSION" <<'PY'
import re, sys
from pathlib import Path
root, version = Path(sys.argv[1]), sys.argv[2]
files = [
    ("pyproject.toml", r'(?m)^version\s*=\s*"([^"]*)"'),
    ("app/src-tauri/tauri.conf.json", r'(?m)^\s*"version"\s*:\s*"([^"]*)"'),
    ("app/src-tauri/Cargo.toml", r'(?m)^version\s*=\s*"([^"]*)"'),
    ("app/package.json", r'(?m)^\s*"version"\s*:\s*"([^"]*)"'),
    ("app/src-tauri/Cargo.lock", r'(?m)^name = "meet-desktop"\r?\nversion = "([^"]*)"'),
]
bad = []
for name, pattern in files:
    found = re.search(pattern, (root / name).read_text(encoding="utf-8"))
    if not found:
        sys.exit(f"Не нашёл версию в {name}")
    print(f"  {name}: {found.group(1)}")
    if found.group(1) != version:
        bad.append(f"{name}: {found.group(1)}")
if bad:
    sys.exit(f"Версия в файлах не совпадает с {version}: " + "; ".join(bad)
             + ". Проставьте её: build_release.ps1 -SetVersion")
PY

# --- 2. Колесо meet и точные версии движка ------------------------------------
step "Колесо meet (uv build --wheel)"
rm -rf "$ROOT/build/lib" "$WHEEL_OUT"
uv build --wheel --out-dir "$WHEEL_OUT" "$ROOT"
WHEEL_NAME="meet_transcriber-${VERSION}-py3-none-any.whl"
WHEEL="$WHEEL_OUT/$WHEEL_NAME"
[[ -f "$WHEEL" ]] || fail "uv build не оставил $WHEEL_NAME"

step "Точные версии движка Apple Silicon (uv pip compile)"
rm -rf "$CONSTRAINTS_OUT"
mkdir -p "$CONSTRAINTS_OUT"
TORCH_IN="$CONSTRAINTS_OUT/torch.in"
printf '%s\n' "${TORCH_SPECS[@]}" > "$TORCH_IN"
CONSTRAINTS="$CONSTRAINTS_OUT/constraints-mac.txt"
uv pip compile "$ROOT/pyproject.toml" "$TORCH_IN" --extra engine-mac --extra gigaam \
    --python-version 3.12 --python-platform aarch64-apple-darwin \
    --no-config --no-header --no-annotate --quiet -o "$CONSTRAINTS"
grep -Eq '^gigaam @ .+#sha256=[0-9a-f]{64}$' "$CONSTRAINTS" \
    || fail "constraints-mac: нет строки gigaam с #sha256"
grep -Eq '^torch==2\.11\.' "$CONSTRAINTS" || fail "constraints-mac: нет torch 2.11"
echo "  constraints-mac.txt: $(wc -l < "$CONSTRAINTS" | tr -d ' ') пакетов"

# --- 3. Закреплённые загрузки ----------------------------------------------------
pinned_hash() {
    awk -v asset="$1" '!/^#/ && NF == 2 { name = $2; sub(/^\*/, "", name); if (name == asset) print tolower($1) }' \
        "$ROOT/scripts/macos.sha256"
}

fetch() {  # fetch <url> <asset> <подпапка кэша> → путь к файлу
    local url="$1" asset="$2" dir="$CACHE/$3" expected actual
    expected="$(pinned_hash "$asset")"
    [[ -n "$expected" ]] || fail "в scripts/macos.sha256 нет хэша для $asset"
    mkdir -p "$dir"
    if [[ -f "$dir/$asset" ]]; then
        actual="$(sha256 "$dir/$asset")"
        if [[ "$actual" == "$expected" ]]; then
            echo "  из кэша: $dir/$asset" >&2
            echo "$dir/$asset"
            return
        fi
        echo "  хэш $asset в кэше не совпал — качаю заново" >&2
        rm -f "$dir/$asset"
    fi
    echo "  скачиваю $url" >&2
    curl --fail --location --silent --show-error --retry 3 -o "$dir/$asset.part" "$url"
    actual="$(sha256 "$dir/$asset.part")"
    if [[ "$actual" != "$expected" ]]; then
        rm -f "$dir/$asset.part"
        fail "SHA-256 $asset не совпал: ждали $expected, получили $actual"
    fi
    mv "$dir/$asset.part" "$dir/$asset"
    echo "$dir/$asset"
}

step "uv $UV_VERSION"
UV_TAR="$(fetch "$UV_URL" "$UV_ASSET" "uv-$UV_VERSION")"

# --- 3b. ffmpeg (LGPL) из исходников --------------------------------------------
FFMPEG_PREFIX="$CACHE/ffmpeg-mac-$FFMPEG_VERSION-opus-$OPUS_VERSION"
step "ffmpeg $FFMPEG_VERSION (LGPL, Opus $OPUS_VERSION) → $FFMPEG_PREFIX"
# Исходники качаются всегда (не только при сборке): они идут в выпуск рядом
# с образом — LGPL требует давать исходники там же, где программу.
FFMPEG_TAR="$(fetch "$FFMPEG_URL" "$FFMPEG_ASSET" "ffmpeg-src-$FFMPEG_VERSION")"
OPUS_TAR="$(fetch "$OPUS_URL" "$OPUS_ASSET" "opus-src-$OPUS_VERSION")"
if [[ ! -x "$FFMPEG_PREFIX/bin/ffmpeg" ]]; then
    WORK="$ROOT/build/ffmpeg-work"
    rm -rf "$WORK" "$FFMPEG_PREFIX"
    mkdir -p "$WORK" "$FFMPEG_PREFIX"
    JOBS="$(sysctl -n hw.ncpu)"
    tar -xzf "$OPUS_TAR" -C "$WORK"
    (
        cd "$WORK/opus-$OPUS_VERSION"
        ./configure --prefix="$FFMPEG_PREFIX" --disable-shared --enable-static \
            --disable-doc --disable-extra-programs CFLAGS="-O2 -mmacosx-version-min=13.0"
        make -j"$JOBS"
        make install
    )
    tar -xJf "$FFMPEG_TAR" -C "$WORK"
    (
        cd "$WORK/ffmpeg-$FFMPEG_VERSION"
        # LGPL: без --enable-gpl/--enable-nonfree. --disable-autodetect: набор
        # возможностей не зависит от того, что стоит в образе раннера (иначе
        # подхватились бы библиотеки Homebrew, которых нет у человека); нужное
        # включено явно — zlib и bzlib системные (/usr/lib), Opus — свой.
        PKG_CONFIG_PATH="$FFMPEG_PREFIX/lib/pkgconfig" ./configure \
            --prefix="$FFMPEG_PREFIX" --arch=arm64 --target-os=darwin --cc=clang \
            --enable-static --disable-shared --disable-autodetect \
            --disable-doc --disable-ffplay --disable-debug --disable-network \
            --enable-zlib --enable-bzlib \
            --enable-libopus --pkg-config-flags=--static \
            --extra-cflags="-I$FFMPEG_PREFIX/include -mmacosx-version-min=13.0" \
            --extra-ldflags="-L$FFMPEG_PREFIX/lib -mmacosx-version-min=13.0"
        make -j"$JOBS"
        make install
        cp COPYING.LGPLv2.1 LICENSE.md "$FFMPEG_PREFIX/"
    )
    cp "$WORK/opus-$OPUS_VERSION/COPYING" "$FFMPEG_PREFIX/opus-COPYING"
    rm -rf "$WORK"
fi
FFMPEG_BIN="$FFMPEG_PREFIX/bin/ffmpeg"
"$FFMPEG_BIN" -hide_banner -version | head -n 1
CONFIGURATION="$("$FFMPEG_BIN" -hide_banner -buildconf)"
if grep -Eq -- '--enable-(gpl|nonfree)' <<<"$CONFIGURATION"; then
    fail "ffmpeg собран с GPL/nonfree"
fi
grep -q -- '--enable-libopus' <<<"$CONFIGURATION" || fail "ffmpeg собран без libopus"
"$FFMPEG_BIN" -hide_banner -L | grep -q "Lesser General Public License" \
    || fail "ffmpeg не под LGPL"
# Только системные библиотеки: всё прочее (Homebrew) у человека не найдётся.
FOREIGN="$(otool -L "$FFMPEG_BIN" | tail -n +2 | awk '{print $1}' \
    | grep -Ev '^(/usr/lib/|/System/Library/)' || true)"
[[ -z "$FOREIGN" ]] || fail "ffmpeg зависит от несистемных библиотек: $FOREIGN"

# --- 3c. Помощник meet-audiotap ---------------------------------------------------
step "meet-audiotap (Swift, ScreenCaptureKit)"
rm -rf "$HELPER_OUT"
mkdir -p "$HELPER_OUT"
swiftc -O -swift-version 5 -target arm64-apple-macos13.0 \
    -o "$HELPER_OUT/meet-audiotap" "$ROOT/mac/audiotap/main.swift"
SELF_TEST="$("$HELPER_OUT/meet-audiotap" --self-test)"
echo "  $SELF_TEST"
grep -q '"meet_audiotap":1' <<<"$SELF_TEST" || fail "meet-audiotap --self-test ответил не по протоколу"

# --- 4. Ресурсы приложения ---------------------------------------------------------
step "Ресурсы -> $RESOURCES"
rm -rf "$RESOURCES"
mkdir -p "$RESOURCES"
tar -xzf "$UV_TAR" -C "$RESOURCES" --strip-components 1 "uv-aarch64-apple-darwin/uv"
cp "$FFMPEG_BIN" "$RESOURCES/ffmpeg"
cp "$HELPER_OUT/meet-audiotap" "$RESOURCES/meet-audiotap"
{
    echo "FFmpeg $FFMPEG_VERSION, собран из исходников $FFMPEG_URL"
    echo "(SHA-256 $(pinned_hash "$FFMPEG_ASSET")) в LGPL-конфигурации:"
    echo "$CONFIGURATION"
    echo
    echo "Opus $OPUS_VERSION (BSD), исходники $OPUS_URL"
    echo
    echo "==================== FFmpeg: GNU LGPL 2.1 ===================="
    cat "$FFMPEG_PREFIX/LICENSE.md"
    echo
    cat "$FFMPEG_PREFIX/COPYING.LGPLv2.1"
    echo
    echo "==================== Opus ===================="
    cat "$FFMPEG_PREFIX/opus-COPYING"
} > "$RESOURCES/ffmpeg-LICENSE.txt"
cp "$ROOT/scripts/licenses/uv-LICENSE.txt" "$RESOURCES/uv-LICENSE.txt"
cp "$ROOT/LICENSE" "$RESOURCES/LICENSE"
cp "$ROOT/NOTICE" "$RESOURCES/NOTICE"
cp "$WHEEL" "$RESOURCES/$WHEEL_NAME"
cp "$CONSTRAINTS" "$RESOURCES/constraints-mac.txt"
chmod 755 "$RESOURCES/uv" "$RESOURCES/ffmpeg" "$RESOURCES/meet-audiotap"
"$RESOURCES/uv" --version
ls -l "$RESOURCES"

# --- 5. Окно и оболочка ---------------------------------------------------------------
BUNDLE_DIR="$TAURI_DIR/target/release/bundle"
rm -rf "$BUNDLE_DIR/dmg" "$BUNDLE_DIR/macos"
step "npm ci"
(cd "$APP_DIR" && npm ci --no-audit --no-fund)
step "tauri build (.app, релизный конфиг с ресурсами)"
(cd "$APP_DIR" && npx --no-install tauri build --config src-tauri/tauri.release.conf.json --bundles app)

# --- 6. Результат ------------------------------------------------------------------------
APP_BUNDLE="$BUNDLE_DIR/macos/Meet.app"
[[ -d "$APP_BUNDLE" ]] || fail "tauri build не оставил Meet.app в $BUNDLE_DIR/macos"
for file in uv ffmpeg meet-audiotap ffmpeg-LICENSE.txt "$WHEEL_NAME" constraints-mac.txt; do
    [[ -f "$APP_BUNDLE/Contents/Resources/resources/$file" ]] || fail "в Meet.app нет resources/$file"
done
SOURCES="$BUNDLE_DIR/dmg/sources"
mkdir -p "$SOURCES"
cp "$FFMPEG_TAR" "$OPUS_TAR" "$SOURCES/"

step "Готово"
echo "  пакет:   $APP_BUNDLE (подпись ad-hoc; своя — scripts/sign_macos.sh)"
echo "  исходники FFmpeg и Opus: $SOURCES"
echo "  время:   $(( $(date +%s) - STARTED )) с"
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
        echo "app=$APP_BUNDLE"
        echo "sources=$SOURCES"
    } >> "$GITHUB_OUTPUT"
fi
