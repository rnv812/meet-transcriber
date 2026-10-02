"""Сборка для macOS (экспериментально) и её место в выпуске — без Mac.

Сама сборка идёт только в CI (job `macos` в release.yml); здесь проверяется
то, что ломается незаметно: закреплённые версии и хэши, LGPL-конфигурация
ffmpeg, лицензии в ресурсах, пробный прогон без Windows и без публикации,
неизменность Windows-сборки, Info.plist и протокол помощника."""

import json
import plistlib
import re
from pathlib import Path

from meet import audiotap, engine

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "build_release_macos.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
TAURI = ROOT / "app" / "src-tauri"


def _sh_value(name: str) -> str:
    found = re.search(rf'^{name}="([^"]*)"', SCRIPT, re.MULTILINE)
    assert found, name
    return found.group(1)


def test_pins_match_the_windows_build_and_the_engine():
    ps1 = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8-sig")
    uv = re.search(r"\$UvVersion = '([^']+)'", ps1).group(1)
    assert _sh_value("UV_VERSION") == uv
    specs = re.search(r"^TORCH_SPECS=\(([^)]*)\)", SCRIPT, re.MULTILINE).group(1)
    assert re.findall(r'"([^"]+)"', specs) == list(engine.TORCH_SPECS)
    assert "--extra engine-mac --extra gigaam" in SCRIPT
    assert "constraints-mac.txt" in SCRIPT
    assert "aarch64-apple-darwin" in SCRIPT


def test_every_download_is_hash_pinned():
    pins = {}
    for line in (ROOT / "scripts" / "macos.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split()
            pins[name] = digest
    assert set(pins) == {
        "uv-aarch64-apple-darwin.tar.gz",
        f"ffmpeg-{_sh_value('FFMPEG_VERSION')}.tar.xz",
        f"opus-{_sh_value('OPUS_VERSION')}.tar.gz",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in pins.values())
    for url in ("UV_URL", "FFMPEG_URL", "OPUS_URL"):
        assert _sh_value(url).startswith("https://")


def test_ffmpeg_is_built_lgpl_with_licenses_shipped():
    configure = SCRIPT[SCRIPT.index("./configure \\"):]
    configure = configure[: configure.index("make -j")]
    assert "--enable-gpl" not in configure and "--enable-nonfree" not in configure
    assert "--enable-libopus" in configure
    for off in ("--disable-network", "--disable-lzma", "--disable-sdl2"):
        assert off in configure
    # Сборку проверяют: без GPL, под LGPL, без библиотек Homebrew.
    assert "--enable-(gpl|nonfree)" in SCRIPT
    assert "Lesser General Public License" in SCRIPT
    assert "otool -L" in SCRIPT
    for name in ("ffmpeg-LICENSE.txt", "uv-LICENSE.txt", "LICENSE", "NOTICE"):
        assert f'"$RESOURCES/{name}"' in SCRIPT
    for part in ("COPYING.LGPLv2.1", "opus-COPYING"):
        assert part in SCRIPT
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Opus" in notice and "LGPL configuration" in notice


def test_helper_is_built_and_self_tested():
    assert "swiftc -O -swift-version 5 -target arm64-apple-macos13.0" in SCRIPT
    assert "--self-test" in SCRIPT
    assert '"$RESOURCES/meet-audiotap"' in SCRIPT
    assert 'DMG_NAME="Meet_${VERSION}_aarch64.dmg"' in SCRIPT


def test_helper_speaks_the_python_protocol():
    swift = (ROOT / "mac" / "audiotap" / "main.swift").read_text(encoding="utf-8")
    for name, code in (("exitUsage", audiotap.EXIT_USAGE),
                       ("exitUnsupported", audiotap.EXIT_UNSUPPORTED),
                       ("exitFailed", audiotap.EXIT_FAILED),
                       ("exitPermission", audiotap.EXIT_PERMISSION)):
        assert f"let {name}: Int32 = {code}" in swift, name
    assert f"let protocolVersion = {audiotap.PROTOCOL}" in swift
    for mode in ('"--stream"', '"--mic-users"', '"--self-test"'):
        assert mode in swift
    assert '"format": "s16le"' in swift


def test_workflow_dry_run_skips_windows_and_publishing():
    assert "workflow_dispatch:" in WORKFLOW
    assert re.search(r"macos_only:\n\s+description: .+\n\s+type: boolean\n\s+default: true", WORKFLOW)
    assert "if: github.event_name == 'push' || !inputs.macos_only" in WORKFLOW
    assert "runs-on: macos-14" in WORKFLOW
    assert 'bash scripts/build_release_macos.sh "$VERSION"' in WORKFLOW
    publish = WORKFLOW[WORKFLOW.index("  publish-macos:"):]
    assert "needs: [release, macos]" in publish
    assert "if: github.event_name == 'push'" in publish
    assert "SHA256SUMS.txt" in publish and "--clobber" in publish


def test_every_action_is_pinned_to_a_commit():
    for line in WORKFLOW.splitlines():
        if "uses:" in line:
            assert re.search(r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40} # ", line), line


def test_windows_bundle_config_is_untouched():
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["productName"] == "meet"
    assert conf["bundle"]["targets"] == ["nsis"]
    assert "macOS" not in conf["bundle"]


def test_mac_bundle_config_and_permission_strings():
    mac = json.loads((TAURI / "tauri.macos.conf.json").read_text(encoding="utf-8"))
    assert mac["productName"] == "Meet"
    assert mac["bundle"]["targets"] == ["app", "dmg"]
    macos = mac["bundle"]["macOS"]
    assert macos["minimumSystemVersion"] == "13.0"
    assert macos["signingIdentity"] == "-"  # ad-hoc: подписи Apple нет
    assert macos["hardenedRuntime"] is False
    plist = plistlib.loads((TAURI / macos["infoPlist"]).read_bytes())
    assert plist["LSUIElement"] is False
    assert set(plist["CFBundleLocalizations"]) == {"ru", "en"}
    keys = ("NSMicrophoneUsageDescription", "NSAudioCaptureUsageDescription",
            "NSScreenCaptureUsageDescription")
    for key in keys:
        assert plist[key].startswith("Meet ")
    for target, source in macos["files"].items():
        assert target.startswith("Resources/") and target.endswith("InfoPlist.strings")
        text = (TAURI / source).read_text(encoding="utf-8")
        for key in keys:
            assert f'"{key}" = "Meet ' in text, (source, key)
    for icon in ("icons/icon.icns",):
        assert (TAURI / icon).is_file()
