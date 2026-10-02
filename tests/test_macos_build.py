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
    # Набор возможностей не зависит от образа раннера: автоопределение
    # выключено, нужное включено явно.
    for flag in ("--disable-autodetect", "--disable-network", "--enable-zlib", "--enable-bzlib"):
        assert flag in configure
    # Никаких установок на лету (brew — незакреплённая загрузка).
    assert "brew install" not in SCRIPT
    # Исходники FFmpeg и Opus идут в выпуск рядом с образом (LGPL).
    assert 'cp "$FFMPEG_TAR" "$OPUS_TAR" "$SOURCES/"' in SCRIPT
    assert 'echo "sources=$SOURCES"' in SCRIPT
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
    for mode in ('"--stream"', '"--mic-users"', '"--self-test"', '"--preflight"'):
        assert mode in swift
    assert '"format": "s16le"' in swift
    # Помощник дописывает тишину раньше резидента: иначе дописали бы оба.
    from meet import recorder

    gap = float(re.search(r"private let silenceAfter = ([0-9.]+)", swift).group(1))
    limit = float(re.search(r"private let fillLimit = ([0-9.]+)", swift).group(1))
    assert gap < limit < recorder.TICK_PAD_S
    # --preflight: только проверка разрешения, коды 0 / 77.
    assert "exit(CGPreflightScreenCaptureAccess() ? exitOK : exitPermission)" in swift
    assert "private let errorEvery = 5.0" in swift


def test_workflow_dry_run_skips_windows_and_publishing():
    assert "workflow_dispatch:" in WORKFLOW
    assert re.search(r"macos_only:\n\s+description: .+\n\s+type: boolean\n\s+default: true", WORKFLOW)
    windows = WORKFLOW[WORKFLOW.index("  release:"):WORKFLOW.index("    runs-on: windows-latest")]
    # Только на теге: и пуш, и ручной запуск без macos_only (ветка с именем
    # вида v1.2.3 выпуск не создаст).
    assert ("if: github.ref_type == 'tag' && (github.event_name == 'push' || "
            "(github.event_name == 'workflow_dispatch' && !inputs.macos_only))") in windows
    # Пуш в ветку пробного прогона собирает только macOS.
    assert 'branches: ["ci/macos-dry-run"]' in WORKFLOW
    assert "runs-on: macos-14" in WORKFLOW
    assert 'bash scripts/build_release_macos.sh "$VERSION"' in WORKFLOW
    publish = WORKFLOW[WORKFLOW.index("  publish-macos:"):]
    assert "needs: [release, macos]" in publish
    assert "SHA256SUMS.txt" in publish and "--clobber" in publish


def _job(name: str) -> str:
    start = WORKFLOW.index(f"\n  {name}:\n")
    rest = WORKFLOW[start + 1:]
    nxt = [rest.find(f"\n  {j}:\n") for j in ("release", "macos", "publish-macos",
                                                "publish-macos-dry-run")]
    ends = [i for i in nxt if i > 0]
    return rest[: min(ends)] if ends else rest


def test_token_is_read_only_except_for_publishing_jobs():
    """Пробный прогон (пуш в ci/macos-dry-run публичного репозитория) не
    получает пишущего токена: право писать — только у публикующих job."""
    top = "\n".join(line for line in WORKFLOW[: WORKFLOW.index("\njobs:")].splitlines()
                    if not line.startswith("#"))
    assert "permissions:\n  contents: read" in top
    assert "contents: write" not in top
    for job in ("release", "publish-macos"):
        assert "permissions:\n      contents: write" in _job(job), job
    for job in ("macos", "publish-macos-dry-run"):
        assert "permissions:\n      contents: read" in _job(job), job
        assert "contents: write" not in _job(job), job
    # Публикация — только на теге.
    assert "if: github.ref_type == 'tag' && (github.event_name == 'push' || !inputs.macos_only)" \
        in _job("publish-macos")
    assert "if: github.ref_type != 'tag'" in _job("publish-macos-dry-run")


def test_no_checkout_keeps_the_token_on_disk():
    lines = WORKFLOW.splitlines()
    checkouts = [i for i, line in enumerate(lines) if "uses: actions/checkout@" in line]
    assert len(checkouts) == 4
    for i in checkouts:
        assert lines[i + 1].strip() == "with:" and lines[i + 2].strip() == "persist-credentials: false"


def test_publish_merges_checksums_and_uploads_the_image_first():
    publish = _job("publish-macos")
    assert "timeout-minutes:" in publish
    assert "scripts/merge_sums.py" in publish
    image = publish.index('gh release upload "$TAG" "$dmg" sources/*')
    sums = publish.index("gh release upload \"$TAG\" merged/SHA256SUMS.txt")
    assert image < sums
    dry = _job("publish-macos-dry-run")
    assert "scripts/merge_sums.py" in dry and "gh release" not in dry
    assert "sha256sum -c" in dry
    # Настоящая публикация не загрузит суммы без строки установщика Windows.
    check = publish.index("_x64-setup\\.exe$' merged/SHA256SUMS.txt")
    assert check < sums


def test_ffmpeg_cache_key_uses_the_runner_image():
    """ImageOS/ImageVersion — переменные машины: в контексте `env` их нет."""
    macos = _job("macos")
    assert 'echo "key=${ImageOS:-unknown}-${ImageVersion:-unknown}"' in macos
    assert "key: ffmpeg-mac-${{ steps.image.outputs.key }}-" in macos
    assert "env.ImageOS" not in WORKFLOW


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


# --- слияние сумм выпуска (scripts/merge_sums.py) ----------------------------------

def _merge_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("merge_sums", ROOT / "scripts" / "merge_sums.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A, B, C = "a" * 64, "b" * 64, "c" * 64


def test_merge_keeps_windows_lines_and_replaces_the_image_line():
    merge = _merge_module().merge
    published = f"\ufeff{A}  meet_0.3.0_x64-setup.exe\r\n{C}  Meet_0.3.0_aarch64.dmg"
    new = f"{B}  Meet_0.3.0_aarch64.dmg\n"
    assert merge(published, new) == (f"{A}  meet_0.3.0_x64-setup.exe\n"
                                     f"{B}  Meet_0.3.0_aarch64.dmg\n")
    # Без перевода строки в конце и в двоичном режиме (`*имя`) — то же.
    assert merge(f"{A} *meet_0.3.0_x64-setup.exe", new).splitlines() == [
        f"{A}  meet_0.3.0_x64-setup.exe", f"{B}  Meet_0.3.0_aarch64.dmg"]
    assert merge("", new) == new


def test_merge_refuses_broken_new_sums(tmp_path):
    module = _merge_module()
    import pytest

    with pytest.raises(ValueError):
        module.merge("", "")
    with pytest.raises(ValueError):
        module.merge("", "not-a-hash  Meet_0.3.0_aarch64.dmg\n")
    published, new, out = tmp_path / "p.txt", tmp_path / "n.txt", tmp_path / "o.txt"
    published.write_bytes(f"{A}  meet_0.3.0_x64-setup.exe\r\n".encode())
    new.write_text(f"{B}  Meet_0.3.0_aarch64.dmg\n", encoding="utf-8")
    assert module.main([str(published), str(new), str(out)]) == 0
    assert out.read_bytes() == (f"{A}  meet_0.3.0_x64-setup.exe\n{B}  Meet_0.3.0_aarch64.dmg\n").encode()
