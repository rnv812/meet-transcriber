"""Параметры запуска агента во вкладке «Агент» (настройки `agent.launch`).

Те же правила, что у оболочки (`pty.rs`: `parse_launch_args`) и окна
(`app/src/lib/agentLaunch.ts`): строка параметров разбирается в отдельные
аргументы без командной оболочки, переменные окружения — пары ИМЯ=значение.
Здесь — только проверка того, что сохраняется в настройки; запускает агента
оболочка.

Разбор параметров (удобный для путей Windows):
* разделители — пробел и табуляция;
* «"…"» и «'…'» объединяют текст с пробелами; кавычки примыкают к соседнему
  тексту (`--x="a b"` → `--x=a b`); `""` — пустой аргумент;
* обратная косая черта — обычный символ (`D:\\Docs`, `\\\\server\\share`);
  только внутри двойных кавычек `\\"` — сама кавычка;
* управляющие символы (кроме табуляции-разделителя) недопустимы.
"""

import re

PROVIDERS = ("claude-code", "codex")

ARGS_CONTROL = "Недопустимый управляющий символ в параметрах запуска"
ARGS_QUOTE = ("Незакрытая кавычка в параметрах запуска (обратная косая черта перед "
              "кавычкой \\\" считается частью текста — уберите её в конце пути)")
ENV_NAME = "Переменная окружения «{key}»: недопустимое имя (латинские буквы, цифры и _, не с цифры)"
ENV_CONTROL = "Переменная окружения «{key}»: управляющие символы в значении недопустимы"
ENV_TWICE = "Переменная окружения «{key}» задана дважды"
ENV_SHAPE = "Переменные окружения — список пар {key, value}"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _control(ch: str) -> bool:
    return ord(ch) < 0x20 or ord(ch) == 0x7F


def parse_args(text: str) -> list[str]:
    """Строка параметров → аргументы; ValueError с текстом для человека."""
    args: list[str] = []
    cur: str | None = None
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if _control(ch) and not (ch == "\t" and quote is None):
            raise ValueError(ARGS_CONTROL)
        if quote is None:
            if ch in " \t":
                if cur is not None:
                    args.append(cur)
                    cur = None
            elif ch in "\"'":
                quote = ch
                cur = cur or ""
            else:
                cur = (cur or "") + ch
        elif quote == "'":
            if ch == "'":
                quote = None
            else:
                cur += ch
        elif ch == "\\" and text[i + 1:i + 2] == "\"":
            cur += "\""
            i += 1
        elif ch == "\"":
            quote = None
        else:
            cur += ch
        i += 1
    if quote is not None:
        raise ValueError(ARGS_QUOTE)
    if cur is not None:
        args.append(cur)
    return args


def env_error(entries) -> str | None:
    """Ошибка в списке переменных [{key, value}] или None."""
    if not isinstance(entries, list):
        return ENV_SHAPE
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) \
                or not isinstance(item.get("value", ""), str):
            return ENV_SHAPE
        key, value = item["key"], item.get("value", "")
        if not _NAME.fullmatch(key):
            return ENV_NAME.format(key=key)
        if any(_control(ch) for ch in value):
            return ENV_CONTROL.format(key=key)
        if key.upper() in seen:
            return ENV_TWICE.format(key=key)
        seen.add(key.upper())
    return None


def launch_error(launch) -> str | None:
    """Ошибка в `agent.launch` из окна: {провайдер: {args, env}} или None."""
    if not isinstance(launch, dict):
        return "Параметры запуска агента — объект по провайдерам"
    for name, item in launch.items():
        if name not in PROVIDERS:
            return f"Неизвестный агент: {name}"
        if not isinstance(item, dict):
            return f"Параметры запуска {name} — объект {{args, env}}"
        args = item.get("args", "")
        if not isinstance(args, str):
            return "Дополнительные параметры — строка"
        try:
            parse_args(args)
        except ValueError as e:
            return str(e)
        error = env_error(item.get("env", []))
        if error:
            return error
    return None
