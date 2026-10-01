"""Токен Hugging Face: диспетчер учётных данных, запасные пути, проверка доступа.

Настоящий диспетчер не трогается: conftest подменяет keyring in-memory
бэкендом на каждый тест.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meet import credentials, models, paths, settings

TOKEN = "hf_TEST_secret_value_123"


class _FailingKeyring:
    """Бэкенд, который падает на всём, — как недоступный диспетчер."""

    def __new__(cls):
        from keyring.backend import KeyringBackend
        from keyring.errors import KeyringError

        class Failing(KeyringBackend):
            priority = 1

            def get_password(self, service, username):
                raise KeyringError("диспетчер недоступен")

            def set_password(self, service, username, password):
                raise KeyringError("диспетчер недоступен")

            def delete_password(self, service, username):
                raise KeyringError("диспетчер недоступен")

        return Failing()


@pytest.fixture
def failing_keyring():
    import keyring

    keyring.set_keyring(_FailingKeyring())  # conftest восстановит прежний


def _config(data: dict) -> None:
    target = paths.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _raw_config() -> dict:
    return json.loads(paths.config_path().read_text(encoding="utf-8"))


def _watch_log() -> str:
    from meet import watch

    path = watch.default_log_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- хранение ---------------------------------------------------------------


def test_set_get_clear_roundtrip(memory_keyring):
    assert credentials.get_hf_token() is None
    assert credentials.set_hf_token(TOKEN) == "keyring"
    assert memory_keyring.store[("meet", "huggingface")] == TOKEN
    assert credentials.get_hf_token() == TOKEN
    assert credentials.hf_token_source() == "keyring"
    credentials.clear_hf_token()
    assert credentials.get_hf_token() is None
    assert credentials.hf_token_source() is None
    credentials.clear_hf_token()  # повторная очистка — не ошибка


def test_set_strips_whitespace(memory_keyring):
    credentials.set_hf_token(f"  {TOKEN}\n")
    assert credentials.get_hf_token() == TOKEN


def test_env_fallback(monkeypatch):
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_from_hub_env")
    assert credentials.get_hf_token() == "hf_from_hub_env"
    assert credentials.hf_token_source() == "env"
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    assert credentials.get_hf_token() == "hf_from_env"


def test_keyring_wins_over_env(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    credentials.set_hf_token(TOKEN)
    assert credentials.get_hf_token() == TOKEN


def test_failing_keyring_keeps_token_in_config(failing_keyring):
    """Диспетчер недоступен — токен ложится в config.json и продолжает работать."""
    assert credentials.set_hf_token(TOKEN) == "config"
    assert _raw_config()["integrations"]["hf_token"] == TOKEN
    assert credentials.get_hf_token() == TOKEN
    assert credentials.hf_token_source() == "config"
    credentials.clear_hf_token()
    assert "hf_token" not in _raw_config().get("integrations", {})
    assert credentials.get_hf_token() is None


def test_saving_to_keyring_drops_stale_config_copy(memory_keyring):
    """Старая копия в config.json не должна пережить новый токен."""
    _config({"integrations": {"hf_token": "hf_old", "gpu_marker": False}})
    credentials.set_hf_token(TOKEN)
    raw = _raw_config()
    assert "hf_token" not in raw["integrations"]
    assert raw["integrations"]["gpu_marker"] is False


# --- миграция из config.json --------------------------------------------------


def test_migration_moves_token_and_removes_field(memory_keyring):
    _config({"integrations": {"hf_token": TOKEN, "gpu_marker": False},
             "recording": {"speaker_name": "Алексей"}})
    cfg = settings.load()
    assert memory_keyring.store[("meet", "huggingface")] == TOKEN
    raw = _raw_config()
    assert "hf_token" not in raw["integrations"]
    assert raw["integrations"]["gpu_marker"] is False
    assert raw["recording"]["speaker_name"] == "Алексей"  # остальное не тронуто
    assert cfg.recording.speaker_name == "Алексей"
    log = _watch_log()
    assert "токен HF перенесён в диспетчер учётных данных" in log
    assert TOKEN not in log
    assert credentials.get_hf_token() == TOKEN
    # повторная загрузка ничего не переносит и не пишет второй строки
    settings.load()
    assert _watch_log().count("перенесён") == 1


def test_migration_skips_explicit_foreign_path(memory_keyring, tmp_path):
    """Чужой файл настроек (тест, утилита) — не источник секретов приложения."""
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"integrations": {"hf_token": TOKEN}}), encoding="utf-8")
    settings.load(other)
    assert memory_keyring.store == {}
    assert json.loads(other.read_text(encoding="utf-8"))["integrations"]["hf_token"] == TOKEN


def test_migration_with_failing_keyring_leaves_config(failing_keyring):
    _config({"integrations": {"hf_token": TOKEN}})
    settings.load()
    settings.load()
    assert _raw_config()["integrations"]["hf_token"] == TOKEN
    log = _watch_log()
    assert log.count("диспетчер учётных данных недоступен — токен остаётся в config.json") == 1
    assert TOKEN not in log
    assert credentials.get_hf_token() == TOKEN
    assert credentials.hf_token_source() == "config"


def test_settings_never_expose_token(failing_keyring):
    """Даже когда токен остался в config.json, GET /settings его не отдаёт."""
    _config({"integrations": {"hf_token": TOKEN}})
    assert TOKEN not in json.dumps(settings.load().to_raw(), ensure_ascii=False)


def test_patch_with_token_goes_to_keyring(memory_keyring):
    """Прежнее окно настроек шлёт токен через PATCH /settings — он уходит в
    диспетчер, а не в файл."""
    settings.patch({"integrations": {"hf_token": TOKEN, "gpu_marker": False}})
    assert memory_keyring.store[("meet", "huggingface")] == TOKEN
    raw = _raw_config()
    assert "hf_token" not in raw["integrations"]
    assert raw["integrations"]["gpu_marker"] is False
    settings.patch({"integrations": {"hf_token": ""}})
    assert memory_keyring.store == {}


# --- проверка доступа -------------------------------------------------------


class _FakeHub:
    """Локальный huggingface.co: whoami и HEAD на config.yaml гейтед-модели."""

    def __init__(self, whoami=200, resolve=200):
        self.whoami, self.resolve = whoami, resolve
        self.seen: list[tuple[str, str, str]] = []
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body=b""):
                hub.seen.append((self.command, self.path,
                                 self.headers.get("Authorization", "")))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body and self.command != "HEAD":
                    self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/whoami-v2":
                    self._reply(hub.whoami, b'{"name": "someone"}')
                else:
                    self._reply(404)

            def do_HEAD(self):
                if self.path == ("/pyannote/speaker-diarization-community-1"
                                 "/resolve/main/config.yaml"):
                    self._reply(hub.resolve)
                else:
                    self._reply(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hub():
    made = []

    def make(**kw):
        made.append(_FakeHub(**kw))
        return made[-1]

    yield make
    for h in made:
        h.close()


def test_check_ok(hub):
    fake = hub()
    result = models.check_hf_access(TOKEN, base_url=fake.url)
    assert result == {"ok": True, "reason": "ok", "message": "Доступ есть"}
    assert ("GET", "/api/whoami-v2", f"Bearer {TOKEN}") in fake.seen
    assert any(m == "HEAD" for m, _, _ in fake.seen)


def test_check_invalid_token(hub):
    result = models.check_hf_access(TOKEN, base_url=hub(whoami=401).url)
    assert result["ok"] is False and result["reason"] == "invalid_token"
    assert result["message"] == "Неверный токен"


@pytest.mark.parametrize("status", [403, 401])
def test_check_terms_not_accepted(hub, status):
    result = models.check_hf_access(TOKEN, base_url=hub(resolve=status).url)
    assert result["ok"] is False and result["reason"] == "terms_not_accepted"
    assert result["message"] == ("Условия модели не приняты — откройте страницу модели "
                                 "и нажмите «Agree and access repository»")


def test_check_redirect_counts_as_access_and_is_not_followed(hub):
    """resolve отвечает редиректом на CDN — доступ есть; токен туда не уходит."""
    fake = hub(resolve=302)
    assert models.check_hf_access(TOKEN, base_url=fake.url)["reason"] == "ok"


def test_check_network_error():
    import socket

    with socket.socket() as s:  # свободный порт, на котором никто не слушает
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = models.check_hf_access(TOKEN, base_url=f"http://127.0.0.1:{port}")
    assert result == {"ok": False, "reason": "network",
                      "message": "Нет связи с huggingface.co"}


def test_check_never_leaks_token(hub):
    for kw in ({}, {"whoami": 401}, {"resolve": 403}, {"whoami": 500}):
        result = models.check_hf_access(TOKEN, base_url=hub(**kw).url)
        assert TOKEN not in json.dumps(result, ensure_ascii=False)


def test_check_rejects_malformed_token_without_network():
    """Перевод строки в токене — инъекция заголовка; ошибка urllib вывела бы
    токен в текст исключения. Отказ до сети."""
    result = models.check_hf_access("hf_abc\r\nX: y", base_url="http://127.0.0.1:9")
    assert result["reason"] == "invalid_token"
    assert "hf_abc" not in json.dumps(result, ensure_ascii=False)


def test_check_timeout_is_ten_seconds():
    assert models.HF_CHECK_TIMEOUT_S == 10


def test_models_state_sees_keyring_token():
    assert models.state()["token"] is False
    credentials.set_hf_token(TOKEN)
    assert models.state()["token"] is True
