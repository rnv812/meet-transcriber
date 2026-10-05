"""Детектор звонка на macOS: тот же интерфейс (`Signals`, `mic_busy`,
`render_active`), сигналы — от помощника `meet-audiotap --mic-users`.

Имена программ в настройках — exe Windows; на macOS они переводятся в имена
процессов. Ответ помощника — фикстура tests/fixtures/mac/mic_users.json."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from meet import audiotap, watch

FIXTURES = Path(__file__).parent / "fixtures" / "mac"


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(watch, "_mac_users_cache", None)
    users = audiotap.parse_mic_users((FIXTURES / "mic_users.json").read_text(encoding="utf-8"))
    calls = []

    def fake_mic_users():
        calls.append(1)
        return users

    monkeypatch.setattr(audiotap, "mic_users", fake_mic_users)
    return SimpleNamespace(calls=calls, users=users)


def test_windows_exe_names_map_to_mac_processes():
    assert watch.mac_names("Zoom.exe") == ("zoom.us",)
    assert "MSTeams" in watch.mac_names("ms-teams.exe")
    assert watch.mac_names("Telegram.exe") == ("Telegram",)
    assert watch.mac_names("") == ()
    assert watch.mac_match("zoom.us", "Zoom.exe")
    assert watch.mac_match("Google Chrome Helper (Renderer)", "chrome.exe")
    assert watch.mac_match("Google Chrome", "chrome.exe")
    assert not watch.mac_match("Google Chromecast", "chrome.exe")
    assert not watch.mac_match("", "chrome.exe")
    # Приложение Телемоста — не Яндекс Браузер, хотя оба начинаются с «Yandex».
    assert watch.mac_match("Yandex", "browser.exe")
    assert watch.mac_match("Yandex Helper (Renderer)", "browser.exe")
    assert not watch.mac_match("Yandex Telemost", "browser.exe")
    assert watch.mac_match("Yandex Telemost", "YandexTelemost.exe")


def test_mic_and_playback_come_from_the_helper(mac):
    assert watch.mic_busy("Zoom.exe") is True
    assert watch.render_active("Zoom.exe") is True
    assert watch.mic_busy("chrome.exe") is False  # Chrome только играет звук
    assert watch.render_active("chrome.exe") is True
    assert watch.mic_busy("Teams.exe") is False
    # Один вызов помощника на опрос: ответ живёт MAC_USERS_TTL_S.
    assert len(mac.calls) == 1


def test_helper_cache_expires(mac):
    watch._mac_users(now=100.0)
    watch._mac_users(now=100.5)
    assert len(mac.calls) == 1
    watch._mac_users(now=100.0 + watch.MAC_USERS_TTL_S + 0.1)
    assert len(mac.calls) == 2


def test_no_helper_answer_means_unknown_not_false(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(watch, "_mac_users_cache", None)
    monkeypatch.setattr(audiotap, "mic_users", lambda: None)  # macOS 13, нет помощника
    assert watch.mic_busy("Zoom.exe") is None
    assert watch.render_active("Zoom.exe") is None


def test_signals_merge_on_mac(mac, monkeypatch):
    """Тот же Signals, что на Windows: звонок — микрофон или звук программы,
    пока процесс жив."""
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: name == "Zoom.exe")
    signals = watch.Signals(["Zoom.exe", "Teams.exe"], render_period=0.0)
    assert signals.read(1.0) == (True, True, True)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: False)
    monkeypatch.setattr(watch, "_mac_users_cache", None)
    assert signals.read(2.0)[0] is False  # метка пережила процесс — не звонок


def test_browser_call_needs_the_mic_on_mac(mac, monkeypatch):
    """Chrome только играет звук (видео) — не звонок; браузерный звонок
    начинает только микрофон, как на Windows."""
    monkeypatch.setattr(watch, "browser_pids", lambda name: {777})
    signals = watch.Signals([], browsers=["chrome.exe"], min_mic_s=0.0)
    call, mic, _ = signals.read(1.0)
    assert call is False and signals.browser_call is None


def test_process_names_on_mac_use_the_mapping(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    class Proc:
        def __init__(self, pid, name):
            self.pid, self.info = pid, {"name": name}

    fake = SimpleNamespace(process_iter=lambda attrs: [Proc(1, "zoom.us"),
                                                       Proc(2, "Google Chrome Helper")])
    monkeypatch.setitem(sys.modules, "psutil", fake)
    assert watch.process_running("Zoom.exe") is True
    assert watch.process_running("Teams.exe") is False
    assert watch.browser_pids("chrome.exe") == {2}


def test_window_titles_are_empty_on_mac(monkeypatch):
    """Заголовков окон на macOS нет (нужен доступ к записи экрана другим
    путём) — сайт звонка не определяется, сбоя нет."""
    monkeypatch.setattr(watch, "_USER32", False)
    assert watch.window_titles({1, 2}) == []


def test_native_mac_names_match_case_insensitively(monkeypatch):
    """Имена процессов macOS из настроек (без .exe) сверяются без учёта
    регистра и с «… Helper»."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert watch.mac_names("zoom.us") == ("zoom.us",)
    assert watch.mac_match("zoom.us", "zoom.us")
    assert watch.mac_match("ZOOM.US", "zoom.us")
    assert watch.mac_match("Microsoft Teams", "microsoft teams")
    assert watch.mac_match("MSTeams", "MSTeams")
    assert watch.mac_match("Slack Helper (Renderer)", "Slack")
    assert watch.mac_match("Discord", "discord")
    assert not watch.mac_match("Slackbot", "Slack")
    # имя с «Yandex …» длиннее браузера и принадлежит Телемосту
    assert watch.mac_match("Yandex Telemost", "Yandex Telemost")
    assert not watch.mac_match("Yandex Telemost", "Yandex")


def test_process_running_native_name_on_macos(monkeypatch):
    class _P:
        def __init__(self, name):
            self.info = {"name": name}

    import psutil
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [_P("zoom.us"), _P("Finder")])
    assert watch.process_running("zoom.us") is True
    assert watch.process_running("ZOOM.US") is True
    assert watch.process_running("Telegram") is False
