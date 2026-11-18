"""Секреты meet: токен Hugging Face в диспетчере учётных данных Windows.

Раньше токен лежал открытым текстом в `config.json` (а до того — в переменной
среды). Теперь его место — Credential Manager через `keyring` (служба `meet`,
пользователь `huggingface`): файл настроек можно показывать, пересылать и
класть в бэкап, не думая о секрете.

На macOS токен хранит оболочка Meet.app в своей связке ключей. Связка ключей
привязывает доступ к записи к программе, которая её создала; Meet.app
подписан постоянным сертификатом, а Python движка меняется с каждым
обновлением — чтение из Python открывало бы системное окно «python хочет
доступ…», и задача ждала бы ответа. Оболочка передаёт токен резиденту
переменной `MEET_SHELL_HF_TOKEN` при запуске, а сохранённый в окне —
`POST /hf/token {"store": "shell"}` (`use_shell_token`); задачи наследуют
окружение резидента. `keyring` на Mac остаётся запасным путём (CLI из
Терминала, токен прежних версий) и зовётся с пределом по времени
(`KEYRING_TIMEOUT_S`): не ответил — «токен недоступен», без зависания.

Откуда токен берётся, по порядку:

1. оболочка (`MEET_SHELL_HF_TOKEN`, только macOS) — keyring тогда не зовётся;
2. диспетчер учётных данных;
3. переменные среды `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` — так живут запуски
   CLI, и отбирать этот путь незачем;
4. `integrations.hf_token` в `config.json` — только если диспетчер недоступен
   (сломан бэкенд, нет keyring): тогда токен остаётся в файле и продолжает
   работать, а не теряется.

IMPORTANT: значение токена никогда не попадает ни в журнал, ни в текст
исключения, ни в ответ API — наружу уходит только «есть/нет» и источник.
"""

import os
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from meet import plat

SERVICE = "meet"
USERNAME = "huggingface"
ENV_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
# Токен от оболочки (macOS): его ставит Meet.app при запуске резидента.
# Совпадает с `hf_token::ENV` в app/src-tauri.
SHELL_ENV = "MEET_SHELL_HF_TOKEN"

SHELL = "shell"
KEYRING = "keyring"
ENV = "env"
CONFIG = "config"

# Предел ожидания keyring на macOS: системное окно доступа к связке ключей
# открывает фоновый процесс, и вызов ждёт ответа сколько угодно.
KEYRING_TIMEOUT_S = 5.0


class Unavailable(Exception):
    """Диспетчер учётных данных недоступен. Текст — без значения токена."""


class TimedOut(Unavailable):
    """macOS: связка ключей не ответила в срок (ждёт ответа в системном окне)."""


def _keyring():
    try:
        import keyring
    except Exception as e:  # нет пакета или сломан импорт бэкенда
        raise Unavailable(f"keyring не загружается ({type(e).__name__})") from None
    return keyring


_waiting_lock = threading.Lock()
_waiting: Future | None = None  # вызов keyring, который не ответил в срок


def _timed_out_text() -> str:
    return f"токен недоступен: связка ключей не ответила за {KEYRING_TIMEOUT_S:g} с"


def _bounded(fn, *args):
    """Вызов keyring: на Windows — как есть, на macOS — не дольше
    KEYRING_TIMEOUT_S. Не ответивший вызов остаётся ждать в своём потоке;
    пока он не кончился, новые не начинаются (второе окно связки ключей
    поверх первого ни к чему) — сразу TimedOut."""
    global _waiting
    if not plat.is_macos():
        return fn(*args)
    with _waiting_lock:
        if _waiting is not None and not _waiting.done():
            raise TimedOut(_timed_out_text())
        _waiting = None
    future: Future = Future()

    def run():
        try:
            future.set_result(fn(*args))
        except BaseException as e:  # отдать вызывающему как есть
            future.set_exception(e)

    threading.Thread(target=run, name="meet-keyring", daemon=True).start()
    try:
        return future.result(timeout=KEYRING_TIMEOUT_S)
    except FutureTimeout:
        with _waiting_lock:
            _waiting = future
        raise TimedOut(_timed_out_text()) from None


def keyring_get() -> str | None:
    """Токен из диспетчера; нет записи — None, нет диспетчера — Unavailable
    (на macOS не ответил в срок — TimedOut)."""
    kr = _keyring()
    try:
        value = _bounded(kr.get_password, SERVICE, USERNAME)
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable(f"чтение не удалось ({type(e).__name__})") from None
    return value.strip() if isinstance(value, str) and value.strip() else None


def keyring_set(token: str) -> None:
    """Записать в диспетчер и прочитать обратно. Ошибка бэкенда или запись,
    которая не читается тем же значением (бэкенд молча её потерял), —
    Unavailable (без токена в тексте): тогда копию в config.json стирать
    нельзя."""
    kr = _keyring()
    try:
        _bounded(kr.set_password, SERVICE, USERNAME, token)
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable(f"запись не удалась ({type(e).__name__})") from None
    if keyring_get() != token:
        raise Unavailable("запись не подтвердилась чтением")


def keyring_delete() -> None:
    """Стереть из диспетчера. Нет записи — не ошибка."""
    kr = _keyring()
    from keyring.errors import PasswordDeleteError

    try:
        _bounded(kr.delete_password, SERVICE, USERNAME)
    except PasswordDeleteError:
        return
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable(f"удаление не удалось ({type(e).__name__})") from None


def _from_shell() -> str | None:
    value = os.environ.get(SHELL_ENV)
    return value.strip() if value and value.strip() else None


def _from_env() -> str | None:
    for name in ENV_VARS:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _from_config() -> str | None:
    try:
        from meet import settings

        value = settings.load().integrations.hf_token
    except Exception:
        return None
    return value or None


def lookup() -> tuple[str | None, str | None]:
    """(токен, источник): источник — "shell" | "keyring" | "env" | "config" | None."""
    value = _from_shell()
    if value:
        return value, SHELL
    timed_out = False
    try:
        value = keyring_get()
    except TimedOut:
        value, timed_out = None, True
    except Unavailable:
        value = None
    if value:
        return value, KEYRING
    value = _from_env()
    if value:
        return value, ENV
    value = _from_config()
    if value:
        return value, CONFIG
    # Чтение config.json могло само перенести токен в диспетчер (миграция в
    # settings.load) — или это сделал другой процесс между нашими чтениями.
    # Тогда в файле его уже нет, а в диспетчере есть: смотрим ещё раз.
    # Связка ключей не ответила в первый раз — второй раз не ждём.
    if timed_out:
        return None, None
    try:
        value = keyring_get()
    except Unavailable:
        value = None
    if value:
        return value, KEYRING
    return None, None


def get_hf_token() -> str | None:
    """Токен Hugging Face или None. Единственная точка чтения для всего кода."""
    return lookup()[0]


def hf_token_source() -> str | None:
    """Откуда токен берётся сейчас — для `GET /hf/status`, без самого токена."""
    return lookup()[1]


def set_hf_token(token: str, config=None) -> str:
    """Сохранить токен. Возвращает, куда он лёг: "keyring" или "config".

    В диспетчер — и тогда копия в `config.json`, если была, стирается: старый
    токен не должен пережить новый. Диспетчер недоступен — токен остаётся в
    `config.json` (`config` — путь к файлу, по умолчанию настройки приложения).
    """
    from meet import settings

    token = str(token or "").strip()
    if not token:
        raise ValueError("пустой токен")
    try:
        keyring_set(token)
    except Unavailable:
        settings.write_hf_token(token, config)
        return CONFIG
    settings.keyring_works_again()
    settings.drop_hf_token(config)
    return KEYRING


def use_shell_token(token: str, config=None) -> None:
    """macOS: токен сохранила оболочка в своей связке ключей — резидент держит
    его в своём окружении (`SHELL_ENV`, его наследуют задачи) до следующего
    запуска, где оболочка передаст его сама. keyring Python не трогаем: на
    Mac он и есть то окно, от которого уходим. Копия в `config.json`, если
    была, стирается: старый токен не должен пережить новый."""
    from meet import settings

    token = str(token or "").strip()
    if not token:
        raise ValueError("пустой токен")
    os.environ[SHELL_ENV] = token
    settings.drop_hf_token(config)


def clear_hf_token(config=None) -> None:
    """Забыть токен: от оболочки, в диспетчере и копию в `config.json`.
    Переменные среды `HF_TOKEN` не трогаем — их ставил не meet."""
    from meet import settings

    os.environ.pop(SHELL_ENV, None)
    try:
        keyring_delete()
    except Unavailable:
        pass
    settings.drop_hf_token(config)
