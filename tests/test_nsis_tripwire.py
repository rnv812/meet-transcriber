"""Проверка установщика перед выпуском (scripts/nsis_tripwire.ps1).

Папка установки — она же папка данных (%LOCALAPPDATA%\\meet): правило «ни
RMDir /r, ни масок Delete по $INSTDIR, кроме resources» держит записи, голоса
и настройки при обновлении, переустановке и откате. Здесь — самопроверка
правил на примерах и прогон по нашим хукам и языковым файлам: их установщик
подключает как есть.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nsis_tripwire.ps1"
WINDOWS = ROOT / "app" / "src-tauri" / "windows"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="нужен PowerShell")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_rules_catch_data_wipes_and_spare_installer_files():
    result = _run("-SelfTest")
    assert result.returncode == 0, result.stdout + result.stderr


def test_our_hooks_and_texts_never_delete_data():
    files = [WINDOWS / "hooks.nsh", *sorted((WINDOWS / "lang").glob("*.nsh"))]
    assert len(files) == 3
    result = _run("-Path", ",".join(str(f) for f in files))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_recursive_delete_of_the_install_folder_fails_the_build(tmp_path):
    bad = tmp_path / "bad.nsh"
    bad.write_text('Section\n  RMDir /r "$INSTDIR"\nSectionEnd\n', encoding="utf-8")
    result = _run("-Path", str(bad))
    assert result.returncode != 0


def test_masked_delete_in_the_install_root_fails_the_build(tmp_path):
    bad = tmp_path / "bad.nsh"
    bad.write_text('Delete "$INSTDIR\\*.json"\n', encoding="utf-8")
    assert _run("-Path", str(bad)).returncode != 0
    ok = tmp_path / "ok.nsh"
    ok.write_text('Delete "$INSTDIR\\resources\\meet_transcriber-*.whl"\n', encoding="utf-8")
    assert _run("-Path", str(ok)).returncode == 0


def test_changed_tauri_template_fails_the_build(tmp_path):
    # Шаблон без переменной ReinstallPageCheck: MeetGuiInit не смог бы
    # выбрать «Обновить до» по умолчанию.
    nsi = tmp_path / "installer.nsi"
    nsi.write_text("Page custom PageReinstall PageLeaveReinstall\n", encoding="utf-8")
    assert _run("-Installer", str(nsi)).returncode != 0
