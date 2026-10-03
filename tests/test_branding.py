"""Имя приложения — «Meet» везде, где его видит человек; служебное имя
«meet» — там, от чего зависят обновления.

productName в tauri.conf.json остаётся «meet»: из него шаблон NSIS берёт папку
установки (%LOCALAPPDATA%\\meet — она же папка данных), ключ удаления в
реестре, значение автозапуска в Run и имя файла установщика
`meet_<версия>_x64-setup.exe`, который ищут уже установленные 0.2.x
(`updater.rs`, префикс «meet_» сравнивается с учётом регистра). Поэтому имя в
окнах установщика задаёт `LangString ^Name` языковых файлов, а подпись в
«Параметры → Приложения» — хук после установки.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "app" / "src-tauri"
LANGS = {"Russian": "LANG_RUSSIAN", "English": "LANG_ENGLISH"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_service_name_stays_lowercase_for_upgrades():
    conf = json.loads(_text(TAURI / "tauri.conf.json"))
    assert conf["productName"] == "meet"
    assert conf["identifier"] == "com.meet.desktop"
    assert conf["bundle"]["publisher"] == "meet"
    updater = _text(TAURI / "src" / "updater.rs")
    assert 'const INSTALLER_PREFIX: &str = "meet_";' in updater
    build = _text(ROOT / "scripts" / "build_release.ps1")
    assert '$installerName = "meet_${Version}_x64-setup.exe"' in build


def test_installer_windows_say_meet():
    for name, lang in LANGS.items():
        text = _text(TAURI / "windows" / "lang" / f"{name}.nsh")
        assert f'LangString ^Name ${{{lang}}} "Meet"' in text, name
        assert f'LangString ^NameDA ${{{lang}}} "Meet"' in text, name
        strings = [line for line in text.splitlines() if line.startswith("LangString ")]
        # Имя — буквой: ${PRODUCTNAME} и {{product_name}} дали бы «meet».
        for line in strings:
            assert "${PRODUCTNAME}" not in line and "{{product_name}}" not in line, line
            assert not re.search(r"(?<![\w$])meet(?![\w])", line.split('"', 1)[1]), line
    russian = _text(TAURI / "windows" / "lang" / "Russian.nsh")
    for phrase in ('"Meet уже установлен"', '"Удалить Meet (данные останутся в папке)"',
                   '"Meet всё ещё работает.', '"Закрываю Meet: идущая запись сохраняется..."'):
        assert phrase in russian, phrase
    english = _text(TAURI / "windows" / "lang" / "English.nsh")
    assert '"Meet is already installed"' in english


def test_apps_list_shows_meet():
    hooks = _text(TAURI / "windows" / "hooks.nsh")
    post = hooks.split("!macro NSIS_HOOK_POSTINSTALL", 1)[1].split("!macroend", 1)[0]
    assert 'WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "Meet"' in post


def _macro(text: str, name: str) -> str:
    return text.split(f"!macro {name}\n", 1)[1].split("!macroend", 1)[0]


def test_shortcuts_and_notifications_say_meet():
    """Уведомления Windows подписаны именем ярлыка в «Пуске» с тем же AUMID,
    а шаблон Tauri называет ярлыки ${PRODUCTNAME}.lnk = «meet.lnk». Хук
    меняет только регистр имени — и после секции установки, и после страницы
    «Готово» (ярлык на рабочем столе по её галочке)."""
    hooks = _text(TAURI / "windows" / "hooks.nsh").replace("\r\n", "\n")
    one = _macro(hooks, "MEET_SHORTCUT_CASE dir")
    # только наш ярлык, только переименование, имя — «Meet.lnk»
    assert ('!insertmacro IsShortcutTarget "${dir}\\${PRODUCTNAME}.lnk" '
            '"$INSTDIR\\${MAINBINARYNAME}.exe"') in one
    assert '${AndIf} $1 S!= "Meet.lnk"' in one
    assert 'Rename "${dir}\\$1" "${dir}\\Meet.lnk"' in one
    for verb in ("Delete", "CreateShortcut", "RMDir", "SetLnkAppUserModelId"):
        assert verb not in one, verb
    both = _macro(hooks, "MEET_SHORTCUTS_CASE")
    assert '!insertmacro MEET_SHORTCUT_CASE "$SMPROGRAMS"' in both
    assert '!insertmacro MEET_SHORTCUT_CASE "$DESKTOP"' in both
    # регистры, которые портит IsShortcutTarget шаблона, возвращаются
    stack = [line.strip() for line in both.splitlines()
             if line.strip().startswith(("Push", "Pop"))]
    assert stack == ["Push $0", "Push $1", "Push $2", "Push $3",
                     "Pop $3", "Pop $2", "Pop $1", "Pop $0"]
    post = _macro(hooks, "NSIS_HOOK_POSTINSTALL")
    assert "!insertmacro MEET_SHORTCUTS_CASE" in post
    functions = _macro(hooks, "MEET_UPGRADE_FUNCTIONS")
    gui_end = functions.split("Function .onGUIEnd", 1)[1].split("FunctionEnd", 1)[0]
    assert "!insertmacro MEET_SHORTCUTS_CASE" in gui_end
    # AUMID тостов — identifier: его ставят и плагин уведомлений, и ярлык.
    conf = json.loads(_text(TAURI / "tauri.conf.json"))
    assert conf["identifier"] == "com.meet.desktop"


def test_window_titles_and_shell_texts():
    assert "<title>Meet</title>" in _text(ROOT / "app" / "index.html")
    assert "<title>Meet — ассистент</title>" in _text(ROOT / "app" / "live.html")
    assert '.title("Meet")' in _text(TAURI / "src" / "windows.rs")
    assert '.title("Meet — ассистент")' in _text(TAURI / "src" / "live_panel.rs")
    tray = _text(TAURI / "src" / "tray.rs")
    assert 'format!("Meet · {text}")' in tray
    assert '"meet — ' not in tray
    upgrade = _text(TAURI / "src" / "upgrade.rs")
    assert '"Meet обновлён до "' in upgrade


def test_authors_have_full_names():
    readme = _text(ROOT / "README.md")
    assert "**Андрей Сивуха** — архитектура десктопного приложения ([@ndrsvh]" in readme
    notice = _text(ROOT / "NOTICE")
    assert "Andrey Sivukha (ndrsvh) — desktop application architecture" in notice
    about = _text(ROOT / "app" / "src" / "features" / "settings" / "About.tsx")
    assert '"Андрей Сивуха (@ndrsvh)"' in about
