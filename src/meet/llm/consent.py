"""Согласие пользователя на действия агента-участника (0.3.7, A1; 0.4 — «как CLI»).

Кто просил, решает Meet (классификатор автомода реплики встречи отличить от
просьбы не может). Уровни хода (`Participant.consent_level`):

* ``NONE`` — ход без просьбы пользователя: только реплики встречи, 👍/👎,
  «Не надо», повтор после сбоя. Можно только читать рабочие папки и то, что
  пользователь приложил к чату. Всё остальное — отказ «спроси пользователя
  с кнопками» (`ask_text`), что бы ни прозвучало на встрече.
* ``READ`` — ❓ «Поясни»: чтение где угодно, веб-поиск, MCP-чтение, простые
  команды чтения (`shell_plan`) — без карточек; остальное — отказ «предложи
  кнопками» (`ASK_FIRST_READ`).
* ``USER`` — сообщение пользователя, кнопка агента (кроме отказа,
  `click_level`), слэш-команда. Режим `auto` (0.4): Meet отдаёт решение CLI
  (исход ``auto`` — хук отвечает `{}`, дальше правила пользователя и
  классификатор автомода), а **карточкой Meet** (исход ``ask``) спрашивает
  только рискованное: удаление (`rm`, `Remove-Item`, `git clean`, `git reset
  --hard`…), запись вне рабочих папок, отправку наружу (`git push`, `scp`,
  `curl -d`…), команды без песочницы, MCP-изменения и MCP с адресом в
  аргументах. Режим `confirm` — как 0.3.7: карточка на каждое действие.

Рабочие папки (`own_dirs` — папка записи, `work_dirs` — база знаний,
служебная `cwd` и папки, где пользователь разрешил запись «до конца
встречи»). Библиотека встреч читается, а запись в неё вне своей папки идёт
карточкой.

**Всегда запрещено:** закрытые папки (`kb_exclude`), чувствительные пути
(`sensitive_paths`: ключи SSH и облаков, настройки Claude Code и Codex,
профили браузеров, хранилища паролей ОС, служебная папка Meet с его токеном,
`.env` вне встречи), фоновое выполнение (подагенты Task/Agent, Bash с
`run_in_background`, расписания), обращения к локальным адресам (API Meet),
скрытые символы, огромные вызовы, свой вопрос CLI (`AskUserQuestion`).

`ConsentGate.decide(tool, input)` — решение без ожидания (`allow` / `auto` /
`deny` / `ask`); `ConsentGate.check(...)` — то же с карточкой: им отвечает
`claude_stream.Conversation` на хук PreToolUse (он приходит на **каждый**
вызов — проверено на claude 2.1.292 без модели) и на `can_use_tool` (вопрос
самого CLI — в ходе USER всегда карточка). Исходы `check`: `allow` (хук —
`permissionDecision: "allow"`: Meet или человек решили), `auto` (хук — `{}`),
`deny`. Codex и OpenCode обратного вызова не имеют: строки `ask` у них —
отказ (`codex.access_args`, `opencode.permission_for`).

Модуль — только stdlib.
"""

import ipaddress
import json
import ntpath
import os
import posixpath
import re
import shlex
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

NONE, READ, USER = "none", "read", "user"
LEVELS = (NONE, READ, USER)
_RANK = {NONE: 0, READ: 1, USER: 2}
# `auto` — отдать решение CLI (его правила и классификатор автомода).
ALLOW, AUTO, DENY, ASK = "allow", "auto", "deny", "ask"
# Как действует ассистент в ходе USER (`assist.agent_mode`).
MODE_AUTO, MODE_CONFIRM = "auto", "confirm"
MODES = (MODE_AUTO, MODE_CONFIRM)
ALLOW_MEETING = "allow_meeting"      # «Разрешать такое до конца встречи»
# Ключ разрешения на правки файлов в папке (Write, Edit, MultiEdit, NotebookEdit).
FILES_GRANT = "write:files:"

# Сколько ждать решения по карточке (ответ хука CLI всё это время держится).
CONFIRM_TIMEOUT_S = 120.0
# Технический предел против злоупотреблений (fix round 3: обычные длинные
# вызовы идут карточкой; показанное никогда не обрезается относительно
# выполняемого — длинное показывается началом и концом, середина — пометкой).
ARGS_LIMIT = 100_000
# Карточка целиком — до стольких строк и знаков; длиннее — начало и конец.
CARD_FULL_LINES = 12
CARD_FULL_CHARS = 1_200
# Пробелов подряд — не больше; длиннее — видимой пометкой «⟨N пробелов⟩».
SPACES_SHOWN = 8

# Отказ в ходе без просьбы (NONE); свой текст — параметр `ask_text` ворот
# (у профиля «Личный» — без «встречи», `participant_prompts.ASK_FIRST_PERSONAL`).
ASK_FIRST = ("Meet заблокировал: {what} — без просьбы пользователя. Сейчас ход по репликам встречи: "
             "без его просьбы можно только читать рабочие папки (эту встречу, базу знаний) и то, что он "
             "добавил в чат, — ничего не менять и не выполнять. "
             "Спроси пользователя с кнопками, прежде чем это делать (например: "
             "{{\"say\": \"Я гляну …?\", \"buttons\": [\"Да, глянь\", \"Не надо\"]}}), "
             "и сделай после его согласия. Не пытайся обойти запрет другим инструментом.")
# Отказ в ходе по ❓ «Поясни» (READ): карточек нет — только чтение.
ASK_FIRST_READ = ("Meet заблокировал: {what} — сейчас ход по «Поясни»: можно только читать (файлы, "
                  "веб-поиск, MCP на чтение, простые команды чтения). Если для ответа нужно это действие, "
                  "предложи его пользователю с кнопками (например: "
                  "{{\"say\": \"Сделать …?\", \"buttons\": [\"Да, сделай\", \"Не надо\"]}}) "
                  "и сделай после его согласия. Не пытайся обойти запрет другим инструментом.")
EXCLUDED =("Meet заблокировал: {what} — эта папка закрыта настройками пользователя "
            "(«Не показывать ассистенту»). Не открывай и не ищи там, в том числе командами.")
SENSITIVE = ("Meet заблокировал: {what} — это закрытые данные (ключи, пароли, настройки программ, "
             "служебные файлы Meet). Их ассистенту не открыть никак.")
BACKGROUND = ("Meet заблокировал: {what} — фоновое выполнение (подагенты, фоновые команды, расписания) "
              "ассистенту недоступно: делай всё сам и в этом ходе.")
LOCAL = ("Meet заблокировал: {what} — обращения к локальным адресам (127.0.0.1, localhost) и к файлам "
         "через веб закрыты.")
NO_QUESTIONS = ("Этот инструмент не для тебя: спрашивай пользователя только сообщением "
                "{\"say\": …, \"buttons\": […]}.")
DECLINED = ("Пользователь отклонил: {what}. Не повторяй этот вызов; если нужно — коротко спроси, "
            "что сделать иначе.")
TIMED_OUT = ("Пользователь не ответил на запрос Meet за {minutes} мин: {what} — не выполнено. "
             "Не повторяй вызов сам; скажи, что ждёшь подтверждения.")
NO_CARD = ("Meet заблокировал: {what} — на это нужно подтверждение пользователя в карточке Meet, "
           "а показать её сейчас негде. Опиши, что хочешь сделать, и подожди.")
TOO_LONG = ("Meet заблокировал: {what} — вызов огромный ({size} симв., предел {limit}). "
            "Разбей на части покороче.")
HIDDEN_CHARS = ("Meet заблокировал: {what} — в вызове скрытые символы ({chars}): управляющие, нулевой "
                "ширины или смены направления текста. Такой вызов не показать человеку честно — убери их.")
UNSEEN = ("Meet заблокировал: {what} — вызов не прошёл проверку согласия Meet (обновите Meet или "
          "Claude Code).")

# --- виды инструментов ---------------------------------------------------------------

FILE_READ = {"Read", "Grep", "Glob", "LS", "NotebookRead"}
FILE_WRITE = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
SHELL = {"Bash", "PowerShell"}
WEB_SEARCH = {"WebSearch"}
WEB_FETCH = {"WebFetch"}
# Фоновое выполнение и то, что переживает ход, — нельзя никогда.
BACKGROUND_TOOLS = {"Agent", "Task", "BashOutput", "KillShell", "KillBash", "CronCreate", "CronDelete",
                    "ScheduleWakeup", "Workflow", "EnterPlanMode", "ExitPlanMode", "EnterWorktree",
                    "ExitWorktree", "SendMessage", "TaskStop"}
# Свои служебные: ничего не читают и не меняют вне сеанса.
INTERNAL = {"TodoWrite", "TodoRead", "ToolSearch"}
MCP_RESOURCES = {"ListMcpResourcesTool", "ListMcpResources", "ReadMcpResourceTool", "ReadMcpResource"}
QUESTION_TOOLS = {"AskUserQuestion"}

# MCP без карточки: имя (после одного префикса — части имени сервера)
# НАЧИНАЕТСЯ с глагола чтения и нигде не содержит глагола изменения.
# MCP без карточки — инструмент, в имени которого нет глагола изменения
# (fix round 3). `fetch` — глагол изменения: MCP-«fetch» ходит по любому адресу.
MCP_WRITE_VERBS = frozenset((
    "create", "update", "delete", "remove", "set", "add", "post", "put", "patch", "write", "send",
    "merge", "close", "move", "rollover", "purge", "run", "exec", "execute", "start", "stop", "assign",
    "transition", "approve", "publish", "upload", "edit", "insert", "drop", "kill", "cancel", "reset",
    "clear", "sync", "apply", "deploy", "import", "install", "register", "unregister", "restart",
    "restore", "archive", "rename", "reindex", "refresh", "shrink", "upsert", "download", "trigger",
    "submit", "save", "modify", "change", "grant", "revoke", "invite", "lock", "unlock", "fork",
    "protect", "unprotect", "link", "unlink", "mark", "comment", "watch", "unwatch", "enable",
    "disable", "generic", "duplicate", "batch", "replace", "unapprove", "resolve", "reopen", "tag",
    "log", "copy", "transfer", "pay", "order", "book", "schedule", "invoke", "call", "query",
    "push", "commit", "ack", "store", "notify", "subscribe", "share", "reply", "label", "persist",
    "emit", "accept", "reject", "fetch", "request", "forward", "export",
))

# Имена, которые резолвятся в 127.0.0.1 (публичные «локальные» домены).
_LOOPBACK_NAMES = ("localhost", "localtest.me", "lvh.me", "vcap.me", "lacolhost.com", "localho.st",
                   "yoogle.com", "fbi.com", "nip.io", "sslip.io", "xip.io")
# Хост-подобные слова в тексте команды или аргумента (с портом или без).
_HOSTISH = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?(?:[^\s/@'\"`]*@)?(\[[0-9a-f:.]+\]|[\w.-]+)(?::\d+)?",
                      re.IGNORECASE)
_ENV_IN_TEXT = re.compile(r"(^|[\s/\"'=])\.env(\.[\w-]+)?(?![\w.])")


_NEGATIVE = re.compile(r"^\s*(не\b|нет\b|отмен|стоп\b|пропусти|позже|потом|хватит|no\b|cancel|skip)",
                       re.IGNORECASE)


def click_level(label: str) -> str:
    """Согласие от нажатой кнопки агента: «Не надо», «Нет», «Позже» — NONE;
    остальное — USER (нажал человек: его просьба, как сообщение).
    Рискованное (удаление, запись вне рабочих папок, отправка наружу) всё
    равно идёт карточкой Meet по каждому вызову."""
    text = " ".join(str(label or "").split())
    return NONE if not text or _NEGATIVE.match(text) else USER


def _ipv4_legacy(text: str) -> int | None:
    """IPv4 в формах inet_aton: `127.1`, `2130706433`, `0x7f.1`, `0177.0.0.1`."""
    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or not all(parts):
        return None
    nums = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                nums.append(int(part, 16))
            elif len(part) > 1 and part.startswith("0"):
                nums.append(int(part, 8))
            else:
                nums.append(int(part, 10))
        except ValueError:
            return None
    *head, last = nums
    if any(n > 255 for n in head) or last >= 256 ** (5 - len(nums)):
        return None
    value = 0
    for i, n in enumerate(head):      # первые части — старшие байты
        value += n * 256 ** (3 - i)
    return value + last


def is_local_host(host: str) -> bool:
    """Хост — этот компьютер (или служебный адрес): loopback в любой записи
    IPv4/IPv6, `0.0.0.0`, link-local (169.254.x.x — метаданные облаков),
    `localhost` (и с точкой в конце), `*.localhost` и публичные «локальные»
    имена (`localtest.me`, `lvh.me`…). DNS не спрашивается (дорого и
    медленно) — эти имена известны заранее."""
    h = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not h:
        return False
    if any(h == n or h.endswith("." + n) for n in _LOOPBACK_NAMES) or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        value = _ipv4_legacy(h)
        if value is None:
            return False
        ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified or ip.is_link_local


def mentions_local(text: str) -> bool:
    """В тексте есть адрес этого компьютера: имя (`localhost`, `lvh.me`…),
    обычный IP (`127.0.0.1`, `[::1]`, `0.0.0.0`, `169.254.169.254`) — везде;
    короткие и числовые формы IPv4 (`127.1`, `2130706433`, `0x7f.1`) — только
    в адресе (со схемой `://` или портом), иначе это просто числа."""
    for m in _HOSTISH.finditer(str(text or "")):
        token, host = m.group(0), m.group(1)
        low = host.strip("[]").rstrip(".").lower()
        if not low:
            continue
        addressed = "://" in token or re.search(r":[0-9]+$", token) is not None
        try:
            ipaddress.ip_address(low)
            canonical = True
        except ValueError:
            canonical = False
        named = not re.fullmatch(r"[0-9a-fx.]+", low)
        if (canonical or named or addressed) and is_local_host(low):
            return True
    return False


# --- скрытые символы и показ вызова (fix round 2, R1, R2) ----------------------------------

_ALLOWED_CONTROLS = {"\n", "\t", "\r"}
# Невидимые или похожие на пробел символы, которых нет в категориях C*/Z*
# (Unicode Default_Ignorable_Code_Point, пустые хангыль и брайль; ревью N1).
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)


def _invisible(cp: int) -> bool:
    return any(a <= cp <= b for a, b in _INVISIBLE_RANGES)


def hidden_chars(text: str, *, shell: bool = False) -> list[str]:
    """Скрытые символы: управляющие (кроме перевода строки, табуляции,
    возврата каретки), форматирующие (нулевой ширины, смены направления —
    Trojan Source), разделители строк и абзацев, любые пробелы кроме обычного
    (неразрывный — только вне команд), невидимые по Unicode
    (Default_Ignorable), пустой брайль, хангыль-заполнители. → «U+202E»…"""
    found: list[str] = []
    for ch in str(text or ""):
        if ch in _ALLOWED_CONTROLS or ch == " ":
            continue
        cp = ord(ch)
        cat = unicodedata.category(ch)
        bad = (cat in ("Cc", "Cf", "Zl", "Zp", "Co", "Cn") or _invisible(cp)
               or (cat == "Zs" and (cp != 0x00A0 or shell)))
        if bad and f"U+{cp:04X}" not in found:
            found.append(f"U+{cp:04X}")
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


# Знаки пометок карточки: в самом вызове они экранируются (`\u21B5`), чтобы
# настоящую пометку нельзя было подделать (ревью N4).
_MARKER_GLYPHS = {"↵": "\\u21B5", "⇥": "\\u21E5", "␍": "\\u240D", "⟨": "\\u27E8", "⟩": "\\u27E9"}


def display_text(text: str, *, shell: bool = False) -> str:
    """Вызов для карточки — один на оба окна (резидент отдаёт уже готовый
    текст). Знаки пометок из самого вызова — экранированы; длинные пробелы —
    «⟨N пробелов⟩», табуляции — «⟨N табуляций⟩»; в команде — перевод строки
    «↵», табуляция «⇥», возврат каретки «␍», три и больше пустых строк подряд
    — «⟨N пустых строк⟩». Ничего не выбрасывается: всё, что выполнится, видно."""
    text = "".join(_MARKER_GLYPHS.get(ch, ch) for ch in str(text or ""))
    text = re.sub(r"\t{4,}", lambda m: f"⟨{len(m.group(0))} табуляций⟩", text)
    if shell:
        text = re.sub(r"(?:[ \t]*\r?\n){3,}", lambda m: f"↵⟨{m.group(0).count(chr(10))} пустых строк⟩↵\n",
                      text)
        text = text.replace("\r", "␍").replace("\t", "⇥").replace("\n", "↵\n")
    return re.sub(r" {%d,}" % (SPACES_SHOWN + 1), lambda m: f" ⟨{len(m.group(0))} пробелов⟩ ", text)


def card_preview(args: str) -> str | None:
    """Длинный вызов в карточке — начало и конец с пометкой посередине
    (None — короткий, показывается целиком). Пробелы уже свёрнуты пометками,
    невидимые символы запрещены — середина не может спрятать хвост."""
    lines = args.split("\n")
    if len(lines) <= CARD_FULL_LINES and len(args) <= CARD_FULL_CHARS:
        return None
    head = "\n".join(lines[:6])[:500]
    tail = "\n".join(lines[-4:])[-400:]
    hidden_chars_n = len(args) - len(head) - len(tail)
    hidden_lines = max(0, args[len(head):len(args) - len(tail)].count("\n") - 1)
    return f"{head}\n…⟨скрыто: {hidden_lines} строк, {max(hidden_chars_n, 0)} симв.⟩…\n{tail}"


def _size(text: str) -> str:
    lines = text.count("\n") + 1
    word = "строка" if lines % 10 == 1 and lines % 100 != 11 else (
        "строки" if 2 <= lines % 10 <= 4 and not 12 <= lines % 100 <= 14 else "строк")
    return f"{lines} {word}, {len(text)} симв."


def higher(a: str, b: str) -> str:
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def _words(name: str) -> list[str]:
    """`jira_get_issue` / `ClusterHealthTool` / `get-file` → слова в нижнем регистре."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def mcp_parts(tool: str) -> tuple[str, str]:
    """`mcp__team-jira__jira_get_issue` → («team-jira», «jira_get_issue»)."""
    rest = tool[len("mcp__"):]
    server, _, name = rest.partition("__")
    return server, name


def mcp_reads(tool_name: str) -> bool:
    """MCP-инструмент без карточки: ни одного глагола изменения в имени
    (`get_issue`, `search_pages`, `ClusterHealthTool` — да; `create_issue`,
    `fetch_url`, `rollover_index`, `list_and_push` — нет)."""
    _server, name = mcp_parts(tool_name) if tool_name.startswith("mcp__") else ("", tool_name)
    words = _words(name)
    return bool(words) and not (set(words) & MCP_WRITE_VERBS)


# --- безопасный набор команд без карточки (fix round 3) -------------------------------------

# Любой из этих знаков — не простая команда (карточка): разделители, перенаправления,
# подстановки, шаблоны, `~`, обратная косая.
_SHELL_UNSAFE = set("\n\r&;|><`$(){}*?[]~\\!")
# Простая команда для «разрешать до конца встречи» (по первому слову).
_SHELL_SIMPLE_BAD = re.compile(r"[\n\r;&|><`]|\$\(")
_WRAPPERS = {"env", "command", "exec", "xargs", "nohup", "sudo", "nice", "time", "timeout", "start", "cmd",
             "powershell", "pwsh", "bash", "sh", "busybox", "doas", "runas", "watch", "strace"}


def _abs_path(arg: str) -> bool:
    return bool(re.match(r"^([A-Za-z]:[\\/]|/)", arg))


def shell_plan(command: str):
    """Команда Bash из безопасного набора чтения — (пути, рекурсивно ли) или
    None (`read_plan`)."""
    return read_plan("Bash", command)


def _path_ok(arg: str) -> bool:
    """Путь в команде чтения: только абсолютный и буквальный (без `$`,
    шаблонов, `~` и обратных кавычек) — иначе не знаем, что прочтётся.
    Без `,` и `;` (в PowerShell `a,b` — два файла: проверялся бы один путь,
    а читались бы оба, I1) и `@` (развёртка аргументов PowerShell). Не
    сетевой и не путь устройства: `//хост/…`, `\\\\хост\\…`, `\\\\?\\…` (I2)."""
    return (_abs_path(arg) and not any(ch in arg for ch in "$*?[]~`,;@")
            and not re.match(r"^[\\/]{2}", arg))


def _ps_paths(positional: list[str], values: dict, *, positional_max: int = 1) -> list[str] | None:
    """Пути командлета PowerShell — по одному на параметр: один позиционный
    или один `-Path`, или один `-LiteralPath` (несколько — карточкой, I1)."""
    given = [positional, values.get("-path", []), values.get("-literalpath", [])]
    if len(positional) > positional_max or any(len(v) > 1 for v in given[1:]) or sum(bool(v) for v in given) > 1:
        return None
    return [p for v in given for p in v]


def _paths_only(args, *, need=0, recursive=False):
    if len(args) < need or not all(_path_ok(a) for a in args):
        return None
    return list(args), recursive


def _scan(args, flags: dict[str, bool], attached: str | None = None, *, fold: bool = False):
    """Флаги команды чтения: только из `flags` (флаг → берёт ли значение
    отдельным словом) или слитные по `attached`; остальное — позиционные.
    → (позиционные, значения флагов) или None (чужой флаг)."""
    positional, values, k = [], {}, 0
    while k < len(args):
        a = args[k]
        key = a.lower() if fold else a
        if a.startswith("-") and a != "-":
            if key in flags:
                if flags[key]:
                    if k + 1 >= len(args):
                        return None
                    values.setdefault(key, []).append(args[k + 1])
                    k += 1
            elif not (attached and re.fullmatch(attached, a)):
                return None
        else:
            positional.append(a)
        k += 1
    return positional, values


def _files(flags: dict[str, bool], attached: str | None = None, *, max_paths: int | None = None):
    """cat/head/tail/wc/cut/sort/uniq/nl: позиционные — файлы. Первая в цепочке
    без файла — нет (читала бы ввод), за `|` — можно."""
    def plan(args, piped):
        scanned = _scan(args, flags, attached)
        if scanned is None:
            return None
        paths = scanned[0]
        if max_paths is not None and len(paths) > max_paths:
            return None           # `uniq a b` пишет в b
        return _paths_only(paths, need=0 if piped else 1)
    return plan


def _plan_ls(args, piped=False):
    flags = [a for a in args if a.startswith("-")]
    paths = [a for a in args if not a.startswith("-")]
    if any(not re.fullmatch(r"-[lahAR1tSrF]+|--all|--human-readable", f) for f in flags):
        return None
    return _paths_only(paths, recursive=any("R" in f for f in flags))


def _plan_grep(rg: bool):
    def plan(args, piped=False):
        pattern_given, positional, k = False, [], 0
        while k < len(args):
            a = args[k]
            if a in ("-e", "-A", "-B", "-C", "-m", "--max-count") or (rg and a in ("-g", "--glob")):
                if k + 1 >= len(args):
                    return None
                pattern_given = pattern_given or a == "-e"
                k += 2
                continue
            if a.startswith(("--glob=", "--color=", "--max-count=")) or re.fullmatch(r"-[ABCm]\d+", a):
                k += 1
                continue
            if a.startswith("-"):
                if not re.fullmatch(r"-[inlcwFvoEHhxs]+", a):
                    return None          # --pre, -O, --pre-glob, -r, -z… — не чтение
                k += 1
                continue
            positional.append(a)
            k += 1
        if not pattern_given:
            if not positional:
                return None
            positional = positional[1:]
        return _paths_only(positional, need=0 if rg or piped else 1, recursive=rg)
    return plan


# sed — только печать строк: адреса (номер, `$`, /рег/) и команды p, d, q, =,
# замена s/…/…/ с флагами g, i, p, числом. Ни `w` (запись), ни `e`
# (выполнение), ни `r` (чтение файла), ни `-i`.
_SED_ADDR = r"(?:\d+|\$|/(?:[^/\\]|\\.)*/)"
_SED_CMD = (r"\s*(?:" + _SED_ADDR + r"(?:\s*,\s*" + _SED_ADDR + r")?)?\s*!?\s*"
            r"(?:[pdq=]|s/(?:[^/\\\n]|\\.)*/(?:[^/\\\n]|\\.)*/[gip0-9]*)\s*")
_SED_SAFE = re.compile(r"(?:" + _SED_CMD + r";)*" + _SED_CMD)


def _plan_sed(args, piped=False):
    scanned = _scan(args, {"-n": False, "-E": False, "-r": False, "-z": False, "--quiet": False,
                           "--silent": False, "-e": True, "--expression": True})
    if scanned is None:
        return None
    positional, values = scanned
    scripts = values.get("-e", []) + values.get("--expression", [])
    if not scripts:
        if not positional:
            return None
        scripts, positional = [positional[0]], positional[1:]
    if not all(_SED_SAFE.fullmatch(s) for s in scripts):
        return None
    return _paths_only(positional, need=0 if piped else 1)


def _plan_findstr(args, piped=False):
    """findstr: `/i /n /v /r /l /b /e /x /m /c:строка` (без /s, /f, /g — обхода и списков из файлов)."""
    positional, literal = [], False
    for a in args:
        low = a.lower()
        if low.startswith("/c:"):
            literal = True
        elif low.startswith("/"):
            if not re.fullmatch(r"(/[invrlbexm])+", low):
                return None
        else:
            positional.append(a)
    if not literal:
        if not positional:
            return None
        positional = positional[1:]
    return _paths_only(positional, need=0 if piped else 1)


def _no_paths(flags: dict[str, bool], attached: str | None = None, *, positional_ok: bool = False,
              fold: bool = False):
    """Фильтр потока без файлов (tr, Select-Object, Sort-Object…)."""
    def plan(args, piped=False):
        scanned = _scan(args, flags, attached, fold=fold)
        if scanned is None or (scanned[0] and not positional_ok):
            return None
        return [], False
    return plan


def _plan_get_content(args, piped=False):
    scanned = _scan(args, {"-path": True, "-literalpath": True, "-totalcount": True, "-head": True, "-first": True,
                           "-tail": True, "-last": True, "-encoding": True, "-raw": False, "-readcount": True},
                    fold=True)
    if scanned is None:
        return None
    paths = _ps_paths(*scanned)
    return None if paths is None else _paths_only(paths, need=1)


def _plan_gci(args, piped=False):
    scanned = _scan(args, {"-path": True, "-literalpath": True, "-filter": True, "-name": False, "-file": False,
                           "-directory": False, "-force": False, "-recurse": False, "-depth": True,
                           "-include": True, "-exclude": True}, fold=True)
    if scanned is None:
        return None
    paths = _ps_paths(*scanned)
    recursive = any(a.lower() in ("-recurse", "-depth") for a in args)
    return None if paths is None else _paths_only(paths, recursive=recursive)


def _plan_select_string(args, piped=False):
    scanned = _scan(args, {"-pattern": True, "-path": True, "-literalpath": True, "-simplematch": False,
                           "-casesensitive": False, "-context": True, "-list": False, "-notmatch": False,
                           "-allmatches": False, "-raw": False, "-quiet": False, "-encoding": True}, fold=True)
    if scanned is None:
        return None
    positional, values = scanned
    if "-pattern" not in values:
        if not positional:
            return None
        positional = positional[1:]
    paths = _ps_paths(positional, values)
    return None if paths is None else _paths_only(paths, need=0 if piped else 1)


_GIT_SUBS = {
    "status": {"-s", "--short", "-b", "--branch"},
    "log": {"--oneline", "--stat", "-n"},
    "diff": {"--stat", "--name-only", "--name-status"},
    "show": {"--stat", "--name-only", "--name-status"},
    "branch": {"-a", "-r"},
}


def _plan_git(args):
    if not args or args[0] not in _GIT_SUBS:
        return None
    sub, rest, allowed = args[0], args[1:], _GIT_SUBS[args[0]]
    k = 0
    while k < len(rest):
        a = rest[k]
        if a.startswith("-"):
            if a not in allowed:
                return None
            if a == "-n":
                if k + 1 >= len(rest) or not rest[k + 1].isdigit():
                    return None
                k += 1
        elif sub in ("status", "branch") or not re.fullmatch(r"[\w./-]+", a):
            return None
        elif _abs_path(a):
            return None             # путь вне репозитория — карточкой
        k += 1
    return [], False


def _plan_find(args):
    paths, k = [], 0
    while k < len(args) and not args[k].startswith("-"):
        paths.append(args[k])
        k += 1
    while k < len(args):
        a = args[k]
        if a == "-name" and k + 1 < len(args):
            k += 2
        elif a == "-type" and k + 1 < len(args) and args[k + 1] in ("f", "d", "l"):
            k += 2
        elif a == "-maxdepth" and k + 1 < len(args) and args[k + 1].isdigit():
            k += 2
        else:
            return None              # -exec, -delete, -fprint… — карточкой
    return _paths_only(paths, need=1, recursive=True)


def _plan_git_read(args, piped=False):
    return _plan_git(args)


_STREAM = r"-n\d+|-\d+|-c\d+|--lines=\d+|--bytes=\d+"
_SAFE_PROGRAMS = {
    "ls": _plan_ls, "dir": _plan_ls,
    "cat": _files({"-n": False, "-b": False, "-s": False, "-A": False, "-E": False, "-T": False}),
    "type": _files({}),
    "head": _files({"-n": True, "-q": False}, _STREAM), "tail": _files({"-n": True, "-q": False}, _STREAM),
    "wc": _files({"-l": False, "-w": False, "-c": False, "-m": False}, r"-[lwcm]+"),
    "nl": _files({"-b": True, "-w": True, "-n": True}, r"-b[atn]|-w\d+|-n(?:ln|rn|rz)"),
    "cut": _files({"-c": True, "-f": True, "-d": True, "-b": True, "-s": False, "--complement": False},
                  r"-[cfb][\d,-]+|-d.|--(?:characters|fields|delimiter|bytes)=.+"),
    "sort": _files({"-n": False, "-r": False, "-u": False, "-f": False, "-h": False, "-V": False, "-b": False,
                    "-g": False, "-M": False, "-s": False, "-k": True, "-t": True},
                   r"-[nrufhVbgMs]+|-k[\d.,a-zA-Z]+|-t."),
    "uniq": _files({"-c": False, "-d": False, "-u": False, "-i": False, "-f": True, "-s": True},
                   r"-[cdui]+", max_paths=1),
    "tr": _no_paths({"-d": False, "-s": False, "-c": False}, r"-[dsc]+", positional_ok=True),
    "pwd": lambda args, piped=False: ([], False) if not args else None,
    "echo": lambda args, piped=False: ([], False),
    "rg": _plan_grep(True), "grep": _plan_grep(False),
    "sed": _plan_sed, "findstr": _plan_findstr,
    "git": _plan_git_read,
    "find": lambda args, piped=False: _plan_find(args),
}
# PowerShell: свои командлеты и псевдонимы (`cat`, `ls`, `type` там — Get-Content / Get-ChildItem).
_PS_PROGRAMS = {
    "get-content": _plan_get_content, "gc": _plan_get_content, "cat": _plan_get_content,
    "type": _plan_get_content,
    "get-childitem": _plan_gci, "gci": _plan_gci, "ls": _plan_gci, "dir": _plan_gci,
    "select-string": _plan_select_string, "sls": _plan_select_string,
    "select-object": _no_paths({"-first": True, "-last": True, "-skip": True, "-unique": False, "-index": True,
                                "-expandproperty": True, "-property": True}, fold=True),
    "sort-object": _no_paths({"-unique": False, "-descending": False, "-property": True}, fold=True,
                             positional_ok=True),
    "measure-object": _no_paths({"-line": False, "-word": False, "-character": False}, fold=True),
    "format-table": _no_paths({"-autosize": False, "-wrap": False}, fold=True, positional_ok=True),
    "format-list": _no_paths({}, fold=True, positional_ok=True),
    "out-string": _no_paths({"-width": True, "-stream": False}, fold=True),
    "get-location": _no_paths({}, fold=True), "pwd": _no_paths({}, fold=True),
    "write-output": lambda args, piped=False: ([], False), "echo": lambda args, piped=False: ([], False),
    "git": _plan_git_read, "rg": _plan_grep(True), "findstr": _plan_findstr,
}
for _alias, _name in (("select", "select-object"), ("sort", "sort-object"), ("measure", "measure-object"),
                      ("ft", "format-table"), ("fl", "format-list")):
    _PS_PROGRAMS[_alias] = _PS_PROGRAMS[_name]
# Знаки, которых в команде чтения не бывает: подстановки, перенаправления,
# блоки и подвыражения, история; `#` — комментарий (bash и PowerShell режут
# по нему по-разному, `<#…#>` — блок PowerShell): команда с ним — не чтение.
_READ_UNSAFE = re.compile(r"[`<>(){}!#\r\n]|\$\(|\$\{|@\(")
_PATTERN_PROGRAMS = frozenset(("sed", "grep", "rg", "findstr", "select-string", "sls"))


def read_plan(tool: str, command: str):
    """Команда только чтения (0.4: и цепочки через `|`, `;`, `&&`) — (пути,
    рекурсивно ли) или None. Каждая команда цепочки — из белого списка
    (cat, head, tail, `sed -n` с печатью строк, grep, rg, cut, sort, uniq,
    wc, nl, tr, ls, findstr, git status/log/diff/show; в PowerShell —
    Get-Content, Get-ChildItem, Select-String, Select-Object, Sort-Object…)
    с КАЖДЫМ флагом из списка; без перенаправлений в файлы, подстановок
    (`$(…)`, обратные кавычки), обёрток, переменных окружения и in-place
    флагов (`sed -i`, `sort -o`); пути — только абсолютные и буквальные."""
    cmd = str(command or "")
    if tool not in SHELL or not cmd.strip() or _READ_UNSAFE.search(cmd):
        return None
    tokens = _tokens(cmd, tool)
    if not tokens:
        return None
    programs = _PS_PROGRAMS if tool == "PowerShell" else _SAFE_PROGRAMS
    segments: list[tuple[list[str], bool]] = [([], False)]
    for t in tokens:
        if t in ("|", ";", "&&"):
            if not segments[-1][0]:
                return None
            segments.append(([], t == "|"))
        elif t in _SEPARATORS or set(t) <= set(";&|"):
            return None                       # `&` (фон), `||` и прочее — не чтение
        else:
            segments[-1][0].append(t)
    paths, recursive = [], False
    for words, piped in segments:
        if not words:
            return None
        head = words[0]
        if "=" in head or head.lower() in _WRAPPERS or "$" in head:
            return None
        # `$` — только в шаблоне поиска или сценарии sed (якорь конца строки, адрес `$`).
        if head.lower() not in _PATTERN_PROGRAMS and any("$" in w for w in words[1:]):
            return None
        handler = programs.get(head.lower() if tool == "PowerShell" else head)
        plan = handler(words[1:], piped) if handler else None
        if plan is None:
            return None
        paths += plan[0]
        recursive = recursive or plan[1]
    return paths, recursive


# --- «до конца встречи» для команд (ревью round 3, G1) -----------------------------------------

# Никогда не разрешаются «до конца встречи» — каждая команда своей карточкой:
# интерпретаторы с кодом в строке и разрушающие команды.
_NEVER_GRANT = {
    "python", "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php", "lua", "bash", "sh",
    "zsh", "fish", "pwsh", "powershell", "cmd", "npx", "pnpx", "bunx", "osascript", "wscript", "cscript",
    "rm", "del", "erase", "rmdir", "rd", "mv", "move", "chmod", "chown", "chgrp", "dd", "shred", "truncate",
    "format", "mkfs", "remove-item", "ri", "move-item", "set-acl", "takeown", "icacls", "reg", "schtasks",
    "sc", "kill", "taskkill", "stop-process", "shutdown",
}
# Команды с подкомандами: ключ — слово и подкоманда; подкоманды записи и
# публикации — никогда.
_SUBCOMMAND_TOOLS = {
    "git": {"push", "reset", "clean", "rm", "rebase", "filter-branch", "filter-repo", "gc", "prune",
            "update-ref", "config", "remote", "checkout", "restore", "stash", "switch", "submodule", "worktree",
            "reflog", "replace", "notes", "credential", "daemon", "send-email", "svn"},
    "npm": {"publish", "unpublish", "exec", "x", "install", "i", "ci", "add", "uninstall", "remove", "rm",
            "un", "update", "up", "link", "deprecate", "dist-tag", "owner", "access", "login", "adduser",
            "token", "config", "set", "cache", "init", "create"},
    "pnpm": {"publish", "exec", "dlx", "install", "i", "add", "remove", "rm", "update", "up", "link", "config",
             "store", "create", "env"},
    "yarn": {"publish", "exec", "dlx", "install", "add", "remove", "upgrade", "up", "link", "npm", "config",
             "create", "set", "plugin"},
    "pip": {"install", "uninstall", "download", "wheel", "config", "cache"},
    "pip3": {"install", "uninstall", "download", "wheel", "config", "cache"},
    "uv": {"pip", "add", "remove", "sync", "tool", "publish", "python", "self", "cache", "init", "venv", "lock",
           "build", "run"},
    "cargo": {"publish", "install", "uninstall", "yank", "login", "logout", "owner", "add", "remove", "clean",
              "new", "init", "update", "run"},
    "docker": {"run", "exec", "rm", "rmi", "push", "login", "logout", "system", "volume", "network", "compose",
               "kill", "stop", "restart", "build", "buildx", "cp", "create", "start", "commit", "tag", "save",
               "load", "import", "prune", "swarm", "service", "secret", "config", "plugin", "context"},
    "kubectl": {"delete", "apply", "create", "edit", "patch", "replace", "exec", "scale", "drain", "cordon",
                "uncordon", "rollout", "set", "label", "annotate", "run", "cp", "expose", "autoscale", "taint",
                "port-forward", "proxy", "attach", "debug", "config", "certificate", "auth"},
    "gh": {"api", "auth", "secret", "variable", "ssh-key", "gpg-key", "extension", "alias", "config",
           "codespace", "release", "repo", "gist", "workflow", "run", "cache", "label", "ruleset"},
    "glab": {"api", "auth", "variable", "ssh-key", "config", "release", "repo", "alias", "ci", "schedule",
             "deploy-key", "token", "snippet", "label"},
    "dotnet": {"publish", "nuget", "tool", "add", "remove", "new", "workload", "run", "pack", "dev-certs",
               "user-secrets"},
    "go": {"install", "get", "run", "generate", "mod", "work", "clean", "env"},
}
# У gh / glab подкоманда двухсловная (`gh pr view`): ключ — три слова, действие
# с глаголом изменения — никогда.
_THREE_WORD = {"gh", "glab"}
_NETWORK_TOOLS = {"curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "http", "https",
                  "xh", "httpie"}
_BODY_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form", "-T",
               "--upload-file", "--json", "--post-data", "--post-file", "--body-data", "--body-file", "-Body",
               "-InFile"}
_METHOD_FLAGS = {"-X", "--request", "--method", "-Method"}


def _shell_words(tool: str, command: str) -> list[str] | None:
    cmd = str(command or "").strip()
    if not cmd or _SHELL_SIMPLE_BAD.search(cmd):
        return None
    try:
        words = shlex.split(cmd, posix=tool != "PowerShell")
    except ValueError:
        return None
    if not words or "=" in words[0] or words[0].lower() in _WRAPPERS:
        return None
    return words


def _program(word: str) -> str:
    name = os.path.basename(word.replace("\\", "/")).lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


# Флаги, которые покрывает разрешение «до конца встречи» (ревью round 3b):
# всё, чего нет в списке, — карточкой. Значение флага — отдельным словом;
# слитная форма (`-HX`, `--data=x`) — карточкой, кроме `-X<метод>`.
_CURL_FLAGS = {"-s", "--silent", "-S", "--show-error", "-f", "--fail", "-i", "--include", "-L", "--location"}
_CURL_SHORT = re.compile(r"-[sSfiL]+")
_CURL_VALUE = {"-H", "--header"}
_CURL_BODY = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json"}
# Подкоманды и их флаги (флаг → берёт ли значение); нет подкоманды в списке —
# только позиционные слова, без флагов.
_GRANT_FLAGS = {
    "git": {
        "status": {"-s": False, "--short": False, "-b": False, "--branch": False},
        "log": {"--oneline": False, "--stat": False, "-n": True, "--graph": False, "--decorate": False},
        "diff": {"--stat": False, "--name-only": False, "--name-status": False, "--cached": False},
        "show": {"--stat": False, "--name-only": False, "--name-status": False},
        "branch": {"-a": False, "-r": False, "-v": False},
        "fetch": {"--all": False, "--prune": False, "--tags": False},
        "pull": {"--ff-only": False},
        "add": {"-A": False, "--all": False, "-u": False},
        "commit": {"-m": True},
        "tag": {"-l": False, "--list": False},
    },
    "npm": {"test": {"--silent": False, "-s": False}, "run": {"--silent": False, "-s": False},
            "ls": {"--depth": True}, "outdated": {}, "view": {}},
    "pnpm": {"test": {}, "run": {}, "ls": {}, "outdated": {}},
    "yarn": {"test": {}, "run": {}, "list": {}, "outdated": {}},
    "pip": {"list": {"--outdated": False}, "show": {}, "freeze": {}, "check": {}},
    "pip3": {"list": {"--outdated": False}, "show": {}, "freeze": {}, "check": {}},
    "cargo": {"test": {"--release": False}, "build": {"--release": False}, "check": {}, "clippy": {}, "fmt": {}},
    "docker": {"ps": {"-a": False}, "images": {}, "logs": {"--tail": True}, "inspect": {}, "version": {}},
    "kubectl": {"get": {"-n": True, "--namespace": True, "-o": True, "-A": False},
                "describe": {"-n": True, "--namespace": True}, "logs": {"-n": True, "--tail": True}},
    "dotnet": {"build": {"-c": True}, "test": {"-c": True}, "--info": {}},
    "go": {"test": {"-v": False}, "build": {}, "vet": {}, "version": {}},
    "uv": {"tree": {}, "version": {}},
    "gh": {}, "glab": {},
}
_GH_FLAGS = {"--json": True, "-L": True, "--limit": True, "--state": True}
_SAFE_POSITIONAL = re.compile(r"[\w./@:+,^~%#-]+")
_GIT_NEVER = re.compile(r"^(--output|--ext-diff|--textconv|-c$|-C$|-O|-p$|--patch|--exec-path|--upload-pack|"
                        r"--receive-pack|--config|--git-dir|--work-tree)")


def _args_ok(args: list[str], flags: dict[str, bool]) -> bool:
    """Аргументы гранта: флаги — только из `flags` (значение — отдельным
    словом), позиционные — простые слова без `=`."""
    k = 0
    while k < len(args):
        a = args[k]
        if a.startswith("-"):
            if a not in flags:
                return False
            if flags[a]:
                if k + 1 >= len(args):
                    return False
                k += 1
        elif not _SAFE_POSITIONAL.fullmatch(a):
            return False
        k += 1
    return True


def _curl_grant(tool: str, words: list[str]) -> tuple[str, str] | None:
    """curl: точный хост и метод; флаги — только безопасные (`-s -S -f -i -L`,
    `-H` значением отдельно, `-X`/`--request` с методом, тело — только для
    POST/PUT/PATCH); всё прочее (`--resolve`, `--connect-to`, `--proxy`,
    `-k`, `-o`, `-K`, `--next`, вторая `--url`…) — карточкой."""
    urls, method, body = [], "", False
    k = 1
    while k < len(words):
        w = words[k]
        if w in _CURL_FLAGS or _CURL_SHORT.fullmatch(w):
            k += 1
            continue
        if w in _CURL_VALUE:
            if k + 1 >= len(words) or words[k + 1].startswith("@"):   # `@файл` — чтение файла (ревью F1)
                return None
            k += 2
            continue
        m = re.fullmatch(r"-X([A-Za-z]+)", w)
        if m:
            method = m.group(1).upper()
            k += 1
            continue
        if w.lower() in ("-x", "--request") and w != "-x":      # «-x» у curl — прокси
            if k + 1 >= len(words) or not re.fullmatch(r"[A-Za-z]+", words[k + 1]):
                return None
            method = words[k + 1].upper()
            k += 2
            continue
        if w in _CURL_BODY:
            if k + 1 >= len(words) or words[k + 1].startswith("@"):   # `-d @файл` — отправка файла
                return None
            body = True
            k += 2
            continue
        if w.startswith("-"):
            return None
        urls.append(w)
        k += 1
    if len(urls) != 1 or not ("://" in urls[0] or _URLISH.match(urls[0])):
        return None
    host = url_host(urls[0])
    if not host or is_local_host(host):
        return None
    method = method or ("POST" if body else "GET")
    if body and method not in ("POST", "PUT", "PATCH"):
        return None
    label = f"{tool}: curl → {host}" + ("" if method == "GET" else f" ({method})")
    return f"net:{tool}:curl:{host}:{method}", label


def shell_grant(tool: str, command: str) -> tuple[str, str] | None:
    """Что разрешит «до конца встречи» для простой команды (None — каждая
    своей карточкой). Обычная программа — по первому слову («Bash: make»);
    `git`, `npm`, `docker`… — по слову и подкоманде («Bash: git status»), и
    флаги — только из списка подкоманды; подкоманды записи и публикации —
    никогда; `gh`/`glab` — по трём словам; `curl` — по точному хосту и
    методу с безопасными флагами («Bash: curl → api.example.com»); прочие
    сетевые (`wget`, `Invoke-WebRequest`…), интерпретаторы с кодом и
    разрушающие команды — никогда. Вызов, подходящий под разрешение, но с
    флагом вне списка, — снова карточкой."""
    words = _shell_words(tool, command)
    if not words:
        return None
    prog = _program(words[0])
    if prog in _NEVER_GRANT:
        return None
    if prog in _NETWORK_TOOLS:
        return _curl_grant(tool, words) if prog == "curl" and tool == "Bash" else None
    if prog in _SUBCOMMAND_TOOLS:
        # Подкоманда — сразу за программой: флаги до неё (`git -c …`, `-C dir`) — не простая команда.
        need = 3 if prog in _THREE_WORD else 2
        if len(words) < need or any(w.startswith("-") for w in words[1:need]):
            return None
        sub = words[1].lower()
        if sub in _SUBCOMMAND_TOOLS[prog]:
            return None
        scope, rest = [prog, sub], words[2:]
        if prog in _THREE_WORD:
            action = words[2].lower()
            if action in MCP_WRITE_VERBS or action in {"merge", "close", "reopen", "delete", "create", "edit",
                                                        "comment", "review", "ready", "lock", "unlock",
                                                        "transfer", "pin", "unpin", "develop", "checkout"}:
                return None
            scope.append(action)
            rest = words[3:]
            flags = _GH_FLAGS
        else:
            flags = _GRANT_FLAGS.get(prog, {}).get(sub, {})
        if prog == "git" and any(_GIT_NEVER.match(w) for w in rest):
            return None
        if not _args_ok(rest, flags):
            return None
        key = " ".join(scope)
        return f"shell:{tool}:{key}", f"{tool}: {key}"
    return f"shell:{tool}:{prog}", f"{tool}: {prog}"


def shell_simple_word(tool: str, command: str) -> str | None:
    """Первое слово простой команды (одна команда, без `;`, `&`, `|`, `>`, `<`, обратной кавычки, `$(` и
    переводов строки) — для «разрешать такое до конца встречи»; иначе None."""
    cmd = str(command or "").strip()
    if not cmd or _SHELL_SIMPLE_BAD.search(cmd):
        return None
    try:
        words = shlex.split(cmd, posix=tool != "PowerShell")
    except ValueError:
        return None
    if not words or "=" in words[0] or words[0].lower() in _WRAPPERS:
        return None
    return words[0]


# --- рискованные команды: удаление и отправка наружу (0.4, спец. §2) ---------------------------
#
# Команда разбирается на простые команды (`;`, `&&`, `||`, `|`, `&`, скобки,
# `$(…)`, обратные кавычки, блоки PowerShell `{…}`), у каждой снимаются
# обёртки (`sudo`, `env`, `xargs`…) и разворачиваются оболочки с кодом в
# строке (`bash -c "…"`, `cmd /c`, `powershell -Command`, `eval`, `iex`).
# Не разобралась — запасной путь в закрытую сторону: любое слово из списков.

DELETE = "delete"
SEND = "send"
# Команду не разобрать до конца (кавычки без пары, блок-комментарий
# PowerShell, имя программы из подстановки…) — карточка, как у рискованной.
UNPARSED = "unparsed"
# Удаление файлов (PowerShell: `rm`, `del`, `erase`, `rd`, `rmdir`, `ri` — псевдонимы Remove-Item).
DELETE_PROGRAMS = frozenset(("rm", "rmdir", "del", "erase", "rd", "remove-item", "ri", "unlink", "shred"))
MOVE_PROGRAMS = frozenset(("mv", "move", "move-item", "mi"))
# Отправка наружу — всегда (любые аргументы).
SEND_PROGRAMS = frozenset(("scp", "sftp", "ftp", "tftp", "ssh", "nc", "ncat", "netcat", "telnet",
                           "send-mailmessage"))
_PS_WEB = frozenset(("invoke-webrequest", "iwr", "invoke-restmethod", "irm"))
_SEND_METHODS = frozenset(("post", "put", "patch", "delete", "merge"))
# Публикация пакетов и образов: (программа, подкоманда).
_PUBLISH = {"npm": {"publish"}, "pnpm": {"publish"}, "yarn": {"publish", "npm"}, "cargo": {"publish"},
            "twine": {"upload"}, "docker": {"push"}, "podman": {"push"}, "gem": {"push"},
            "poetry": {"publish"}, "uv": {"publish"}, "flit": {"publish"}, "hatch": {"publish"}}
# gh / glab: действия, которые пишут на сервер (задача, PR, комментарий…).
_FORGE_ACTIONS = frozenset(("create", "comment", "note", "review", "merge", "close", "reopen", "edit", "delete",
                            "upload", "ready", "lock", "unlock", "transfer", "approve", "revoke", "update",
                            "rename", "archive", "fork", "sync", "set", "add", "remove", "cancel", "rerun",
                            "run", "enable", "disable"))
# Обёртки, у которых дальше идёт обычная команда (флаги и их значения пропускаются).
_PASS_WRAPPERS = frozenset(("sudo", "doas", "env", "command", "exec", "nohup", "nice", "time", "timeout", "watch",
                            "strace", "busybox", "runas", "start", "start-process", "xargs", "stdbuf", "ionice",
                            "chrt", "setsid", "unbuffer", "caffeinate", "&", "."))
_VALUE_FLAGS = {"xargs": {"-I", "-n", "-L", "-P", "-d", "-E", "-s", "-a", "--max-args", "--max-procs"},
                "timeout": {"-s", "-k", "--signal", "--kill-after"}, "sudo": {"-u", "-g", "-C", "-D", "-h", "-p"},
                "nice": {"-n"}, "env": {"-u", "-C", "-S"}, "start-process": {"-ArgumentList", "-FilePath"}}
# Оболочки с кодом в строке: (программа → флаги, после которых код).
_SHELLS = {"bash": {"-c"}, "sh": {"-c"}, "zsh": {"-c"}, "dash": {"-c"}, "ksh": {"-c"}, "fish": {"-c", "--command"},
           "cmd": {"/c", "/k", "/r"}, "powershell": {"-c", "-command", "-com", "-comm", "-comma", "-comman"},
           "pwsh": {"-c", "-command", "-com", "-comm", "-comma", "-comman"}}
_EVALS = frozenset(("eval", "iex", "invoke-expression", "invoke-command", "icm"))
_CD = frozenset(("cd", "pushd", "chdir", "set-location", "sl", "push-location"))
_SEPARATORS = frozenset((";", ";;", "&&", "||", "|", "&", "|&", "(", ")", "{", "}", "$(", "${"))
_REDIRECTS = re.compile(r"^\d*(?:>>?|<<?|>&|<&|&>>?)$")
_RISK_DEPTH = 4


def _tokens(text: str, tool: str) -> list[str] | None:
    """Слова и разделители команды (кавычки соблюдены; перевод строки — `;`).
    `#` — обычный знак: shlex по умолчанию режет по нему и посреди слова
    (`a.txt#; rm …`), а оболочки — только в начале слова; комментарий,
    прочитанный как код, лишь добавляет слов — в закрытую сторону (C1)."""
    text = re.sub(r"\r\n?|\n", " ; ", str(text or ""))
    try:
        lexer = shlex.shlex(text, posix=tool != "PowerShell", punctuation_chars="();<>|&{}")
        lexer.commenters = ""
        lexer.whitespace_split = True
        out = list(lexer)
    except ValueError:
        return None
    if tool == "PowerShell":
        out = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"" else t for t in out]
    return out


def _commands(tokens: list[str]) -> list[list[str]]:
    """Простые команды из слов: делятся разделителями; перенаправление —
    вместе с его целью — не слово команды; обратная кавычка — граница."""
    out: list[list[str]] = [[]]
    k = 0
    while k < len(tokens):
        t = tokens[k]
        # Подстановка процесса `<(…)` / `>(…)`: команда внутри выполняется —
        # граница, а не перенаправление в файл (иначе `rm` внутри не увидим).
        if t in ("<(", ">(", "<", ">") and k + 1 < len(tokens) and tokens[k + 1] == "(":
            out.append([])
            k += 2
            continue
        if t in ("<(", ">("):
            out.append([])
            k += 1
            continue
        if t in _SEPARATORS or t == "$" or set(t) <= set(";&|(){}"):
            out.append([])
            k += 1
            continue
        if _REDIRECTS.match(t) or set(t) <= set("<>"):
            # Перенаправление и его цель; целью не бывает разделитель (`# >↵rm …`)
            # и открытие подстановки процесса (`< <(rm …)` — её разберём отдельно).
            nxt = tokens[k + 1] if k + 1 < len(tokens) else ""
            swallow = nxt and nxt not in ("<(", ">(", "(") and nxt not in _SEPARATORS \
                and not set(nxt) <= set(";&|(){}")
            k += 2 if swallow else 1
            continue
        if t.startswith("`"):
            out.append([])
            t = t[1:]
        tail = t.endswith("`")
        t = t.rstrip("`")
        if t:
            out[-1].append(t)
        if tail:
            out.append([])
        k += 1
    return [c for c in out if c]


def _inner_code(word: str) -> list[str]:
    """Код внутри слова: `$(…)` и обратные кавычки (в том числе в кавычках)."""
    found = re.findall(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)", word)
    found += re.findall(r"`([^`]*)`", word)
    if not found and ("$(" in word or word.count("`") == 1):
        found.append(word.replace("$(", " ").replace("`", " "))
    return found


def _strip_wrappers(words: list[str]) -> list[str]:
    """Снять присваивания переменных и обёртки (`sudo -u x`, `xargs -n 1`, `timeout 5`)."""
    k = 0
    while k < len(words):
        w = words[k]
        if re.match(r"^[A-Za-z_][\w]*=", w) or w.startswith("$env:"):
            k += 1
            continue
        prog = _program(w)
        if prog not in _PASS_WRAPPERS:
            break
        k += 1
        values = {v.lower() for v in _VALUE_FLAGS.get(prog, ())}
        while k < len(words) and (words[k].startswith("-") or (prog == "timeout" and re.fullmatch(r"[\d.]+\w?",
                                                                                                    words[k]))):
            flag = words[k].lower()
            k += 2 if flag in values else 1
    return words[k:]


def _git_risk(words: list[str]) -> str:
    """`git [глобальные флаги] <подкоманда> …`: push — наружу; clean, rm,
    reset --hard, checkout -- <путь> / checkout ., restore (кроме только
    --staged) — удаление."""
    k = 1
    while k < len(words) and words[k].startswith("-"):
        k += 2 if words[k] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path") else 1
    if k >= len(words):
        return ""
    sub, rest = words[k].lower(), words[k + 1:]
    if sub in ("push", "send-email", "request-pull") or (sub == "svn" and "dcommit" in rest):
        return SEND
    if sub in ("clean", "rm"):
        return DELETE
    if sub == "reset" and any(w in ("--hard", "--merge", "--keep") for w in rest):
        return DELETE
    if sub == "checkout" and ("--" in rest or "." in rest or any(w in ("-f", "--force") for w in rest)):
        return DELETE
    if sub == "restore":
        staged_only = any(w in ("--staged", "-S") for w in rest) and not any(w in ("--worktree", "-W")
                                                                            for w in rest)
        return "" if staged_only else DELETE
    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return DELETE
    return ""


def _curl_sends(words: list[str]) -> bool:
    """curl: тело (`-d`, `--data*`, `-F`, `--form*`, `-T`, `--upload-file`,
    `--json`) или метод изменения (`-X POST`, `-XPUT`, `--request=PATCH`)."""
    k = 1
    while k < len(words):
        w = words[k]
        low = w.lower()
        if w.startswith("--"):
            name, _eq, value = low.partition("=")
            if name.startswith(("--data", "--form", "--upload", "--json", "--post")):
                return True
            if name in ("--request", "--method"):
                method = value or (words[k + 1].lower() if k + 1 < len(words) else "")
                if method in _SEND_METHODS:
                    return True
        elif w.startswith("-") and len(w) > 1:
            letters = w[1:]
            for i, ch in enumerate(letters):
                if ch in "dFT":
                    return True
                if ch == "X":
                    method = (letters[i + 1:] or (words[k + 1] if k + 1 < len(words) else "")).lower()
                    if method in _SEND_METHODS:
                        return True
                    break
                if ch in "HoOuAebcKxwmrEyYzCUQDtP":    # дальше — значение флага
                    break
        k += 1
    return False


def _ps_web_sends(words: list[str]) -> bool:
    """Invoke-WebRequest / Invoke-RestMethod (и curl/wget — их псевдонимы в
    PowerShell 5): `-Method Post…`, `-Body`, `-InFile`, `-Form`
    (сокращения параметров PowerShell — тоже)."""
    k = 1
    while k < len(words):
        w = words[k]
        if w.startswith("-") and len(w) > 2:
            name, _colon, value = w[1:].lower().partition(":")
            if len(name) >= 2 and ("method".startswith(name) or "custommethod".startswith(name)):
                method = value or (words[k + 1].lower() if k + 1 < len(words) else "")
                if method.strip("'\"") in _SEND_METHODS or "custommethod".startswith(name) and len(name) > 2:
                    return True
            if len(name) >= 2 and any(full.startswith(name) for full in ("body", "infile", "form")):
                return True
        k += 1
    return False


def _http_sends(prog: str, words: list[str]) -> bool:
    if prog == "curl" and _curl_sends(words):
        return True
    if prog == "wget" and any(w.lower().startswith(("--post", "--body", "--method")) for w in words[1:]):
        return True
    if prog in ("http", "https", "xh", "httpie"):
        rest = words[1:]
        if any(w.upper() in ("POST", "PUT", "PATCH", "DELETE") for w in rest[:1]) or \
                any(re.search(r"(?<!:)(?::=|=|@)", w) and not w.startswith("-") for w in rest[1:]):
            return True
    return False


def _forge_sends(prog: str, words: list[str]) -> bool:
    """gh / glab: задачи, PR и комментарии (`gh pr create`, `gh issue comment`…),
    `api` с телом или методом изменения."""
    rest = [w.lower() for w in words[1:] if not w.startswith("-")]
    if not rest:
        return False
    if rest[0] == "api":
        for i, w in enumerate(words[2:], 2):
            if re.match(r"^(?:-[fF]|--field|--raw-field|--input|--form)", w):
                return True               # тело запроса
            name, _eq, value = w.lower().partition("=")
            if name in ("-x", "--method", "--request"):
                method = value or (words[i + 1].lower() if i + 1 < len(words) else "")
                if method in _SEND_METHODS:
                    return True
            elif re.fullmatch(r"-x(post|put|patch|delete)", w.lower()):
                return True
        return False
    return (len(rest) >= 2 and rest[1] in _FORGE_ACTIONS) or rest[0] in ("release", "gist", "secret", "variable")


def _paths_of(words: list[str]) -> list[str]:
    return [w for w in words[1:] if not w.startswith("-")]


def _command_risk(words: list[str], tool: str, inside, cd: bool, depth: int) -> str:
    words = _strip_wrappers(words)
    if not words:
        return ""
    for w in words:
        for piece in re.split(r"[=,]", w):
            if _NETWORK_WORD.match(piece):
                return SEND                     # сетевой путь — соединение с чужим хостом (I2)
            if _DEVICE_PREFIX.match(piece) and not _DRIVE.match(piece[4:]):
                return UNPARSED                 # канал, диск целиком, том…
    prog = _program(words[0])
    for w in words[1:]:
        for code in _inner_code(w):
            risk = shell_risk(tool, code, inside=inside, _depth=depth + 1)
            if risk:
                return risk
    if prog in _SHELLS:
        flags = _SHELLS[prog]
        if prog in ("powershell", "pwsh") and any(
                len(w) > 2 and "-encodedcommand".startswith(w.lower()) for w in words[1:]):
            return DELETE                       # код не прочесть — в закрытую сторону
        for i, w in enumerate(words[1:], 1):
            if w.lower() in flags:
                code = " ".join(words[i + 1:])
                return shell_risk("PowerShell" if prog in ("powershell", "pwsh") else "Bash", code,
                                  inside=inside, _depth=depth + 1)
        # Сценарий файлом (`bash x.sh`) — что внутри, Meet не видит: решает автомод CLI.
        return ""
    if prog in _EVALS:
        code = " ".join(w for w in words[1:] if not w.startswith("-"))
        return shell_risk(tool, code, inside=inside, _depth=depth + 1)
    if prog in DELETE_PROGRAMS:
        return DELETE
    if prog in MOVE_PROGRAMS or (prog == "git" and len(words) > 1 and words[1].lower() == "mv"):
        targets = _paths_of(words[1:] if prog == "git" else words)
        if not targets or cd or inside is None or not all(inside(t) for t in targets):
            return DELETE
        return ""
    if prog == "find":
        low = [w.lower() for w in words[1:]]
        if "-delete" in low:
            return DELETE
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in low:
                start = low.index(flag) + 1
                end = next((i for i in range(start, len(low)) if low[i] in (";", "\\;", "+")), len(low))
                risk = _command_risk(words[1 + start:1 + end], tool, inside, cd, depth + 1)
                if risk:
                    return risk
        return ""
    if prog == "git":
        return _git_risk(words)
    if prog in SEND_PROGRAMS:
        return SEND
    if prog == "rsync" and any(re.match(r"^(?:rsync://|[^/\\\s]+::|[^/\\\s:]{2,}:)", w)
                               for w in words[1:] if not w.startswith("-")):
        return SEND
    if prog in _PS_WEB or (tool == "PowerShell" and prog in ("curl", "wget")):
        if _ps_web_sends(words):
            return SEND
    if prog in ("curl", "wget", "http", "https", "xh", "httpie") and _http_sends(prog, words):
        return SEND
    if prog in ("gh", "glab") and _forge_sends(prog, words):
        return SEND
    if prog in _PUBLISH:
        subs = [w.lower() for w in words[1:] if not w.startswith("-")]
        if subs and subs[0] in _PUBLISH[prog]:
            return SEND
    if prog == "dotnet" and [w.lower() for w in words[1:3]] == ["nuget", "push"]:
        return SEND
    return ""


def _fallback_risk(text: str) -> str:
    """Команду не разобрать — любое слово из списков (в закрытую сторону)."""
    words = {_program(w) for w in re.findall(r"[\w./\\:-]+", str(text or ""))}
    if words & (DELETE_PROGRAMS | MOVE_PROGRAMS) or {"clean", "-delete"} & words:
        return DELETE
    if words & SEND_PROGRAMS or "push" in words or (words & {"curl", "wget", "rsync", "gh", "glab"} | words &
                                                     _PS_WEB):
        return SEND
    return ""


def shell_risk(tool: str, command: str, *, inside=None, _depth: int = 0) -> str:
    """Категория Meet для команды (0.4, спец. §2): `delete` — удаление
    (`rm`, `rmdir`, `del`, `erase`, `rd`, `Remove-Item`/`ri`, `git clean`,
    `git rm`, `git reset --hard`, `git checkout -- …`, `git restore …`,
    `find -delete`, `mv`/`move` за пределы рабочих папок); `send` —
    отправка наружу (`git push`, `scp`, `sftp`, `ftp`, `ssh`, `rsync host:`,
    `curl`/`wget`/`Invoke-WebRequest`/`irm` с телом или методом изменения,
    `gh pr|issue create|comment…`, публикация пакетов); `""` — ни то, ни
    другое (решает автомод CLI); `unparsed` — команду не разобрать до конца
    (кавычки без пары, блок-комментарий PowerShell…): карточка, без «до
    конца встречи». `inside(путь) -> bool` — путь в рабочих папках (для
    `mv`); None — любой перенос считается выходом наружу."""
    if _depth > _RISK_DEPTH:
        return DELETE
    command = str(command or "")
    if tool == "PowerShell" and "<#" in command:
        # Блок-комментарий PowerShell: что выполнится — без него (и он же
        # разделяет слова или нет — смотрим оба), а сам блок — в закрытую сторону.
        variants = [re.sub(r"<#.*?#>", sep, command, flags=re.S) for sep in (" ", "")]
        risks = [shell_risk(tool, v, inside=inside, _depth=_depth + 1) for v in variants
                 if "<#" not in v]
        return _worst(risks) or UNPARSED
    tokens = _tokens(command, tool)
    if tokens is None:
        return _fallback_risk(command) or UNPARSED
    commands = _commands(tokens)
    cd = any(_program(c[0]) in _CD for c in commands if c)
    return _worst([_command_risk(c, tool, inside, cd, _depth) for c in commands])


# Категории команд, которые «до конца встречи» не разрешаются: каждая — своей карточкой.
_NO_GRANT = (DELETE, UNPARSED)


def _worst(risks) -> str:
    """Самая серьёзная категория: удаление, отправка, непонятное."""
    for risk in (DELETE, SEND, UNPARSED):
        if risk in risks:
            return risk
    return ""


# --- адреса в аргументах MCP (fix round 3; ревью N2) -------------------------------------------

_URLISH = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*://|www\.|[\w-]+(?:\.[\w-]+)+(?::\d+)?/|[^@\s/]+@[\w-]+(?:\.[\w-]+)+\s*$)",
                     re.IGNORECASE)
_URL_KEYS = {"url", "uri", "link", "href", "endpoint", "host", "hostname", "target", "domain", "webhook",
             "callback", "address", "server", "email", "to", "recipient"}


def _pairs(value, key=""):
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield "", str(k)
            yield from _pairs(v, str(k).lower())
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _pairs(v, key)


def url_host(value: str) -> str:
    """Хост из URL, `host.tld/…`, `user@host` или самого имени хоста."""
    v = value.strip()
    if "://" not in v:
        if "@" in v and "/" not in v:
            return v.rsplit("@", 1)[1].strip().lower()
        v = "http://" + v
    try:
        return (urlsplit(v).hostname or "").lower()
    except ValueError:
        return ""


# --- пути ----------------------------------------------------------------------------


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def _norm_text(text: str) -> str:
    text = text.replace("\\", "/").rstrip("/")
    return text.lower() if sys.platform == "win32" else text


# Чем делятся слова-пути в тексте вызова (списки PowerShell через `,` — тоже).
_PATH_SPLIT = re.compile(r"[\s,;|&()<>{}\"'=`]+")


def _lex_path(word: str, base: str | None = None) -> str:
    """Путь без обращения к диску: `\\` → `/`, без `.`, `..` и повторных
    `/`; на Windows — без точек и пробелов в конце частей (`.ssh.` — это
    `.ssh`); относительный с `..` — от `base`."""
    p = word.replace("\\", "/")
    if base and ".." in p and not re.match(r"^(?:[A-Za-z]:)?/", p):
        p = base.replace("\\", "/").rstrip("/") + "/" + p
    if sys.platform == "win32":
        p = "/".join(s if s in (".", "..") else s.rstrip(". ") for s in p.split("/"))
    return posixpath.normpath(p) if p else p


def _path_words(text: str, base: str | None = None) -> str:
    """Слова текста, похожие на пути, — нормализованными (`_lex_path`) и с
    раскрытыми кавычками (`.ss''h` — `.ssh`, `"$HOME"/.ssh` — `$HOME/.ssh`):
    чтобы проверка закрытых путей по тексту видела то же, что оболочка (I1)."""
    words = set(_PATH_SPLIT.split(text))
    try:
        words.update(shlex.split(text.replace("\\", "/"), posix=True))
    except ValueError:
        pass
    found = []
    for w in words:
        if "/" in w or "\\" in w:
            found += [_lex_path(x, base) for x in _PATH_SPLIT.split(w) if x] if _PATH_SPLIT.search(w) else \
                [_lex_path(w, base)]
    return " ".join(found)


# Сетевой путь (UNC): `\\host…`, `//host…`, `\\?\UNC\…`, `\\.\UNC\…`. Открыть
# его на Windows — соединение SMB с чужим хостом (и хеш NTLM ему), I2.
_NETWORK_PATH = re.compile(r"^[\\/]{2}(?:[?.][\\/]UNC(?:[\\/]|$)|(?![?.][\\/])[^\\/])", re.IGNORECASE)
# То же словом в команде: хост и ресурс (`//TODO` в шаблоне поиска — не путь).
_NETWORK_WORD = re.compile(r"^[\\/]{2}(?:[?.][\\/]UNC[\\/])?[^\\/?.\s][^\\/\s]*[\\/][^\\/\s]", re.IGNORECASE)
# Путь устройства `\\?\…`, `\\.\…`: за ним допустим только диск (`\\?\C:\…`).
_DEVICE_PREFIX = re.compile(r"^[\\/]{2}[?.][\\/]")
_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")


def network_path(text: str) -> bool:
    """Путь сетевой (UNC) или устройства не на диске (`\\\\.\\pipe\\…`) — ворота его
    не открывают и не разбирают через `realpath` (I2)."""
    text = str(text or "")
    if _NETWORK_PATH.match(text):
        return True
    return bool(_DEVICE_PREFIX.match(text)) and not _DRIVE.match(text[4:])


def _unc_text(path) -> str | None:
    """Сетевой путь для сравнения — только по тексту, без обращения к сети:
    `\\\\?\\UNC\\` снят, `.` и `..` разобраны (выше ресурса `\\\\хост\\ресурс`
    не уйти), `/`, без регистра на Windows. Не сетевой — None."""
    text = os.path.expandvars(str(path or "")).replace("/", "\\")
    m = re.match(r"^\\\\[?.]\\UNC\\", text, re.IGNORECASE)
    if m:
        text = "\\\\" + text[m.end():]
    if not re.match(r"^\\\\[^\\?.][^\\]*\\[^\\]+", text):
        return None
    return _norm_text(ntpath.normpath(text))


def resolve(path, cwd=None) -> str | None:
    """Путь для сравнения: `~` и переменные среды раскрыты, относительный —
    от `cwd` (рабочей папки CLI), без префикса `\\\\?\\`, через `realpath`
    (8.3-имена, соединения, ссылки), с `/`, без учёта регистра на Windows.
    Не вышло — None (вызывающий отказывает). Сетевой путь и путь устройства
    — тоже None, до `realpath`: ворота в сеть не ходят никогда (I2)."""
    try:
        text = os.path.expandvars(os.path.expanduser(str(path)))
        if network_path(text):
            return None
        if _DEVICE_PREFIX.match(text):
            text = text[4:]                                   # `\\?\C:\…` → `C:\…`
        if sys.platform == "win32":
            m = re.match(r"^/([a-zA-Z])(/.*)?$", text)     # git bash: /d/KB → D:/KB
            if m:
                text = f"{m.group(1)}:{m.group(2) or '/'}"
        p = Path(text)
        if not p.is_absolute():
            p = Path(cwd or os.getcwd()) / p
        if network_path(str(p)) or _DEVICE_PREFIX.match(str(p)):
            return None
        return _norm_text(os.path.realpath(str(p)))
    except (OSError, ValueError, TypeError):
        return None


def _inside(path: str | None, root: str | None) -> bool:
    return bool(path) and bool(root) and (path == root or path.startswith(root + "/"))


def _glob_root(pattern: str) -> tuple[str, bool]:
    """Неизменная часть шаблона Glob и есть ли `..` после «джокера»."""
    parts = re.split(r"[\\/]", pattern)
    keep: list[str] = []
    wild = False
    for part in parts:
        if wild:
            if part == "..":
                return "/".join(keep), True
            continue
        if any(ch in part for ch in "*?[{"):
            wild = True
            continue
        keep.append(part)
    return "/".join(keep), False


def sensitive_paths(*, data_dir=None, library_root=None, home=None) -> list[Path]:
    """Закрытые всегда (кроме `.env` — он по имени): ключи и настройки
    программ, профили браузеров, хранилища паролей ОС, служебные файлы Meet
    (всё в его папке данных, кроме библиотеки встреч)."""
    home = Path(home or _home())
    out = [home / x for x in (
        ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".netrc", ".git-credentials",
        ".npmrc", ".pypirc", ".claude", ".claude.json", ".codex", ".config/gcloud", ".config/gh",
        ".config/opencode", ".local/share/opencode", ".mozilla", ".config/google-chrome",
        ".config/chromium", ".config/BraveSoftware", ".local/share/keyrings", "Library/Keychains",
        "Library/Application Support/Google/Chrome", "Library/Application Support/Firefox",
        "Library/Application Support/BraveSoftware", "Library/Application Support/Microsoft Edge",
        "Library/Cookies")]
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    if local:
        out += [Path(local) / x for x in (
            "Google/Chrome/User Data", "Microsoft/Edge/User Data", "BraveSoftware", "Chromium/User Data",
            "Yandex/YandexBrowser/User Data", "Microsoft/Credentials", "Microsoft/Vault")]
    if roaming:
        out += [Path(roaming) / x for x in (
            "Mozilla/Firefox", "Opera Software", "Microsoft/Credentials", "Microsoft/Protect",
            "Microsoft/Crypto", "gcloud", "GitHub CLI")]
    if data_dir is None:
        try:
            from meet import paths

            data_dir = paths.data_dir()
        except Exception:
            data_dir = None
    if data_dir is not None:
        data = Path(data_dir)
        lib = resolve(library_root) if library_root else None
        try:
            children = list(data.iterdir()) if data.is_dir() else []
        except OSError:
            children = []
        for child in children:
            rc = resolve(child)
            # Библиотеку встреч (по умолчанию — `recordings` в папке данных) не закрываем.
            if lib and (_inside(lib, rc) or _inside(rc, lib)):
                continue
            out.append(child)
        out += [data / "api.token", data / "config.json"]
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


# --- описание вызова -------------------------------------------------------------------


def describe(tool: str, data: dict) -> tuple[str, str]:
    """(вид, «что агент хотел» по-русски) — для причины отказа, строки в чате
    и карточки."""
    data = data if isinstance(data, dict) else {}
    target = (data.get("file_path") or data.get("notebook_path") or data.get("path")
              or data.get("pattern") or "")
    if tool in FILE_READ:
        return "read", f"открыть {target}" if target else f"прочитать файлы ({tool})"
    if tool in FILE_WRITE:
        return "write", f"записать файл {target}" if target else f"изменить файлы ({tool})"
    if tool in SHELL:
        cmd = " ".join(str(data.get("command") or "").split())
        return "shell", f"выполнить команду «{cmd[:160]}»" if cmd else f"выполнить команду ({tool})"
    if tool in WEB_SEARCH:
        return "web-search", f"искать в вебе «{str(data.get('query') or '')[:120]}»"
    if tool in WEB_FETCH:
        return "web-fetch", f"открыть в вебе {str(data.get('url') or '')[:200]}"
    if tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        return ("mcp-read" if mcp_reads(tool) else "mcp"), f"MCP {server}: {name}"
    if tool in ("ListMcpResourcesTool", "ListMcpResources"):
        return "mcp-read", f"список ресурсов MCP ({tool})"
    if tool in MCP_RESOURCES:
        return "mcp-resource", f"прочитать ресурс MCP {data.get('uri') or ''}".rstrip()
    if tool in BACKGROUND_TOOLS:
        return "background", f"{tool} (фоновое выполнение)"
    if tool in QUESTION_TOOLS:
        return "question", "спросить пользователя своим окном"
    if tool in INTERNAL:
        return "internal", tool
    if tool == "Skill":
        return "skill", f"навык «{data.get('skill') or data.get('command') or data.get('name') or '?'}»"
    return "other", f"инструмент {tool}"


def raw_args(tool: str, data: dict) -> str:
    """Что выполнится — текстом, целиком (по нему предел длины; показ — `card_text`)."""
    data = data if isinstance(data, dict) else {}
    if tool in SHELL:
        return str(data.get("command") or "")
    if tool in WEB_FETCH:
        out = str(data.get("url") or "")
        if data.get("prompt"):
            out += f"\n(что найти: {data['prompt']})"
        return out
    return json.dumps(data, ensure_ascii=False, indent=1)


def bash_warnings(tool: str, data: dict) -> list[str]:
    """Необычные параметры команды — крупно в карточке (ревью N5)."""
    out = []
    if tool in SHELL:
        if data.get("dangerouslyDisableSandbox"):
            out.append("⚠ Без песочницы Claude Code (dangerouslyDisableSandbox)")
        extra = sorted(k for k in data if k not in ("command", "description", "timeout", "run_in_background",
                                                     "dangerouslyDisableSandbox"))
        if extra:
            out.append("⚠ Необычные параметры: " + ", ".join(f"{k}={json.dumps(data[k], ensure_ascii=False)}"
                                                          for k in extra))
    return out


# --- текст карточки по инструменту (grant-polish) ------------------------------------------
#
# Карточка показывает вызов по-человечески, но выводится из ТОЧНЫХ аргументов
# и ничего выполняемого не опускает: известные поля — своим видом (путь,
# содержимое, строки правки), всё прочее — «Другие параметры» в виде JSON.
# Строки проходят те же пометки (`display_text`), что и раньше.


def _inline(value: str, mark: bool) -> str:
    """Однострочное значение (путь, адрес, id): перевод строки, табуляция и
    возврат каретки — пометками, чтобы путь не «съехал» в следующую строку."""
    return display_text(value, shell=True) if mark else value


def _block(text: str, mark: bool) -> list[str]:
    """Многострочный текст (содержимое файла, строки правки) — строками:
    пометки знаков, длинных пробелов и табуляций (`display_text`), одиночный
    возврат каретки — «␍», три и больше пустых строк подряд — одной строкой
    «⟨N пустых строк⟩». CRLF — обычный перевод строки. Пустое — «⟨пусто⟩»."""
    if not mark:
        return text.split("\n")
    if text == "":
        return ["⟨пусто⟩"]
    lines = display_text(text).replace("\r\n", "\n").replace("\r", "␍").split("\n")
    out: list[str] = []
    k = 0
    while k < len(lines):
        j = k
        while j < len(lines) and lines[j].strip(" \t") == "":
            j += 1
        if j - k >= 3:
            out.append(f"⟨{j - k} пустых строк⟩")
            k = j
        else:
            out.append(lines[k])
            k += 1
    return out


def _quote(value: str, mark: bool) -> str:
    """Строка в JSON-виде без двойного экранирования: обратная косая — одна
    (`C:\\Users\\…` показывается как в проводнике); экранируется только
    кавычка (`\\"`) и косые прямо перед кавычкой или в конце строки
    (удваиваются) — так из показа однозначно видно, где строка кончается, и
    поддельный ключ внутри значения выдаёт себя `\\"`. Переводы строк —
    пометкой «↵» и настоящим переводом; строки-продолжения — с «│ », чтобы
    не изобразить строку ключа даже похожими кавычками (`＂…＂`, ревью GP2)."""
    escaped = re.sub(r'(\\*)("|\Z)', lambda m: m.group(1) * 2 + ('\\"' if m.group(2) else ""), str(value))
    if not mark:
        return '"' + escaped + '"'
    return '"' + display_text(escaped, shell=True).replace("\n", "\n│ ") + '"'


def pretty_args(value, mark: bool = True, indent: int = 0) -> str:
    """Аргументы как JSON с отступами — для людей: строки через `_quote`."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}  {_quote(str(k), mark)}: {pretty_args(v, mark, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        if not any(isinstance(v, (dict, list, tuple)) for v in value):
            flat = [pretty_args(v, mark, indent + 1) for v in value]
            if not any("\n" in x for x in flat) and sum(len(x) + 2 for x in flat) <= 80:
                return "[" + ", ".join(flat) + "]"       # короткий список значений — одной строкой
        items = [f"{pad}  {pretty_args(v, mark, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    if isinstance(value, str):
        return _quote(value, mark)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return _quote(repr(value), mark)


def _others(data: dict, known: tuple[str, ...], mark: bool) -> list[str]:
    """Параметры, которых карточка своим видом не показывает, — все до одного."""
    rest = {k: v for k, v in data.items() if k not in known}
    return ["Другие параметры: " + pretty_args(rest, mark)] if rest else []


def _replace_all(data: dict, mark: bool) -> list[str]:
    """`replace_all` — явно, если задан чем-то кроме false."""
    if "replace_all" not in data or data["replace_all"] in (False, None):
        return []
    value = data["replace_all"]
    shown = "true" if value is True else pretty_args(value, mark)
    return [f"Заменить ВСЕ вхождения (replace_all: {shown})"]


def _diff(old: str, new: str, mark: bool) -> list[str]:
    return [*("− " + x for x in _block(old, mark)), *("+ " + x for x in _block(new, mark))]


def _edit_ok(e) -> bool:
    return isinstance(e, dict) and isinstance(e.get("old_string"), str) and isinstance(e.get("new_string"), str)


def _file_text(tool: str, data: dict, mark: bool) -> str | None:
    """Write / Edit / MultiEdit / NotebookEdit — заголовок с путём,
    содержимое или строки правки; None — вызов не того вида (покажем JSON)."""
    path_key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    path = data.get(path_key)
    if not isinstance(path, str):
        return None
    shown = _inline(path, mark)
    if tool == "Write":
        if not isinstance(data.get("content"), str):
            return None
        lines = [f"Записать файл: {shown}", *("│ " + x for x in _block(data["content"], mark))]
        return "\n".join(lines + _others(data, ("file_path", "content"), mark))
    if tool == "Edit":
        if not _edit_ok(data):
            return None
        lines = [f"Изменить файл: {shown}", *_replace_all(data, mark),
                 *_diff(data["old_string"], data["new_string"], mark)]
        return "\n".join(lines + _others(data, ("file_path", "old_string", "new_string", "replace_all"), mark))
    if tool == "MultiEdit":
        edits = data.get("edits")
        if not isinstance(edits, list) or not all(_edit_ok(e) for e in edits):
            return None
        lines = [f"Изменить файл: {shown} (правок: {len(edits)})"]
        for n, e in enumerate(edits, 1):
            lines += [f"Правка {n}:", *_replace_all(e, mark), *_diff(e["old_string"], e["new_string"], mark),
                      *_others(e, ("old_string", "new_string", "replace_all"), mark)]
        return "\n".join(lines + _others(data, ("file_path", "edits"), mark))
    if tool == "NotebookEdit":
        lines = [f"Изменить блокнот: {shown}"]
        for key, name in (("cell_id", "ячейка"), ("cell_type", "тип"), ("edit_mode", "режим")):
            if key in data:
                value = data[key]
                lines.append(f"{name}: " + (_inline(value, mark) if isinstance(value, str) else pretty_args(value, mark)))
        known = ("notebook_path", "cell_id", "cell_type", "edit_mode")
        if isinstance(data.get("new_source"), str):
            lines += ["+ " + x for x in _block(data["new_source"], mark)]
            known += ("new_source",)
        return "\n".join(lines + _others(data, known, mark))
    return None


def web_host(url: str) -> tuple[str, str, bool]:
    """(хост, его punycode, есть ли часть до «@») — из разобранного адреса,
    как его поймёт клиент: у `https://good.com@evil.com/x` хост — evil.com."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        userinfo = "@" in (parts.netloc or "")
    except ValueError:
        return "", "", False
    try:
        puny = host.encode("idna").decode("ascii") if host else ""
    except UnicodeError:
        puny = host
    return host, puny, userinfo


def userinfo_warning(host: str) -> str:
    return f"⚠ в адресе есть часть до @ — запрос уйдёт на {host or '⟨нет хоста⟩'}"


def card_text(tool: str, data: dict, *, mark: bool = True) -> str:
    """Вызов для карточки — по инструменту (`mark=False` — без пометок, для
    размера): команда — как есть; WebFetch — адрес и что найти; Write —
    «Записать файл: путь» и содержимое; Edit/MultiEdit — «Изменить файл:
    путь» и строки «− старое» / «+ новое» (обе стороны целиком, replace_all —
    явно); MCP и прочие — аргументы JSON с отступами без двойного
    экранирования. Неизвестные параметры показываются всегда."""
    data = data if isinstance(data, dict) else {}
    if tool in SHELL:
        command = str(data.get("command") or "")
        return display_text(command, shell=True) if mark else command
    if tool in WEB_FETCH and isinstance(data.get("url", ""), str) and isinstance(data.get("prompt", ""), str):
        # Первая строка — всегда хост, который Meet сам вынул из адреса (ревью GP1):
        # куда уйдёт запрос, видно до всего, что написал агент; часть до «@» —
        # предупреждением. Текст запроса — строками с «│ », так что он не
        # изобразит строку Meet.
        url = str(data.get("url") or "")
        host, puny, userinfo = web_host(url)
        lines = [f"хост: {_inline(host, mark) if host else '⟨нет⟩'}" + (f" ({puny})" if puny != host else "")]
        if userinfo:
            lines.append(userinfo_warning(host))
        lines.append(f"адрес: {_inline(url, mark)}")
        if data.get("prompt"):
            lines += ["что найти:", *("│ " + x for x in _block(data["prompt"], mark))]
        return "\n".join(lines + _others(data, ("url", "prompt"), mark))
    if tool in FILE_WRITE:
        text = _file_text(tool, data, mark)
        if text is not None:
            return text
    return pretty_args(data, mark)


def card_for(tool: str, data: dict, grant: tuple[str, str] | None = None) -> dict:
    """Карточка подтверждения — из настоящего вызова, не из текста агента.
    `args` — вызов целиком (`card_text`: по инструменту, с пометками
    пробелов и переводов строк), `preview` — начало и конец длинного вызова
    (None — короткий), `size` — «3 строки, 812 симв.», `warnings` — необычные
    параметры команды, `grant` — что разрешит «до конца встречи» (None — не
    предлагается)."""
    data = data if isinstance(data, dict) else {}
    kind, what = describe(tool, data)
    if tool in SHELL:
        title = "команду"
    elif tool in WEB_FETCH:
        title = "открыть адрес"
    elif tool in FILE_WRITE:
        title = "запись в файл" if tool == "Write" else "правку файла"
    elif tool.startswith("mcp__"):
        server, name = mcp_parts(tool)
        title = f"MCP {server} → {name}"
    elif tool == "Skill":
        title = "навык"
    else:
        title = tool
    args = card_text(tool, data)
    warnings = bash_warnings(tool, data)
    if tool in WEB_FETCH:
        host, _puny, userinfo = web_host(str(data.get("url") or ""))
        if userinfo:
            warnings.append(userinfo_warning(host))      # и крупно над вызовом
    return {"tool": tool, "title": title, "args": args, "preview": card_preview(args),
            "size": _size(card_text(tool, data, mark=False)),
            "warnings": warnings, "what": what, "kind": kind,
            "grant": {"key": grant[0], "label": grant[1]} if grant else None}


# Подписи решения для строки вызова в чате (0.4, «Ход работы — как в Claude CLI»).
_WHY_LABELS = {
    "excluded": "закрытая папка", "sensitive": "закрытые данные", "background": "фоновое выполнение",
    "local": "локальный адрес", "question": "вопрос не через чат", "hidden": "скрытые символы",
    "too-long": "слишком длинный вызов", "ask": "нужна ваша просьба", "declined": "вы отклонили",
    "timeout": "нет ответа", "unseen": "мимо проверки Meet", "no-card": "некуда показать карточку",
    DELETE: "удаление", SEND: "отправка наружу", "outside": "запись вне рабочих папок",
    "sandbox": "без песочницы", "mcp-write": "изменение через MCP", "mcp-address": "адрес в MCP",
    "recursive": "обход рядом с закрытым", UNPARSED: "непонятная команда",
}


def why_label(why: str) -> str:
    """Причина решения ворот словами («закрытые данные», «удаление»…); нет
    такой — пусто (строка вызова в чате, `assist.tool_rows.gate_view`)."""
    return _WHY_LABELS.get(why or "", "")


@dataclass
class Decision:
    """`outcome`: allow (Meet пропускает сам) / auto (решает CLI: правила
    пользователя и автомод) / deny / ask (нужна карточка); `reason` — для
    модели; `what` — «что агент хотел»; `kind` — вид вызова (`describe`);
    `why` — причина: у отказа (ask, excluded, sensitive, background, local,
    question, hidden, too-long, declined, timeout, unseen, no-card), у
    карточки — категория (delete, send, outside, sandbox, mcp-write,
    mcp-address, recursive; в режиме confirm — пусто), у разрешения —
    granted, granted-now, confirmed; `card` — карточка для `ask`."""
    outcome: str
    reason: str = ""
    what: str = ""
    kind: str = ""
    why: str = ""
    card: dict | None = field(default=None)

    @property
    def allow(self) -> bool:
        return self.outcome == ALLOW

    @property
    def passes(self) -> bool:
        """Вызов идёт дальше (Meet разрешил или отдал решение CLI)."""
        return self.outcome in (ALLOW, AUTO)

    @property
    def label(self) -> str:
        """Коротко для строки вызова в чате: «разрешено автоматически»,
        «действует сам», «разрешили вы», «спрашиваю вас: удаление»,
        «запрещено: закрытые данные»."""
        why = _WHY_LABELS.get(self.why, "")
        if self.outcome == AUTO:
            return "действует сам"
        if self.outcome == ALLOW:
            return {"confirmed": "разрешили вы", "granted-now": "разрешили вы до конца встречи",
                    "granted": "разрешено до конца встречи"}.get(self.why, "разрешено автоматически")
        if self.outcome == ASK:
            return "спрашиваю вас" + (f": {why}" if why else "")
        return "запрещено" + (f": {why}" if why else "")

    def row(self) -> dict:
        """Решение ворот для записи вызова в журнале (`kind: "tool"`, поле `gate`):
        исход, причина и подпись — без текста вызова."""
        return {"outcome": self.outcome, "why": self.why, "label": self.label}


def _shown_dir(folder: str, written=None, cwd=None, *, parent: bool = False) -> str:
    """Папка для подписи разрешения — та, что на деле покрыта (ревью GP3):
    путь после разбора ссылок и соединений (`realpath`, с настоящим
    регистром). Не совпал с ключом разрешения — сам ключ (`folder`)."""
    try:
        text = os.path.expandvars(os.path.expanduser(str(written)))
        if not os.path.isabs(text) and cwd:
            text = os.path.join(str(cwd), text)
        if parent:
            text = os.path.dirname(os.path.normpath(text))
        if network_path(text) or network_path(folder):
            return os.path.normpath(folder)       # сетевая папка — без обращения к сети
        real = os.path.realpath(text)
        for prefix in ("\\\\?\\", "//?/"):
            if real.startswith(prefix):
                real = real[len(prefix):]
        if written is not None and _norm_text(real) == folder:
            return real
    except (TypeError, ValueError, OSError):
        pass
    return os.path.normpath(folder)


def _canonical(tool: str, data) -> str:
    try:
        return tool + "\n" + json.dumps(data, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return tool + "\n" + repr(data)


class ConsentGate:
    """Ворота согласия одного сеанса агента (потокобезопасно: решения
    спрашивают потоки ответов на запросы CLI).

    `own_dirs` — папка записи: читать и править можно всегда, `.env` и
    служебные файлы внутри неё — не закрыты (временная встреча лежит в папке
    данных Meet); `work_dirs` — другие рабочие папки (база знаний): читать
    без просьбы, править в ходе USER — но закрытое внутри них закрыто; `cwd`
    — рабочая папка CLI (служебная; от неё — относительные пути; тоже
    рабочая); папки, где пользователь разрешил правку «до конца встречи»
    (`FILES_GRANT`), — тоже рабочие. `deny_paths` — закрыто всегда
    (`kb_exclude`); `sensitive` — закрыто всегда (по умолчанию
    `sensitive_paths()`); `allow_paths` — файлы, которые приложил
    пользователь (только чтение). `confirmer(card) -> "allow" |
    "allow_meeting" | "deny" | "timeout" | "cancelled"` — показать карточку и
    дождаться решения (блокирующее; ставит вызывающий). `mode` — как
    действует ассистент в ходе USER: `auto` (решает CLI, карточка — только
    рискованное) или `confirm` (карточка на каждое действие, как 0.3.7).
    `ask_text` — отказ в ходе без просьбы (`{what}` — что агент хотел)."""

    def __init__(self, *, own_dirs=(), work_dirs=(), deny_paths=(), sensitive=None, cwd=None, log=None,
                 confirmer=None, mode: str = MODE_AUTO, ask_text: str = ASK_FIRST) -> None:
        self._cwd = str(cwd) if cwd else None
        self._own = [r for r in (self._root(d) for d in own_dirs or () if d) if r]
        self._work = [r for r in (self._root(d) for d in work_dirs or () if d) if r]
        # Папки на сетевом диске, которые задал человек (база знаний на `\\nas\kb`):
        # пути внутри них сравниваются по тексту, без обращения к сети (I2).
        self._unc_roots = [r for r in (*self._own, *self._work) if r.startswith("//")]
        # Папка для подписи разрешения — настоящая (после ссылок), с регистром и «\».
        self._dir_shown = {r: _shown_dir(r, d, self._cwd) for d in (*(own_dirs or ()), *(work_dirs or ())) if d
                           for r in [self._root(d)] if r}
        if self._cwd:
            own_cwd = resolve(self._cwd)
            if own_cwd:
                self._own.append(own_cwd)
                self._dir_shown.setdefault(own_cwd, _shown_dir(own_cwd, self._cwd))
        self._deny = [r for r in (self._root(d) for d in deny_paths or () if d) if r]
        sens = sensitive_paths() if sensitive is None else sensitive
        self._sensitive = [r for r in (self._root(d) for d in sens or () if d) if r]
        self._mode = mode if mode in MODES else MODE_AUTO
        self._ask_text = ask_text or ASK_FIRST
        self._attached: set[str] = set()
        self._lock = threading.RLock()   # решение берёт замок и внутри (разрешения)
        self._level = NONE
        self._denials: list[Decision] = []
        self._approved: dict[str, int] = {}
        self._seen: set[str] = set()
        # «Разрешать такое до конца встречи»: ключ → подпись; папки, куда
        # человек уже разрешал запись (карточкой).
        self._grants: dict[str, str] = {}
        self._approved_dirs: set[str] = set()
        self._log = log or (lambda _m: None)
        self.confirmer = confirmer
        self.calls = 0

    def _root(self, folder) -> str | None:
        """Папка из настроек (запись, база, закрытая): как `resolve`, а
        сетевая — по тексту (`_unc_text`), без обращения к сети."""
        return resolve(folder, self._cwd) or _unc_text(folder)

    def _resolve(self, path) -> str | None:
        """Путь из вызова агента: `resolve`; сетевой — только внутри сетевой
        рабочей папки, которую задал человек (по тексту), иначе None (отказ)."""
        r = resolve(path, self._cwd)
        if r is None and self._unc_roots:
            unc = _unc_text(path)
            if unc and any(_inside(unc, root) for root in self._unc_roots):
                return unc
        return r

    @property
    def level(self) -> str:
        with self._lock:
            return self._level

    @property
    def mode(self) -> str:
        """`auto` / `confirm` — как действует ассистент в ходе USER
        (`claude_stream` берёт отсюда `--permission-mode`)."""
        return self._mode

    def begin(self, level: str) -> None:
        """Ход начинается: согласие на этот ход (NONE / READ / USER)."""
        with self._lock:
            self._level = level if level in _RANK else NONE
            self._seen = set()

    def end(self) -> None:
        """Ход кончился: согласие снято, неиспользованные разрешения карточек — тоже."""
        with self._lock:
            self._level = NONE
            self._approved.clear()

    def allow_paths(self, paths) -> None:
        with self._lock:
            self._attached.update(p for p in (self._root(x) for x in paths or () if x) if p)

    def add_grant(self, key: str, label: str = "") -> None:
        with self._lock:
            self._grants[str(key)] = label or str(key)

    def remove_grant(self, key: str) -> None:
        with self._lock:
            self._grants.pop(str(key), None)

    def grants(self) -> dict[str, str]:
        with self._lock:
            return dict(self._grants)

    def _granted_dirs(self) -> list[str]:
        with self._lock:
            keys = list(self._grants)
        return [k[len(FILES_GRANT):] for k in keys if k.startswith(FILES_GRANT)]

    def _work_roots(self) -> list[str]:
        """Рабочие папки: запись, база знаний, служебная папка CLI и папки с
        разрешением на правку «до конца встречи»."""
        return [*self._own, *self._work, *self._granted_dirs()]

    def grant_for(self, tool: str, data: dict) -> tuple[str, str] | None:
        """Что разрешит «до конца встречи» для такого вызова (None — не
        предлагается): тот же MCP-инструмент; тот же домен WebFetch; то же
        первое слово простой команды (удаление и команды без песочницы —
        никогда); изменение файлов любым инструментом правки (Write, Edit,
        MultiEdit, NotebookEdit) в рабочей папке или в папке, где запись уже
        разрешали, — кроме закрытого внутри."""
        data = data if isinstance(data, dict) else {}
        if tool.startswith("mcp__"):
            server, name = mcp_parts(tool)
            return f"mcp:{tool}", f"MCP {server}: {name}"
        if tool in WEB_FETCH:
            host = url_host(str(data.get("url") or ""))
            return (f"web:{host}", f"веб: {host}") if host else None
        if tool in SHELL:
            if data.get("dangerouslyDisableSandbox") or bash_warnings(tool, data):
                return None
            command = str(data.get("command") or "")
            if shell_risk(tool, command, inside=self._inside_work) in _NO_GRANT:
                return None
            return shell_grant(tool, command)
        if tool in FILE_WRITE:
            target = self._write_target(data)
            if not target:
                return None
            with self._lock:
                folders = [*self._own, *self._work, *self._approved_dirs]
                shown = dict(self._dir_shown)
            for folder in folders:
                if _inside(target, folder) and self._files_grantable(target, folder):
                    return f"{FILES_GRANT}{folder}", f"изменение файлов в {_inline(shown.get(folder, folder), True)}"
            return None
        return None

    def _write_target(self, data: dict) -> str | None:
        return self._resolve(data.get("file_path") or data.get("notebook_path") or "")

    def _files_grantable(self, target: str, folder: str) -> bool:
        """Разрешение на правки в `folder` покрывает `target`, только если
        цель не закрыта ничем, что уже внутри этой папки: `kb_exclude`,
        чувствительный путь во вложенной папке, `.env`. (Сама папка встречи
        может лежать в служебной папке Meet — временная встреча; её «свою»
        запись это не меняет.)"""
        if any(_inside(target, d) for d in self._deny) or _env_file(target):
            return False
        return not any(_inside(target, d) and not _inside(folder, d) for d in self._sensitive)

    def _writable(self, target: str | None) -> bool:
        """Цель записи — в рабочей папке и не закрыта внутри неё."""
        return bool(target) and any(_inside(target, d) and self._files_grantable(target, d)
                                    for d in self._work_roots())

    def _inside_work(self, path: str) -> bool:
        return self._writable(self._resolve(path))

    def _granted(self, tool: str, data: dict) -> bool:
        with self._lock:
            grants = dict(self._grants)
        if not grants:
            return False
        if tool in FILE_WRITE:
            target = self._write_target(data)
            if not target:
                return False
            # Прежние ключи `write:<инструмент>:` (журналы до grant-polish) — только для своего инструмента.
            for k in grants:
                for prefix in (FILES_GRANT, f"write:{tool}:"):
                    if k.startswith(prefix) and _inside(target, k[len(prefix):]) and \
                            self._files_grantable(target, k[len(prefix):]):
                        return True
            return False
        grant = self.grant_for(tool, data)
        return bool(grant) and grant[0] in grants

    def take_denials(self) -> list[Decision]:
        """Отказы с прошлого вызова (для строки в чате)."""
        with self._lock:
            out, self._denials = self._denials, []
        return out

    def unseen(self, tool_use_ids) -> list[str]:
        """Вызовы хода, которые не прошли через хук (дыра в проверке)."""
        with self._lock:
            return [t for t in tool_use_ids or () if t and t not in self._seen]

    # --- решение без ожидания

    def _own_path(self, path: str | None) -> bool:
        return bool(path) and (any(_inside(path, d) for d in self._own) or path in self._attached)

    def _free_read(self, path: str | None) -> bool:
        """Читать можно и без просьбы: рабочие папки и вложения."""
        return bool(path) and (self._own_path(path) or any(_inside(path, d) for d in self._work_roots()))

    def _protected(self, path: str | None) -> str:
        if path is None:
            return "unresolved"
        if any(_inside(path, d) for d in self._deny):
            return "excluded"
        if self._own_path(path):
            return ""
        if any(_inside(path, d) for d in self._sensitive) or _env_file(path):
            return "sensitive"
        return ""

    def _paths(self, tool: str, data: dict) -> list[str | None]:
        out: list[str | None] = []
        cwd = self._cwd
        for key in ("file_path", "notebook_path"):
            if isinstance(data.get(key), str) and data[key].strip():
                out.append(self._resolve(data[key]))
        base = data.get("path") if isinstance(data.get("path"), str) and data["path"].strip() else None
        if tool == "Glob":
            pattern = data.get("pattern") if isinstance(data.get("pattern"), str) else ""
            root, escapes = _glob_root(pattern)
            start = self._resolve(base) if base else resolve(cwd or os.getcwd())
            expanded = os.path.expanduser(root) if root else ""
            if escapes:
                out.append(None)
            elif expanded and (os.path.isabs(expanded) or re.match(r"^[A-Za-z]:", expanded)
                               or expanded.startswith(("/", "\\"))):
                out.append(self._resolve(expanded))
            elif start:
                out.append(self._resolve(os.path.join(start, root)) if root else start)
            else:
                out.append(None)
        elif tool in FILE_READ or tool in FILE_WRITE:
            if base:
                out.append(self._resolve(base))
            elif tool in ("Grep", "LS"):
                out.append(resolve(cwd or os.getcwd()))
        return out

    def _mentions(self, text: str, *, parents: bool = True) -> str:
        """Команда упоминает закрытую папку (или папку над ней — корень базы;
        `parents=False` — у команды чтения с буквальными путями, их проверяет
        `_protected`), чувствительный путь, токен Meet, `.env` или локальный адрес.
        Пути сверяются и как написаны, и нормализованными (`_path_words`)."""
        raw = str(text or "")
        low = _norm_text(raw + " " + _path_words(raw, self._cwd))
        home = _norm_text(str(_home()))

        def forms(p: str) -> list[str]:
            out = [p]
            m = re.match(r"^([a-z]):(/.*)$", p, re.IGNORECASE)
            if m:
                out.append(f"/{m.group(1).lower()}{m.group(2)}")
            if p.startswith(home + "/"):
                rest = p[len(home):]
                out += ["~" + rest, "$home" + rest, "${home}" + rest, "%userprofile%" + rest,
                        "$env:userprofile" + rest]
            return out

        for d in self._deny:
            parent = d.rsplit("/", 1)[0]
            for f in (*forms(d), *(forms(parent) if parents and "/" in parent.strip("/") else ())):
                if f and f in low:
                    return "excluded"
        for d in self._sensitive:
            for f in forms(d):
                if f and f in low:
                    return "sensitive"
        if "api.token" in low or (_ENV_IN_TEXT.search(low) and ".env.example" not in low):
            return "sensitive"
        if mentions_local(str(text or "")):
            return "local"
        return ""

    def decide(self, tool: str, data=None) -> Decision:
        data = data if isinstance(data, dict) else {}
        tool = str(tool or "")
        kind, what = describe(tool, data)
        with self._lock:
            level = self._level
            self.calls += 1
            decision = self._decide(tool, data, kind, what, level)
            if decision.outcome == DENY and decision.why != "question":
                self._denials.append(decision)
        if decision.outcome == DENY:
            # В журнал процесса — только вид и исход, без текста вызова (CLAUDE.md).
            self._log(f"согласие: отказ ({level}, {decision.why}, {kind})")
        return decision

    def _deny_why(self, why: str, what: str, kind: str, level: str = NONE) -> Decision:
        if why == "ask":
            text = ASK_FIRST_READ if level == READ else self._ask_text
        else:
            text = {"excluded": EXCLUDED, "sensitive": SENSITIVE, "unresolved": SENSITIVE,
                    "background": BACKGROUND, "local": LOCAL}[why]
        return Decision(DENY, text.format(what=what), what, kind,
                        "sensitive" if why == "unresolved" else why)

    def _read_plan(self, tool: str, data: dict) -> Decision | str | None:
        """Команда только чтения: отказ (закрытый путь), «recursive» (обход
        над закрытым), "" (чтение) или None (не команда чтения)."""
        if tool not in SHELL or bash_warnings(tool, data):
            return None
        plan = read_plan(tool, str(data.get("command") or ""))
        if plan is None:
            return None
        paths_, recursive = plan
        resolved = [self._resolve(x) for x in paths_]
        for r in resolved:
            why = self._protected(r)
            if why:
                return why
        roots = resolved or [resolve(self._cwd or os.getcwd())]
        if recursive and any(_inside(d, r) for r in roots for d in (*self._deny, *self._sensitive) if r):
            return "recursive"
        return ""

    def _risk(self, tool: str, data: dict, kind: str, force_card: bool) -> str:
        """Категория карточки в ходе USER (спец. §2): delete, send, outside,
        sandbox, mcp-write, mcp-address; "" — решает режим."""
        if tool in FILE_WRITE:
            return "" if self._writable(self._write_target(data)) else "outside"
        if tool in SHELL:
            if data.get("dangerouslyDisableSandbox") or bash_warnings(tool, data):
                return "sandbox"
            return shell_risk(tool, str(data.get("command") or ""), inside=self._inside_work)
        if kind == "mcp":
            return "mcp-write"
        if kind in ("mcp-read", "mcp-resource") and force_card:
            return "mcp-address"
        return ""

    def _ask(self, tool: str, data: dict, what: str, kind: str, why: str = "") -> Decision:
        grant = None if why in (*_NO_GRANT, "sandbox", "recursive") else self._grant_offer(tool, data)
        return Decision(ASK, what=what, kind=kind, why=why, card=card_for(tool, data, grant))

    def _decide(self, tool: str, data: dict, kind: str, what: str, level: str) -> Decision:
        if kind == "question":
            return Decision(DENY, NO_QUESTIONS, what, kind, "question")
        hidden = [c for k, v in _pairs(data) for c in hidden_chars(v, shell=tool in SHELL and k == "command")]
        if hidden:
            shown = ", ".join(dict.fromkeys(hidden).keys())
            return Decision(DENY, HIDDEN_CHARS.format(what=what.encode("ascii", "replace").decode()[:200],
                                                      chars=shown), f"{tool} со скрытыми символами ({shown})",
                            kind, "hidden")
        if kind == "background" or (tool in SHELL and data.get("run_in_background")):
            return self._deny_why("background", what, kind)
        paths = self._paths(tool, data)
        for p in paths:
            why = self._protected(p)
            if why:
                return self._deny_why(why, what, kind)
        if kind in ("shell", "other", "skill"):
            literal = tool in SHELL and read_plan(tool, str(data.get("command") or "")) is not None
            why = self._mentions(" ".join(str(v) for v in data.values() if isinstance(v, (str, int, float))),
                                 parents=not literal)
            if why:
                return self._deny_why(why, what, kind)
        if kind == "web-fetch":
            url = str(data.get("url") or "")
            host = urlsplit(url).hostname or ""
            if not url.lower().startswith(("http://", "https://")) or not host or is_local_host(host):
                return self._deny_why("local", what, kind)
        force_card = False
        if kind in ("mcp", "mcp-read", "mcp-resource"):
            why, force_card = self._mcp_review(tool, data, kind)
            if why:
                return self._deny_why(why, what, kind)
        if kind == "internal":
            return Decision(ALLOW, what=what, kind=kind)
        if kind == "read" and paths and all(self._free_read(p) for p in paths):
            return Decision(ALLOW, what=what, kind=kind)
        # Ход только по репликам встречи: ничего, кроме чтения рабочих папок, —
        # что бы ни прозвучало (инъекция из речи), в любом режиме.
        if level not in (READ, USER):
            return self._deny_why("ask", what, kind)
        raw = raw_args(tool, data)
        if len(raw) > ARGS_LIMIT:
            return Decision(DENY, TOO_LONG.format(what=what[:200], size=len(raw), limit=ARGS_LIMIT), what[:200],
                            kind, "too-long")
        plan = self._read_plan(tool, data)
        if plan not in (None, "", "recursive"):
            return self._deny_why(plan, what, kind)
        reading = (kind in ("read", "web-search") or plan == ""
                   or (kind in ("mcp-read", "mcp-resource") and not force_card))
        if level == READ:
            # ❓ «Поясни»: только чтение, карточек нет.
            if reading:
                return Decision(ALLOW, what=what, kind="shell-read" if plan == "" else kind)
            return self._deny_why("ask", what, kind, READ)
        # USER — просьба пользователя.
        if plan == "":
            return Decision(ALLOW, what=what, kind="shell-read")
        if plan == "recursive":
            return self._ask(tool, data, what, kind, "recursive")
        risk = self._risk(tool, data, kind, force_card)
        if risk:
            if risk not in (*_NO_GRANT, "sandbox") and self._granted(tool, data):
                return Decision(ALLOW, what=what, kind=kind, why="granted")
            return self._ask(tool, data, what, kind, risk)
        if self._mode == MODE_CONFIRM:
            # Как 0.3.7: чтение — само, остальное — карточкой (или по разрешению «до конца»).
            if kind in ("read", "web-search", "mcp-read", "mcp-resource"):
                return Decision(ALLOW, what=what, kind=kind)
            if self._granted(tool, data):
                return Decision(ALLOW, what=what, kind=kind, why="granted")
            return self._ask(tool, data, what, kind)
        if kind == "read":
            return Decision(ALLOW, what=what, kind=kind)
        return Decision(AUTO, what=what, kind=kind)

    def _grant_offer(self, tool: str, data: dict):
        try:
            return self.grant_for(tool, data)
        except Exception:
            return None

    def _mcp_review(self, tool: str, data: dict, kind: str) -> tuple[str, bool]:
        """Аргументы MCP: (почему отказ, нужна ли карточка). Отказ — закрытые и
        чувствительные пути (и после разбора `..`), токен Meet, `.env`,
        локальные адреса. Карточка — адрес, хост или почта в аргументе (кроме
        домена, разрешённого «до конца встречи»), относительный путь с `..`,
        `file:`; у чтения ресурса — `file:` и http(s)."""
        with self._lock:
            web = {k[4:] for k in self._grants if k.startswith("web:")}
        card = False
        for key, value in _pairs(data):
            why = self._mentions(value)
            if why:
                return why, False
            if re.search(r"(^|[\\/])\.\.([\\/]|$)", value):
                if os.path.isabs(value.strip()) or re.match(r"^\s*[A-Za-z]:", value):
                    normalized = os.path.normpath(value.strip())
                    why = self._mentions(normalized) or self._protected(self._resolve(normalized))
                    if why and why != "unresolved":
                        return why, False
                else:
                    card = True
            stripped = value.strip()
            if not stripped:
                continue
            if re.match(r"(?i)file:", stripped) or re.match(r"^[\\/]{2}[^\\/\s]", stripped):
                card = True               # `file:` и сетевой путь (`\\хост\…`, I2)
                continue
            if kind == "mcp-resource" and key == "uri" and not re.match(r"(?i)https?://", stripped):
                continue          # свои схемы ресурсов (jira://…) — чтение
            if _URLISH.match(stripped) or (key in _URL_KEYS and re.search(r"[\w-]\.[a-z]{2,}", stripped, re.I)):
                host = url_host(stripped)
                if host and is_local_host(host):
                    return "local", False
                if not host or host not in web:
                    card = True
        return "", card

    # --- решение с карточкой (ответ CLI)

    def check(self, tool: str, data=None, *, tool_use_id: str | None = None, via: str = "hook") -> Decision:
        """Ответ на хук PreToolUse (`via="hook"`) или `can_use_tool`.

        Хук: `allow` / `auto` / `deny`; `ask` — карточка: ждём решения
        человека (блокирующее, в потоке ответа CLI), «Разрешить» → `allow`
        (человек решил — классификатор не спрашивается), разрешение — на
        этот один вызов. `can_use_tool` (вопрос самого CLI: классификатор
        отказал, правило `ask`, защищённый путь, Manual без автомода) — в
        ходе USER всегда карточка, если хук этот вызов видел; не видел — отказ
        `unseen` (дыра в проверке), кроме того, что Meet пропускает сам. В
        ходах NONE и READ карточек нет: `allow` или отказ."""
        data = data if isinstance(data, dict) else {}
        tool = str(tool or "")
        canonical = _canonical(tool, data)
        key = tool_use_id or canonical
        with self._lock:
            if via == "hook":
                self._seen.update(k for k in (tool_use_id, canonical) if k)
            else:
                keys = {key, canonical}
                if any(self._approved.get(k) for k in keys):
                    for k in keys:     # одно разрешение — один вызов, по любому из ключей
                        if self._approved.get(k):
                            self._approved[k] -= 1
                    return Decision(ALLOW, what=describe(tool, data)[1], kind="approved", why="confirmed")
                seen = (tool_use_id in self._seen) if tool_use_id else canonical in self._seen
                level = self._level
        decision = self.decide(tool, data)
        if decision.outcome == DENY:
            return decision
        if via != "hook":
            if level != USER:
                if decision.outcome == ALLOW:
                    return decision
                return self._record(self._deny_why("ask", decision.what, decision.kind, level))
            if not seen:
                if decision.outcome == ALLOW:
                    return decision
                self._log(f"согласие: вызов {decision.kind} не прошёл через хук — отказ")
                return self._record(Decision(DENY, UNSEEN.format(what=decision.what), decision.what,
                                             decision.kind, "unseen"))
            if decision.outcome != ASK:
                decision = Decision(ASK, what=decision.what, kind=decision.kind, why=decision.why,
                                    card=card_for(tool, data, self._grant_offer(tool, data)))
        elif decision.outcome != ASK:
            return decision
        confirmer = self.confirmer
        if confirmer is None:
            return self._record(Decision(DENY, NO_CARD.format(what=decision.what), decision.what,
                                         decision.kind, "no-card"))
        card_shown = dict(decision.card or card_for(tool, data))
        if tool_use_id:
            card_shown["tool_use_id"] = str(tool_use_id)   # кнопки — в строке этого вызова в чате
        try:
            answer = confirmer(card_shown)
        except Exception as e:  # карточка не показалась — отказ
            self._log(f"согласие: карточка не показана ({type(e).__name__})")
            answer = "cancelled"
        if answer in (ALLOW, ALLOW_MEETING):
            card = decision.card or {}
            with self._lock:
                for k in {key, canonical}:
                    self._approved[k] = self._approved.get(k, 0) + 1
                if tool in FILE_WRITE:
                    target = self._resolve(data.get("file_path") or data.get("notebook_path") or "")
                    if target:
                        folder = target.rsplit("/", 1)[0]
                        self._approved_dirs.add(folder)
                        self._dir_shown.setdefault(folder, _shown_dir(
                            folder, data.get("file_path") or data.get("notebook_path"), self._cwd, parent=True))
                if answer == ALLOW_MEETING and card.get("grant"):
                    self._grants[card["grant"]["key"]] = card["grant"]["label"]
            return Decision(ALLOW, what=decision.what, kind=decision.kind,
                            why="granted-now" if answer == ALLOW_MEETING else "confirmed")
        if answer == "timeout":
            text = TIMED_OUT.format(minutes=max(1, round(CONFIRM_TIMEOUT_S / 60)), what=decision.what)
            return Decision(DENY, text, decision.what, decision.kind, "timeout")
        return Decision(DENY, DECLINED.format(what=decision.what), decision.what, decision.kind, "declined")

    def _record(self, decision: Decision) -> Decision:
        with self._lock:
            self._denials.append(decision)
        return decision


def denial_line(denials) -> str:
    """Строка в чат о заблокированных вызовах хода (без повторов, до трёх).
    Отклонённые человеком карточки строкой не повторяются: их видно в карточке."""
    denials = [d for d in denials or () if d.why not in ("declined", "timeout", "question")]
    seen: list[str] = []
    for d in denials:
        what = " ".join(str(d.what or "").split())
        if what and what not in seen:
            seen.append(what)
    if not seen:
        return ""
    shown = "; ".join(seen[:3]) + (f" и ещё {len(seen) - 3}" if len(seen) > 3 else "")
    whys = {d.why for d in denials}
    why = ("в закрытой папке" if whys == {"excluded"}
           else "закрытые данные" if whys <= {"sensitive"}
           else "в фоне" if whys == {"background"} else "без согласия")
    return f"Ассистент хотел {why}: {shown} — запрос заблокирован"
