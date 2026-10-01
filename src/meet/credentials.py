"""Секреты meet: токен Hugging Face в диспетчере учётных данных Windows.

Раньше токен лежал открытым текстом в `config.json` (а до того — в переменной
среды). Теперь его место — Credential Manager через `keyring` (служба `meet`,
пользователь `huggingface`): файл настроек можно показывать, пересылать и
класть в бэкап, не думая о секрете.

Откуда токен берётся, по порядку:

1. диспетчер учётных данных;
2. переменные среды `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` — так живут запуски
   CLI, и отбирать этот путь незачем;
3. `integrations.hf_token` в `config.json` — только если диспетчер недоступен
   (сломан бэкенд, нет keyring): тогда токен остаётся в файле и продолжает
   работать, а не теряется.

IMPORTANT: значение токена никогда не попадает ни в журнал, ни в текст
исключения, ни в ответ API — наружу уходит только «есть/нет» и источник.
"""

import os

SERVICE = "meet"
USERNAME = "huggingface"
ENV_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")

KEYRING = "keyring"
ENV = "env"
CONFIG = "config"


class Unavailable(Exception):
    """Диспетчер учётных данных недоступен. Текст — без значения токена."""


def _keyring():
    try:
        import keyring
    except Exception as e:  # нет пакета или сломан импорт бэкенда
        raise Unavailable(f"keyring не загружается ({type(e).__name__})") from None
    return keyring


def keyring_get() -> str | None:
    """Токен из диспетчера; нет записи — None, нет диспетчера — Unavailable."""
    kr = _keyring()
    try:
        value = kr.get_password(SERVICE, USERNAME)
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
        kr.set_password(SERVICE, USERNAME, token)
    except Exception as e:
        raise Unavailable(f"запись не удалась ({type(e).__name__})") from None
    if keyring_get() != token:
        raise Unavailable("запись не подтвердилась чтением")


def keyring_delete() -> None:
    """Стереть из диспетчера. Нет записи — не ошибка."""
    kr = _keyring()
    from keyring.errors import PasswordDeleteError

    try:
        kr.delete_password(SERVICE, USERNAME)
    except PasswordDeleteError:
        return
    except Exception as e:
        raise Unavailable(f"удаление не удалось ({type(e).__name__})") from None


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
    """(токен, источник): источник — "keyring" | "env" | "config" | None."""
    try:
        value = keyring_get()
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


def clear_hf_token(config=None) -> None:
    """Забыть токен: и в диспетчере, и копию в `config.json`. Переменные
    среды не трогаем — их ставил не meet."""
    from meet import settings

    try:
        keyring_delete()
    except Unavailable:
        pass
    settings.drop_hf_token(config)
