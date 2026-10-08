"""Постоянный диалог с Claude Code: один процесс CLI на всю встречу.

Подсказкам живого ассистента нужен контекст всего разговора, а не только
последней минуты, и нужен быстро. Отдельный вызов на каждый тик — это
секунда на старт процесса и весь разговор заново в каждом запросе. Здесь —
один процесс `claude -p --input-format stream-json --output-format
stream-json` на встречу: каждый тик — новое сообщение пользователя в том же
диалоге (только новые реплики), ответ приходит потоком, кусками текста.

Процесс:

* без инструментов (`--tools ""`), без настроек и MCP пользователя, без
  слэш-команд, один ход на сообщение;
* не сохраняет сеанс на диск (`--no-session-persistence`): разговор живёт
  только в памяти процесса и в историю Claude Code человека не попадает;
* рабочая папка — служебная пустая папка, не папка встречи: такой диалог
  нечем было бы «продолжить» из вкладки «Агент»;
* окружение — как у разовых вызовов (`llm.claude`): без ANTHROPIC_API_KEY и
  меток чужого сеанса, с прокси из настроек;
* привязан к процессу ассистента (`meet.procjob`): ассистент умер — умер и
  он; штатно его закрывает `close()` (конец stdin, затем — убить).

Сбой процесса или таймаут хода — ошибка ответа и мёртвый диалог: следующий
`send` поднимает новый процесс (вызывающий начинает его с затравки).

V4 (0.3.6, v4-design §3, §5.1, §12; v4-simple §1, §6):

* **агент-собеседник** (`responder=True`) сам читает и ищет своими
  инструментами: `--restricted --tools Read,Grep,Glob --allowedTools
  Read,Grep,Glob --permission-mode dontAsk --add-dir <папка>… --max-turns
  12`. Папки (`add_dirs`) передаёт вызывающий: папка встречи, база знаний,
  библиотека встреч. Закрытые для чтения (`deny_paths`, `kb_exclude`) —
  правила `--disallowedTools Read(//…/**)`: запрет сильнее `--allowedTools`,
  `--add-dir` и `dontAsk`; Grep и Glob CLI проверяет теми же Read-правилами.
  Папки и запреты на сеанс постоянны; поменялись — новый процесс с
  `--resume` (новый `Conversation(resume=…)`). Слушатель — как раньше;
* **изображения**: `send(text, images=[…])` или `send(content=[блоки])` —
  блоки `{"type":"image","source":{"type":"base64",…}}` перед текстом. Тип —
  по первым байтам, предел 5 МБ — по длине base64, сторона ≤ 8 000 px;
  негодное изображение не уходит, модели и окну — пометка (`notes`). Если
  изображение отверг API, сохранённый сеанс не «отравляется»: процесс
  поднимается с `--resume=<id> --resume-session-at=<последняя запись
  прошлого хода> --fork-session --resume-drops-turn=<этот ход>` (ход с
  картинкой выброшен; новый id сеанса) и ход повторяется без изображений;
* **остановка хода**: `interrupt()` — `control_request` с `subtype:
  interrupt` (формат — `claude_agent_sdk` `Query.interrupt`), ждём
  `control_response`. Ход `cancelled`, только если он и правда прерван
  (`result` с ошибкой или конец потока); успевший закончиться ход — обычный
  ответ. Нет подтверждения за INTERRUPT_WAIT_S — процесс убит и поднят заново;
* **нативное продолжение** (`persist=True`): сеанс сохраняется (без
  `--no-session-persistence`), новый — с нашим UUID (`--session-id=`),
  после перезапуска процесса и с `resume=<id>` — `--resume=<id>`. Id —
  `session_id`. Продолжить не вышло — ответ `resume_failed`, диалог
  забывает id: следующий `send` начнёт новый сеанс (вызывающий — с
  затравкой из журнала). Рабочая папка сохраняемых сеансов — постоянная
  (`workdir()`): старые CLI ищут сеанс только в папке проекта.

0.3.7 (A1) — **свобода по согласию** (`gate=ConsentGate`, собеседник):

* все инструменты CLI, настройки и MCP-серверы пользователя — как в его
  собственном Claude Code: без `--restricted`, `--tools`,
  `--setting-sources ""`, `--strict-mcp-config` и `--disable-slash-commands`;
  `--permission-mode default` (поверх `defaultMode` из настроек
  пользователя) и `--permission-prompt-tool stdio` — разрешения CLI
  спрашивает у нас (`control_request` `can_use_tool`). `--add-dir` — только
  папка встречи; `kb_exclude` — те же правила `Read(//…/**)`;
* сразу после запуска процесса — `control_request` `initialize` с хуком
  PreToolUse (как `claude_agent_sdk`): хук приходит на **каждый** вызов
  инструмента, в том числе на те, что CLI разрешил бы сам (чтение вне
  `--add-dir`, `ls`, правила `allow` пользователя), — и хук, и `can_use_tool`
  отвечают решением `ConsentGate.check` (согласие ставит вызывающий на ход:
  `gate.begin(level)` / `gate.end()`). Вызовы, которым нужна карточка
  подтверждения, ответ держат, пока человек не решит: каждый запрос CLI
  отвечается в своём потоке, чтение вывода не останавливается;
* хуки самого пользователя выключены (`--settings <файл>` с
  `disableAllHooks: true`): они получали бы текст встречи и могли бы
  действовать мимо ворот; хук Meet при этом работает (проверено без модели,
  `scripts/probe_claude_gate.py`);
* вызовы инструментов хода, которые не прошли через хук (`tool_use` в
  ответе модели без PreToolUse), — в журнал процесса: дыра в проверке видна. Проверено на claude 2.1.292 без
  модели (поддельный API): хук на Read/Bash/MCP/Write/WebFetch,
  `can_use_tool` — на запись, команду с записью, MCP без правила, WebFetch;
  отказ хука доходит до модели результатом инструмента с нашей причиной;
  MCP-серверы пользователя загружаются (`system/init` → `mcp_servers`,
  они — `mcp_servers` диалога). Нет ответа на `initialize` — процесс убит,
  ход — ошибка (старый CLI без хуков ворота обошёл бы).

0.4 — **ассистент как CLI** (спецификация 2026-10-08, §3 и §6; факты сверены
пробником `scripts/probe_claude_gate.py` на claude 2.1.293 без модели):

* режим (`mode`): `auto` — `--permission-mode auto`, `confirm` — `default`
  (как 0.3.7). Хук PreToolUse — единственные ворота Meet; его ответ по
  исходу ворот: `auto` → `{}` (дальше правила пользователя и классификатор
  автомода), `allow` (Meet разрешил или человек — по карточке) →
  `permissionDecision: "allow"` (CLI не спрашивает; правила `deny` CLI всё
  равно сильнее — проверено), `deny` → отказ с причиной. `can_use_tool`
  (вопрос самого CLI) — решение ворот (в ходе USER это всегда карточка);
* автомод недоступен (`disableAutoMode`, модель) — CLI молча стартует в
  `default`: это видно в ответе `initialize` (`current_permission_mode`) и
  в `system/init` (`permissionMode`), а смена по ходу — в `system/status`
  (`permissionMode`): `permission_mode`, `auto_unavailable`;
* ответ `initialize` — команды CLI (`commands`: имя, описание,
  `argumentHint`) и модели (`models`); `system/init` — `slash_commands`,
  `mcp_servers`, `permissionMode`;
* `control()` — control-запросы CLI, в том числе во время хода (ход не
  ждут): `mcp_status`, `mcp_reconnect` `{serverName}`, `mcp_toggle`
  `{serverName, enabled}`, `set_model` `{model}`, `set_permission_mode`
  `{mode}`, `list_models` — все есть в 2.1.293. Сжатия control-запросом нет
  («Unsupported control request subtype: compact») — `/compact` уходит
  сообщением (`compact()`), граница — `system/compact_boundary`
  (`compact_metadata.pre_tokens/post_tokens`) → `AgentReply.compacted`;
* `restart()` — новый процесс с `--resume` (разговор тот же, MCP-серверы
  поднимаются заново) — запасной путь, если `mcp_reconnect` нет или он
  не помог;
* ход работы в чате: `send(on_event=…)` — события `tool_use`,
  `tool_result`, `gate`, `hook` (см. `ToolTracker` и `_gate_event`).
"""

import asyncio
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from meet import netproxy, procjob
from meet.llm import detect
from meet.llm.base import (
    CANCELLED_ERROR, TIMEOUT_ERROR, AgentReply, claude_deny_rules, claude_model, drop_session_markers, is_uuid,
    model_matches, resume_failure,
)
from meet.llm.claude import dropped_fields, text_delta, user_content

STDERR_TAIL = 2000
CLOSE_WAIT_S = 2.0
WORKDIR = "meet-live"
# Сколько ждать `control_response` на остановку хода (v4-design §3.4).
INTERRUPT_WAIT_S = 5.0
# Собеседник: только чтение (claude 2.1.292 --help: --restricted убирает
# инструменты, выполняющие код, и WebFetch; файлы — только в рабочих папках и
# --add-dir; --permission-mode dontAsk — не спрашивать, а отказывать).
RESPONDER_TOOLS = "Read,Grep,Glob"
# Агент сам ищет в базе знаний: Glob → Grep → несколько Read — ходов нужно больше.
RESPONDER_MAX_TURNS = 12
# Свобода по согласию: MCP, веб, файлы, команды и правки (0.4 — автомод) — шагов больше.
FREE_MAX_TURNS = 40
# Как действует ассистент в ходе по просьбе (`assist.agent_mode`): режим CLI.
MODE_AUTO, MODE_CONFIRM = "auto", "confirm"
_PERMISSION_MODE = {MODE_AUTO: "auto", MODE_CONFIRM: "default"}
# Исход ворот «решает CLI» (`consent.AUTO`).
_AUTO = "auto"
# Сколько ждать ответа на control-запрос; переподключение MCP — дольше.
CONTROL_WAIT_S = 15.0
RECONNECT_WAIT_S = 60.0
# После перезапуска MCP-серверы поднимаются в фоне (`pending`): сколько ждать.
MCP_SETTLE_S = 20.0
# Вывод инструмента в событии `tool_result` — не больше (байт UTF-8).
OUTPUT_LIMIT = 64 * 1024
# Ответ CLI на неизвестный control-запрос (2.1.293).
_UNSUPPORTED = "unsupported control request subtype"
_LOCAL_OUTPUT = re.compile(r"<local-command-(?:stdout|stderr)>(.*?)</local-command-(?:stdout|stderr)>", re.DOTALL)
# Хук PreToolUse ворот согласия (id обратного вызова в `initialize`).
GATE_HOOK_ID = "meet_consent_gate"
# Начало ошибки хода, остановленного Meet: инструмент выполнился мимо хука ворот.
GATE_GAP_ERROR = "Meet остановил ход: вызов инструмента прошёл мимо проверки согласия"
# Сколько ждать ответа на `initialize` (MCP-серверы пользователя могут подниматься долго).
INIT_WAIT_S = 30.0
# Свой вопрос пользователю окном CLI агенту не нужен: он спрашивает say с кнопками.
# Фоновое выполнение (подагенты, фоновые команды, расписания) — тоже: оно
# пережило бы ход и его согласие (ворота отказывают и сами).
FREE_DISALLOWED = ("AskUserQuestion", "Agent", "Task", "BashOutput", "KillShell", "CronCreate",
                   "CronDelete", "ScheduleWakeup")
# Файл настроек свободного режима (`--settings`): хуки пользователя выключены.
FREE_SETTINGS = {"disableAllHooks": True}
FREE_SETTINGS_NAME = "meet-agent-settings.json"
# Что пишет CLI, когда сеанса для --resume нет (2.1.292, проверено без модели:
# пустая папка настроек, без входа): stderr и `errors` у result, код выхода 1.
_RESUME_MISSING = "no conversation found"
# Ошибка API про изображение (400: «image exceeds 5 MB», «Could not process
# image», «does not match the provided media type», …base64…).
_IMAGE_REJECTED = re.compile(r"image|media[_ ]?type|base64", re.IGNORECASE)


def workdir(name: str = WORKDIR) -> Path:
    """Служебная рабочая папка процессов модели (пустая, во временной папке
    системы: у ассистента свой корень временных файлов, а папка «Спросить»
    должна быть той же от встречи к встрече — по ней Claude Code ведёт сессии)."""
    from meet import tempdirs

    path = tempdirs.system_temp() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_cli() -> list[str] | None:
    """Команда запуска найденного Claude Code CLI; не найден — None."""
    exe = detect.find_claude()
    return [exe] if exe else None


def build_command(cli: list[str], *, system_prompt: str, model: str | None = None,
                  thinking: str | None = None, effort: str | None = None,
                  responder: bool = False, add_dirs=(), deny_paths=(), max_turns: int | None = None,
                  session_id: str | None = None, resume: str | None = None,
                  fork_at: str | None = None, drop_turn: str | None = None,
                  freedom: bool = False, settings_file: str | None = None,
                  mode: str = MODE_AUTO) -> list[str]:
    """Командная строка постоянного процесса (флаги сверены с claude 2.1.292
    `--help`; `--max-turns`, `--thinking`, `--resume-session-at`,
    `--resume-drops-turn` в справке не показаны — их передаёт
    claude_agent_sdk; усечение сеанса проверено без модели).

    Слушатель (по умолчанию) — без инструментов, один ход. Собеседник
    (`responder`) — чтение в `add_dirs` (папка встречи, база знаний,
    библиотека — что передал вызывающий), кроме `deny_paths` (правила
    `Read(//…/**)`), до RESPONDER_MAX_TURNS ходов.
    `freedom` (с `responder`, 0.3.7) — все инструменты, настройки и MCP
    пользователя, разрешения спрашиваются у нас (`--permission-prompt-tool
    stdio`; в справке скрыт — его передаёт claude_agent_sdk, проверен без
    модели), `add_dirs` — рабочие папки CLI. Режим (0.4) — `mode`: `auto`
    (`--permission-mode auto`, автомод Claude Code; вместе с
    `--permission-prompt-tool stdio` работает — 2.1.293) или `confirm`
    (`default`, как 0.3.7). `--include-hook-events` — события хуков в потоке
    (`system/hook_*`; хуки пользователя выключены `--settings`, так что это
    задел на хуки, которые CLI запустит сам).
    Сеанс: без `session_id`/`resume` — не сохраняется; `session_id` — новый
    сохраняемый с этим UUID; `resume` — продолжить сохранённый; с `fork_at` —
    новый сеанс-ветка до записи `fork_at` без хода `drop_turn`. Значения —
    через `=`: id не станет отдельным флагом (так же делает SDK).

    `--model=` — всегда, и при `--resume`: без него CLI взял бы модель по
    умолчанию, а при `--resume` — модель прежнего сеанса (claude 2.1.292
    восстанавливает её, только если модель не задана флагом или
    ANTHROPIC_MODEL). Пустая `model` — DEFAULT_CLAUDE_MODEL."""
    cmd = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--include-partial-messages",
           "--system-prompt", system_prompt]
    free = bool(responder and freedom)
    if free:
        cmd += ["--permission-mode", _PERMISSION_MODE.get(mode, "default"),
                "--permission-prompt-tool", "stdio", "--include-hook-events"]
        for d in add_dirs or ():
            if d:
                cmd += ["--add-dir", str(d)]
        cmd += ["--disallowedTools", *FREE_DISALLOWED, *claude_deny_rules(deny_paths)]
        if settings_file:
            cmd += ["--settings", str(settings_file)]
    elif responder:
        cmd += ["--restricted", "--tools", RESPONDER_TOOLS, "--allowedTools", RESPONDER_TOOLS,
                "--permission-mode", "dontAsk"]
        for d in add_dirs or ():
            if d:
                cmd += ["--add-dir", str(d)]
        rules = claude_deny_rules(deny_paths)
        if rules:
            # Флаг со многими значениями: правила — отдельными аргументами
            # (скобки CLI разбирает целиком), дальше сразу другой флаг.
            cmd += ["--disallowedTools", *rules]
    else:
        cmd += ["--tools", ""]
    if not free:
        cmd += ["--setting-sources", "", "--strict-mcp-config"]
    if resume:
        cmd.append(f"--resume={resume}")
        if fork_at:
            cmd += [f"--resume-session-at={fork_at}", "--fork-session"]
            if drop_turn:
                cmd.append(f"--resume-drops-turn={drop_turn}")
    elif session_id:
        cmd.append(f"--session-id={session_id}")
    else:
        cmd.append("--no-session-persistence")
    turns = max_turns or (FREE_MAX_TURNS if free else RESPONDER_MAX_TURNS if responder else 1)
    if not free:
        cmd.append("--disable-slash-commands")   # свобода: навыки пользователя — тоже его
    cmd += ["--max-turns", str(turns)]
    cmd.append(f"--model={claude_model(model)}")
    if thinking:
        cmd += ["--thinking", thinking]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def child_env(proxy: str | None) -> dict:
    env = netproxy.child_env(proxy)
    env.pop("ANTHROPIC_API_KEY", None)  # подписка, а не ключ (как у llm.claude)
    drop_session_markers(env)
    return env


def _image_blocks(content) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "image")


def _without_images(content, note: str):
    """Тот же текст сообщения без блоков изображений, с пометкой в конце."""
    if isinstance(content, list):
        texts = [str(b.get("text") or "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(t for t in texts if t)
    else:
        text = str(content or "")
    return f"{text}\n({note})" if text else f"({note})"


class _Rejected(Exception):
    """Ход с изображением отвергнут API (внутренний сигнал `_send`)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ControlError(Exception):
    """Control-запрос не выполнен: `subtype` — какой; `message` — ошибка CLI
    (или «нет процесса», «нет ответа за N с»); `unsupported` — такого
    подтипа CLI не знает (старая версия): нужен запасной путь."""

    def __init__(self, subtype: str, message: str) -> None:
        super().__init__(f"{subtype}: {message}")
        self.subtype = subtype
        self.message = message
        self.unsupported = _UNSUPPORTED in str(message).lower()


def _cut_utf8(text: str, limit: int = OUTPUT_LIMIT) -> tuple[str, bool]:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text, False
    return data[:limit].decode("utf-8", errors="ignore"), True


def _result_text(content) -> str:
    """Текст результата инструмента: строка или блоки (текст; картинка — пометкой)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif block.get("type") == "image":
            parts.append("[изображение]")
    return "\n".join(parts)


def mcp_name(name: str) -> tuple[str | None, str]:
    """`mcp__сервер__инструмент` → (сервер, инструмент); не MCP — (None, имя)."""
    if name.startswith("mcp__") and "__" in name[5:]:
        server, tool = name[5:].split("__", 1)
        return server, tool
    return None, name


class ToolTracker:
    """События хода работы для чата (спецификация 0.4, §3 «Ход работы — как в
    Claude CLI») из строк потока stream-json. `scan(сообщение)` → список
    событий (словари, ключ `type`):

    * `tool_use` — `id`, `name`, `server`/`tool` (MCP: `mcp__сервер__инструмент`
      разобран; иначе `server` None, `tool` = `name`), `input` (как есть),
      `parent` (`parent_tool_use_id` — вызов подагента, иначе None). Повтор
      того же `id` (модель шлёт сообщения по блокам) — без события;
    * `tool_result` — `id`, `ok` (не `is_error`), `output` (текст, не больше
      OUTPUT_LIMIT байт), `truncated`, `duration_ms` (от `tool_use`; None —
      начала не видели), `parent`, `cli_decision` (если CLI сообщил, кто
      разрешил: `tool_result_meta.permission_decision` — `{decision, source,
      reason_type}`, например `reason_type: "mode"` — автомод);
    * `hook` — `system/hook_started|hook_progress|hook_response` (только с
      `--include-hook-events` и только для хуков-команд: обратный вызов Meet
      в потоке не виден, его исход — событие `gate`): `hook_id`, `name`
      («PreToolUse:Bash»), `event`, `status` (started / progress / done),
      `outcome`, `exit_code`, `output`.

    Время — `clock` (тесты подставляют свои часы)."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._started: dict[str, float] = {}
        self._seen: set[str] = set()

    def scan(self, msg: dict) -> list[dict]:
        kind = msg.get("type")
        parent = msg.get("parent_tool_use_id")
        out: list[dict] = []
        if kind == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")):
                    continue
                tid = str(block["id"])
                if tid in self._seen:
                    continue
                self._seen.add(tid)
                self._started[tid] = self._clock()
                name = str(block.get("name") or "")
                server, tool = mcp_name(name)
                out.append({"type": "tool_use", "id": tid, "name": name, "server": server, "tool": tool,
                            "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                            "parent": parent})
        elif kind == "user":
            content = (msg.get("message") or {}).get("content")
            meta = {str(m.get("id")): m.get("permission_decision") for m in msg.get("tool_result_meta") or []
                    if isinstance(m, dict) and isinstance(m.get("permission_decision"), dict)}
            for block in content if isinstance(content, list) else ():
                if not (isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")):
                    continue
                tid = str(block["tool_use_id"])
                began = self._started.pop(tid, None)
                now = self._clock()
                output, cut = _cut_utf8(_result_text(block.get("content")))
                event = {"type": "tool_result", "id": tid, "ok": not block.get("is_error"), "output": output,
                         "truncated": cut, "duration_ms": None if began is None else round((now - began) * 1000),
                         "parent": parent}
                if tid in meta:
                    event["cli_decision"] = meta[tid]
                out.append(event)
        elif kind == "system" and str(msg.get("subtype") or "").startswith("hook_"):
            status = {"hook_started": "started", "hook_progress": "progress",
                      "hook_response": "done"}.get(str(msg.get("subtype")))
            if status:
                output, _cut = _cut_utf8(str(msg.get("output") or msg.get("stdout") or ""))
                out.append({"type": "hook", "hook_id": msg.get("hook_id"), "name": msg.get("hook_name"),
                            "event": msg.get("hook_event"), "status": status, "outcome": msg.get("outcome"),
                            "exit_code": msg.get("exit_code"), "output": output})
        return out


def _servers(items) -> list[dict]:
    """MCP-серверы из `system/init` или `mcp_status`: имя, состояние, ошибка."""
    out = []
    for x in items if isinstance(items, list) else ():
        if isinstance(x, dict) and x.get("name"):
            entry = {"name": str(x["name"]), "status": str(x.get("status") or "")}
            if x.get("error"):
                entry["error"] = str(x["error"])
            out.append(entry)
    return out


class Conversation:
    """Диалог с Claude Code в одном процессе. `stateful` — модель помнит
    прежние сообщения: вызывающий шлёт только новое.

    `cli` — команда запуска CLI (по умолчанию найденный claude.exe; тесты
    подставляют поддельный). `turns` — сколько ходов в этом процессе,
    `context_tokens` — размер контекста по последнему ответу (вход с кэшем +
    выход): по ним вызывающий решает, когда начать диалог заново.

    `responder` — агент-собеседник: инструменты чтения в `add_dirs` (папка
    встречи, база знаний, библиотека), кроме `deny_paths` (`kb_exclude`).
    `gate` (`meet.llm.consent.ConsentGate`, с `responder`) — свобода по
    согласию: все инструменты и MCP пользователя, каждый вызов решают ворота
    (см. модуль). `mcp_servers` — MCP-серверы из `system/init` (`name`,
    `status`) после первого хода.
    `persist` — сеанс сохраняется и продолжается нативно (`session_id`);
    `resume` — id сохранённого сеанса, с которого начать (из
    `assistant/sessions.json`), подразумевает `persist`. `model` — модель
    для `--model` (пустая — DEFAULT_CLAUDE_MODEL); какая запустилась на
    самом деле — `self.model` и `AgentReply.model` (из `system/init`),
    `on_model(имя)` — когда она стала известна. `has_context` —
    следующий `send` продолжит прежний разговор (живой процесс с ходами или
    сохранённый сеанс): затравка не нужна.

    0.4 (со свободой): `mode` — `auto` | `confirm` (см. модуль);
    `permission_mode` — режим, в котором CLI на деле работает (None — ещё не
    сообщил), `auto_unavailable` — просили автомод, а CLI в другом режиме;
    `commands` (`[{name, description, hint}]`) и `models` — из ответа
    `initialize`; `slash_commands` — имена из `system/init`. Управление для
    слэш-команд: `control()`, `mcp_status()`, `mcp_reconnect()`,
    `mcp_toggle()`, `set_model()`, `set_permission_mode()`, `compact()`,
    `restart()`."""

    stateful = True

    def __init__(self, *, system_prompt: str, model: str | None = None,
                 thinking: str | None = None, effort: str | None = None,
                 proxy: str | None = None, cwd: str | Path | None = None,
                 cli: list[str] | None = None, popen=subprocess.Popen, log=None,
                 responder: bool = False, add_dirs=(), deny_paths=(), max_turns: int | None = None,
                 persist: bool = False, resume: str | None = None, on_model=None,
                 gate=None, mode: str = MODE_AUTO) -> None:
        self._system = system_prompt
        self._mode = mode if mode in _PERMISSION_MODE else MODE_CONFIRM
        self.permission_mode: str | None = None
        self.commands: list[dict] = []
        self.models: list[dict] = []
        self.slash_commands: list[str] | None = None
        self.skills: list[str] | None = None        # навыки из `system/init` (`skills`)
        # Обратный вызов событий хода работы (`send(on_event=…)`), пока идёт ход.
        self._on_event = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._model = claude_model(model)
        # Модель, которую CLI на самом деле запустил (`model` из `system/init`);
        # None — процесс ещё не начинал хода. `on_model(имя)` — узнали или
        # сменилась (новый процесс).
        self.model: str | None = None
        self._on_model = on_model
        self._thinking = thinking
        self._effort = effort
        self._proxy = proxy
        self._cwd = cwd
        self._cli = cli
        self._popen = popen
        self._log = log or (lambda _line: None)
        self._responder = responder
        self._add_dirs = tuple(add_dirs or ())
        self._deny_paths = tuple(deny_paths or ())
        self._max_turns = max_turns
        self._gate = gate if responder else None
        self._needs_init = False
        self.mcp_servers: list[dict] | None = None
        self.unseen_tools: list[str] = []
        self._persist = bool(persist or resume)
        # Id сеанса (persist) и есть ли он уже на диске: тогда процесс
        # поднимается с --resume, иначе — новый сеанс с новым id.
        self.session_id: str | None = resume if self._persist else None
        self._saved = bool(resume)
        self._resuming = False
        # Последняя запись (uuid ответа модели) последнего удачного хода этого
        # сеанса — точка усечения, если следующий ход отвергнут из-за картинки.
        self._last_kept: str | None = None
        # Новый пустой сеанс (или процесс без сохранения), ходов в нём ещё не было.
        self._blank = False
        self._fork: tuple[str, str] | None = None
        self._proc = None
        self._queue: asyncio.Queue | None = None
        self._stderr: list[str] = []
        self._lock = asyncio.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._requests = 0
        self._busy = False
        # idle → starting (процесс, ещё не записано) → writing → running.
        self._phase = "idle"
        self._cancel = False
        self._stop_task: asyncio.Task | None = None
        self.turns = 0
        self.context_tokens = 0
        self.spawns = 0
        self.last_first_text_s: float | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def auto_unavailable(self) -> bool:
        """Просили автомод, а CLI работает в другом режиме (недоступен для
        модели, выключен `disableAutoMode`): действия — карточками через
        `can_use_tool`, шапке — «Автомод недоступен». Пока CLI не сообщил
        режим — False."""
        return (self._gate is not None and self._mode == MODE_AUTO
                and self.permission_mode is not None and self.permission_mode != "auto")

    @property
    def has_context(self) -> bool:
        """Следующий `send` продолжит разговор (затравка не нужна)."""
        if self.alive and self.turns > 0:
            return True
        return self._persist and self._saved and self.session_id is not None

    def forget_session(self) -> None:
        """Забыть сохранённый сеанс: следующий процесс начнёт новый."""
        self._saved = False
        self.session_id = None
        self._last_kept = None
        self._fork = None

    # --- процесс ---

    def _spawn(self) -> str | None:
        """Поднять процесс; None — ок, иначе текст ошибки."""
        cli = self._cli if self._cli is not None else default_cli()
        if not cli:
            return detect.claude_not_found()
        session_id = resume = None
        fork_at = drop_turn = None
        self._resuming = False
        self._blank = not self._persist
        if self._persist:
            if self._saved and self.session_id:
                resume, self._resuming = self.session_id, True
                if self._fork:
                    fork_at, drop_turn = self._fork
            else:
                self.session_id = session_id = str(uuid.uuid4())
                self._last_kept = None
                self._blank = True
        self._fork = None
        cmd = build_command(cli, system_prompt=self._system, model=self._model,
                            thinking=self._thinking, effort=self._effort,
                            responder=self._responder, add_dirs=self._add_dirs,
                            deny_paths=self._deny_paths, max_turns=self._max_turns,
                            session_id=session_id, resume=resume, fork_at=fork_at, drop_turn=drop_turn,
                            freedom=self._gate is not None,
                            settings_file=self._free_settings(), mode=self._mode)
        cwd = str(self._cwd) if self._cwd else str(workdir())
        try:
            proc = self._popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=child_env(self._proxy), cwd=cwd,
                               **procjob.popen_kwargs())
        except OSError as e:
            return f"не удалось запустить Claude Code: {e}"
        procjob.bind_to_this_process(proc.pid)
        self._needs_init = self._gate is not None
        loop = asyncio.get_running_loop()
        self._loop = loop
        queue: asyncio.Queue = asyncio.Queue()
        self._proc, self._queue = proc, queue
        self._stderr = []
        self.turns = 0
        self.context_tokens = 0
        self.spawns += 1
        self.permission_mode = None      # новый процесс сообщит свой
        tracker = ToolTracker()

        def call(fn, *args) -> None:
            try:
                loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass  # цикл закрыт — читать некому

        def read_out() -> None:
            try:
                for raw in proc.stdout:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    kind = msg.get("type")
                    if kind == "control_response":
                        call(self._on_control_response, msg)
                    elif kind == "control_request":
                        self._answer_control(proc, msg)
                    else:
                        # Состояние и события — раньше очереди: строка
                        # инструмента приходит в чат раньше решения ворот по нему.
                        call(self._observe, msg, tracker.scan(msg))
                        call(queue.put_nowait, msg)
            except (OSError, ValueError):
                pass
            finally:
                call(queue.put_nowait, None)

        def read_err() -> None:
            try:
                for raw in proc.stderr:
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    self._stderr.append(text)
                    if sum(len(t) for t in self._stderr) > STDERR_TAIL * 2:
                        self._stderr = ["".join(self._stderr)[-STDERR_TAIL:]]
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_out, name="claude-stream-out", daemon=True).start()
        threading.Thread(target=read_err, name="claude-stream-err", daemon=True).start()
        how = (f", ветка сеанса {resume} без хода {drop_turn}" if fork_at
               else f", продолжение сеанса {resume}" if resume
               else f", сеанс {session_id}" if session_id else "")
        self._log(f"диалог: процесс Claude Code запущен (pid {proc.pid}{how})")
        return None

    def _observe(self, msg: dict, events: list) -> None:
        """Строка потока (цикл событий): состояние CLI — режим, команды,
        MCP — и события хода работы вызывающему."""
        if msg.get("type") == "system":
            sub = msg.get("subtype")
            if sub in ("init", "status") and isinstance(msg.get("permissionMode"), str):
                self._note_mode(msg["permissionMode"])
            if sub == "init":
                if isinstance(msg.get("mcp_servers"), list):
                    self.mcp_servers = _servers(msg["mcp_servers"])
                if isinstance(msg.get("slash_commands"), list):
                    self.slash_commands = [str(x) for x in msg["slash_commands"] if x]
                if isinstance(msg.get("skills"), list):
                    # Навыки пользователя (личные, проекта, плагинов): имена — `/имя` в чате.
                    self.skills = [str(x.get("name") if isinstance(x, dict) else x) for x in msg["skills"]
                                   if (x.get("name") if isinstance(x, dict) else x)]
        for event in events:
            self._emit(event)

    def _note_mode(self, mode: str) -> None:
        if mode and mode != self.permission_mode:
            self.permission_mode = mode
            if self.auto_unavailable:
                self._log(f"диалог: автомод недоступен — Claude Code в режиме {mode}")

    def _emit(self, event: dict) -> None:
        """Событие хода работы обратному вызову текущего хода (цикл событий)."""
        if self._on_event is not None:
            _safe_call(self._on_event, event, self._log)

    def _emit_threadsafe(self, event: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._emit, event)
        except RuntimeError:
            pass   # цикл закрыт

    def _stderr_tail(self) -> str:
        return "".join(self._stderr)[-STDERR_TAIL:].strip()

    def _write_line(self, proc, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:  # ход, остановка и отказ читателя пишут из разных потоков
            _write(proc, data)

    def _on_control_response(self, msg: dict) -> None:
        response = msg.get("response") if isinstance(msg.get("response"), dict) else {}
        fut = self._pending.pop(str(response.get("request_id")), None)
        if fut is not None and not fut.done():
            fut.set_result(response)

    def _free_settings(self) -> str | None:
        """Файл настроек свободного режима (в рабочей папке процесса)."""
        if self._gate is None:
            return None
        folder = Path(self._cwd) if self._cwd else workdir()
        path = folder / FREE_SETTINGS_NAME
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(FREE_SETTINGS), encoding="utf-8")
        except OSError as e:
            self._log(f"диалог: настройки свободного режима не записаны ({e})")
            return None
        return str(path)

    def _answer_control(self, proc, msg: dict) -> None:
        """Запрос от CLI (поток чтения). С воротами согласия — разрешение
        инструмента (`can_use_tool`) и хук PreToolUse решает `gate.check`;
        без них (и на прочее) — отказ, чтобы ход не ждал ответа вечно."""
        request = msg.get("request") if isinstance(msg.get("request"), dict) else {}
        if self._gate is not None and request.get("subtype") in ("can_use_tool", "hook_callback"):
            # Свой поток: ответ может ждать карточку подтверждения, а чтение
            # вывода CLI (и другие запросы того же хода) ждать не должны.
            threading.Thread(target=self._answer_gate, args=(proc, msg), name="claude-gate",
                             daemon=True).start()
            return
        self._refuse_control(proc, msg)

    def _answer_gate(self, proc, msg: dict) -> None:
        request = msg.get("request") if isinstance(msg.get("request"), dict) else {}
        rid = msg.get("request_id")
        gate = self._gate
        if gate is not None:
            event = None
            try:
                body, event = _gate_answer(gate, request)
            except Exception as e:  # ворота не роняют поток чтения: отказ
                self._log(f"диалог: сбой ворот согласия ({type(e).__name__}: {e})")
                body = ({"behavior": "deny", "message": f"проверка согласия не удалась: {type(e).__name__}"}
                        if request.get("subtype") == "can_use_tool" else
                        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                "permissionDecisionReason": "проверка согласия не удалась"}})
            if event is not None:
                self._emit_threadsafe(event)
            try:
                self._write_line(proc, {"type": "control_response", "response": {
                    "subtype": "success", "request_id": rid, "response": body}})
            except (OSError, ValueError):
                pass

    def _refuse_control(self, proc, msg: dict) -> None:
        """Запрос от CLI (разрешение инструмента и т. п.) без ворот согласия:
        отказываем, чтобы ход не ждал ответа вечно. При dontAsk таких запросов
        быть не должно."""
        request = msg.get("request") if isinstance(msg.get("request"), dict) else {}
        rid = msg.get("request_id")
        if request.get("subtype") == "can_use_tool":
            response = {"subtype": "success", "request_id": rid,
                        "response": {"behavior": "deny", "message": "инструмент не разрешён"}}
        else:
            response = {"subtype": "error", "request_id": rid,
                        "error": f"не поддерживается: {request.get('subtype')}"}
        try:
            self._write_line(proc, {"type": "control_response", "response": response})
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        """Закрыть диалог: конец stdin (процесс выходит сам), не вышел за
        CLOSE_WAIT_S — убить. Повторный вызов безопасен."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=CLOSE_WAIT_S)
        except subprocess.TimeoutExpired:
            procjob.kill_tree(proc)
            try:
                proc.wait(timeout=CLOSE_WAIT_S)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass

    def kill(self) -> None:
        """Убить сразу (таймаут хода: что процесс напишет дальше — неизвестно)."""
        proc, self._proc = self._proc, None
        if proc is not None:
            procjob.kill_tree(proc)

    def _kill_if(self, proc) -> None:
        """Убить `proc`; текущим он перестаёт быть, только если ещё им был
        (его мог сменить перезапуск после остановки). Уже мёртвый не
        трогается: taskkill по старому pid мог бы задеть чужой процесс."""
        if self._proc is proc:
            self._proc = None
        if proc.poll() is None:
            procjob.kill_tree(proc)

    # --- ход ---

    async def send(self, text: str | None = None, *, content: list | None = None,
                   images=(), on_text=None, on_event=None, timeout_s: float = 90.0) -> AgentReply:
        """Одно сообщение пользователя → ответ. `on_text(кусок)` — текст по
        мере генерации; `on_text(None)` — модель начала новое сообщение после
        инструмента (собеседник): показанный текст был пояснением. Ошибки — в
        AgentReply.error (процесс тогда закрыт).

        `on_event(событие)` (0.4, ход работы в чате) — в цикле событий, по
        мере хода: `tool_use`, `tool_result`, `hook` (`ToolTracker`) и `gate`
        (`_gate_event`); общий ключ — `id` вызова. `tool_use` всегда раньше
        `gate` и `tool_result` того же вызова. В журнал процесса события не
        пишутся (там нельзя текст команд и вывода).

        Сообщение — `text` и изображения `images` (пути) или готовые блоки
        `content` (список словарей Anthropic API). Негодное изображение не
        уходит: `dropped_images`, `notes`, пометка модели в тексте."""
        dropped: list[tuple[str, str]] = []
        if content is None:
            content, dropped = user_content(text or "", images)
        async with self._lock:
            self._busy, self._cancel, self._phase = True, False, "starting"
            self._on_event = on_event
            try:
                reply = await self._send(content, on_text, timeout_s, list(images or ()))
            finally:
                self._busy, self._phase = False, "idle"
                self._on_event = None
        if dropped:
            extra = dropped_fields(dropped)
            reply.dropped_images = extra["dropped_images"] + reply.dropped_images
            reply.notes = extra["notes"] + reply.notes
        return reply

    async def interrupt(self, *, wait_s: float = INTERRUPT_WAIT_S) -> bool:
        """Остановить идущий ход («Стоп»). Ход завершится ответом
        `cancelled` (error = CANCELLED_ERROR) с тем текстом, что успел прийти,
        — если он и правда прерван; успевший закончиться ход вернётся как
        обычный ответ.

        True — CLI подтвердил (`control_response`), процесс живёт дальше;
        хода нет — тоже True; ход ещё не отправлен CLI (поднимается процесс,
        пишется сообщение) — True: `send` сам не начнёт ход или сразу пошлёт
        остановку. False — подтверждения нет за `wait_s` (или CLI ответил
        ошибкой): процесс убит и поднят заново (`persist` — с `--resume`,
        иначе вызывающему нужна затравка: `has_context` — False).

        Следующий `send` — после `await interrupt()`: перезапуск идёт под тем
        же замком, что и ход."""
        if not self._busy:
            return True
        self._cancel = True
        if self._phase != "running":
            return True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return True
        return await self._stop_turn(proc, wait_s)

    async def _stop_turn(self, proc, wait_s: float) -> bool:
        self._requests += 1
        rid = f"req_{self._requests}_{os.urandom(4).hex()}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        confirmed = False
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "control_request", "request_id": rid, "request": {"subtype": "interrupt"}})
            response = await asyncio.wait_for(fut, wait_s)
            confirmed = response.get("subtype") != "error"
        except (OSError, ValueError, asyncio.TimeoutError):
            confirmed = False
        finally:
            self._pending.pop(rid, None)
        if confirmed:
            return True
        self._log(f"диалог: Claude Code не подтвердил остановку за {wait_s:g} с — перезапуск")
        await asyncio.to_thread(self._kill_if, proc)
        async with self._lock:  # ход дочитал конец потока и вернул ответ
            if not self.alive:
                error = self._spawn()
                if error:
                    self._log(f"диалог: перезапуск не удался: {error}")
        return False

    def _note_unseen(self, tool_ids) -> None:
        """Вызовы хода, которые не прошли через хук ворот, — в журнал процесса."""
        if self._gate is None or not tool_ids:
            return
        missing = self._gate.unseen(tool_ids)
        self.unseen_tools = list(missing)
        if missing:
            self._log(f"диалог: {len(missing)} вызов(ов) инструментов не прошли проверку согласия "
                      f"(хук PreToolUse не пришёл): {', '.join(missing[:5])}")

    def _resume_failed(self, detail: str) -> AgentReply:
        sid = self.session_id
        self.forget_session()
        self._log(f"диалог: сеанс {sid} не продолжить: {detail}")
        return resume_failure(detail)

    def _cancelled(self, parts) -> AgentReply:
        return AgentReply(text="".join(parts).strip(), error=CANCELLED_ERROR, cancelled=True)

    async def _ensure_process(self) -> str | None:
        """Живой процесс без чужих событий в очереди; None — ок, иначе ошибка
        (или AgentReply «сеанс не продолжить» — строкой не вернуть)."""
        if self.alive and self._queue is not None:
            # Хвост прошлого хода (событие после result) не должен попасть в этот.
            while not self._queue.empty():
                if self._queue.get_nowait() is None:  # процесс умер между ходами
                    await asyncio.to_thread(self._kill_if, self._proc)
                    break
        if not self.alive:
            if self._proc is not None:
                await asyncio.to_thread(self._kill_if, self._proc)
            error = self._spawn()
            if error:
                return error
        if self._needs_init:
            return await self._handshake()
        return None

    async def _handshake(self):
        """`initialize` с хуком PreToolUse ворот согласия (формат —
        `claude_agent_sdk` `Query.initialize`). None — ок; иначе текст ошибки
        или ответ «сеанс не продолжить» (процесс с `--resume` умер сразу)."""
        proc = self._proc
        self._requests += 1
        rid = f"req_{self._requests}_{os.urandom(4).hex()}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        response = None
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "control_request", "request_id": rid, "request": {
                    "subtype": "initialize",
                    "hooks": {"PreToolUse": [{"matcher": None, "hookCallbackIds": [GATE_HOOK_ID]}]}}})
            deadline = time.monotonic() + INIT_WAIT_S
            while response is None:
                try:
                    response = await asyncio.wait_for(asyncio.shield(fut), 0.25)
                except asyncio.TimeoutError:
                    if proc.poll() is not None or time.monotonic() > deadline:
                        break
        except (OSError, ValueError):
            response = None
        finally:
            self._pending.pop(rid, None)
        if response is not None and response.get("subtype") != "error":
            self._needs_init = False
            self._note_initialize(response.get("response"))
            return None
        await asyncio.sleep(0.2)   # хвост stderr («сеанс не найден» и т. п.)
        dead = proc.poll() is not None
        detail = self._stderr_tail() or (str(response.get("error")) if response else "") or (
            "процесс завершился" if dead else f"нет ответа за {INIT_WAIT_S:g} с")
        await asyncio.to_thread(self._kill_if, proc)
        if self._resuming:
            return self._resume_failed(detail)
        return netproxy.with_hint(
            f"Claude Code не включил проверку согласия ({detail[:300]}) — обновите Claude Code "
            "или выключите «Расширенные возможности ассистента» в настройках")

    def _note_initialize(self, body) -> None:
        """Ответ `initialize` (2.1.293): команды CLI (`commands`: `name`,
        `description`, `argumentHint`), модели (`models`), режим
        (`current_permission_mode`)."""
        if not isinstance(body, dict):
            return
        commands = body.get("commands")
        if isinstance(commands, list):
            self.commands = [{"name": str(c["name"]), "description": str(c.get("description") or ""),
                              "hint": str(c.get("argumentHint") or "")}
                             for c in commands if isinstance(c, dict) and c.get("name")]
        if isinstance(body.get("models"), list):
            self.models = [m for m in body["models"] if isinstance(m, dict) and m.get("value")]
        if isinstance(body.get("current_permission_mode"), str):
            self._note_mode(body["current_permission_mode"])

    # --- управление (слэш-команды) ---

    async def _live_process(self, subtype: str):
        """Живой процесс для control-запроса: идёт ход — тот же процесс (ход
        не ждём); процесса нет — поднять (с `initialize`, без хода модели)."""
        if self.alive:
            return self._proc
        async with self._lock:
            if not self.alive or self._needs_init:
                error = await self._ensure_process()
                if isinstance(error, AgentReply):     # сеанс не продолжить — новый
                    error = await self._ensure_process()
                if error:
                    raise ControlError(subtype, str(getattr(error, "error", error)))
            return self._proc

    async def control(self, subtype: str, payload: dict | None = None, *,
                      wait_s: float = CONTROL_WAIT_S) -> dict:
        """Control-запрос CLI (`{"subtype": …, **payload}`) → тело ответа
        (`response`, может быть пустым). Во время хода уходит сразу; без
        процесса — процесс поднимается (модель не зовётся). Ошибка CLI, нет
        процесса, нет ответа за `wait_s` — `ControlError` (`unsupported` —
        подтипа нет в этой версии CLI)."""
        proc = await self._live_process(subtype)
        self._requests += 1
        rid = f"req_{self._requests}_{os.urandom(4).hex()}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        response = None
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "control_request", "request_id": rid, "request": {"subtype": subtype, **(payload or {})}})
            deadline = time.monotonic() + wait_s
            while response is None:
                try:
                    response = await asyncio.wait_for(asyncio.shield(fut), 0.25)
                except asyncio.TimeoutError:
                    if proc.poll() is not None:
                        raise ControlError(subtype, "процесс Claude Code завершился") from None
                    if time.monotonic() > deadline:
                        raise ControlError(subtype, f"нет ответа за {wait_s:g} с") from None
        except (OSError, ValueError) as e:
            raise ControlError(subtype, f"процесс Claude Code не принял запрос: {e}") from None
        finally:
            self._pending.pop(rid, None)
        if response.get("subtype") == "error":
            raise ControlError(subtype, str(response.get("error") or "ошибка Claude Code"))
        body = response.get("response")
        return body if isinstance(body, dict) else {}

    async def mcp_status(self, *, settle_s: float = 0.0) -> list[dict]:
        """MCP-серверы: `[{name, status, error?}]`, статусы CLI — `connected`,
        `failed`, `needs-auth`, `pending`, `disabled`. `settle_s` — подождать,
        пока `pending` не сменится (серверы поднимаются в фоне после старта).
        Запроса нет в этой версии CLI — последний список из `system/init`."""
        deadline = time.monotonic() + settle_s
        while True:
            try:
                body = await self.control("mcp_status")
            except ControlError as e:
                if e.unsupported and self.mcp_servers is not None:
                    return [dict(s) for s in self.mcp_servers]
                raise
            servers = _servers(body.get("mcpServers"))
            if not any(s["status"] == "pending" for s in servers) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        self.mcp_servers = servers
        return [dict(s) for s in servers]

    async def mcp_reconnect(self, name: str | None = None) -> dict:
        """Переподключить MCP-сервер (`name`) или, без имени, все `failed`.
        Подтипа нет или переподключение не удалось — перезапуск с `--resume`
        (`restart()`): MCP поднимаются заново. Ответ: `results` — {имя:
        None | ошибка}, `servers` — состояние после, `restarted`, `context`
        (после перезапуска разговор продолжится; False — нужна затравка).
        Неизвестное имя — ошибка в `results` без перезапуска."""
        servers = await self.mcp_status()
        known = {s["name"] for s in servers}
        names = [name] if name else [s["name"] for s in servers if s["status"] == "failed"]
        results: dict[str, str | None] = {}
        need_restart = False
        for n in names:
            if n not in known:
                results[n] = f"Нет MCP-сервера {n}"
                continue
            try:
                await self.control("mcp_reconnect", {"serverName": n}, wait_s=RECONNECT_WAIT_S)
                results[n] = None
            except ControlError as e:
                results[n] = e.message
                need_restart = True
        out = {"results": results, "servers": servers, "restarted": False, "context": True}
        if need_restart:
            self._log("диалог: MCP не переподключился запросом — перезапуск Claude Code")
            out["context"] = await self.restart()
            out["restarted"] = True
            servers = await self.mcp_status(settle_s=MCP_SETTLE_S)
            state = {s["name"]: s for s in servers}
            for n in results:
                if n in state:
                    s = state[n]
                    results[n] = None if s["status"] == "connected" else (s.get("error") or s["status"])
        elif names:
            servers = await self.mcp_status()
        out["servers"] = servers
        return out

    async def mcp_toggle(self, name: str, enabled: bool) -> list[dict]:
        """Включить или выключить MCP-сервер на этот сеанс; состояние после."""
        await self.control("mcp_toggle", {"serverName": name, "enabled": bool(enabled)},
                           wait_s=RECONNECT_WAIT_S)
        return await self.mcp_status()

    async def set_model(self, model: str | None) -> str:
        """Сменить модель (`set_model`) на оставшиеся ходы; перезапуск
        (`--resume`) пойдёт с ней же. Возвращает полное имя модели, если CLI
        его назвал в `models`, иначе заданное. Неизвестную модель CLI
        проверяет запросом к API (2.1.293) и отказывает — `ControlError`.
        Автомод с новой моделью может стать недоступен — `auto_unavailable`
        после `system/status`."""
        name = str(model or "").strip()
        await self.control("set_model", {"model": name or None})
        self._model = claude_model(name)
        resolved = next((str(m.get("resolvedModel") or m.get("value")) for m in self.models
                         if m.get("value") == self._model), self._model)
        self._note_model(resolved)
        return resolved

    async def set_permission_mode(self, mode: str) -> str:
        """Сменить режим CLI по ходу (`auto` / `confirm`, без перезапуска);
        возвращает режим, который CLI подтвердил."""
        self._mode = mode if mode in _PERMISSION_MODE else MODE_CONFIRM
        body = await self.control("set_permission_mode", {"mode": _PERMISSION_MODE[self._mode]})
        self._note_mode(str(body.get("mode") or _PERMISSION_MODE[self._mode]))
        return self.permission_mode or ""

    async def compact(self, instructions: str | None = None, **kwargs) -> AgentReply:
        """`/compact [указание]` отдельным ходом (control-запроса для сжатия
        нет). Удалось — `reply.compacted` (`pre_tokens`, `post_tokens`);
        нет — текст CLI в `reply.text`."""
        text = "/compact" + (f" {instructions.strip()}" if instructions and instructions.strip() else "")
        return await self.send(text, **kwargs)

    async def restart(self) -> bool:
        """Перезапустить процесс CLI (MCP-серверы и настройки — заново) после
        текущего хода. Сохраняемый сеанс продолжается (`--resume`): True —
        разговор на месте; False — процесс новый без прежнего контекста
        (сеанс не сохранялся или не нашёлся): вызывающему нужна затравка.
        Процесс не поднялся — `ControlError`."""
        async with self._lock:
            if self._proc is not None:
                await asyncio.to_thread(self.close)
            error = await self._ensure_process()
            if isinstance(error, AgentReply):         # сеанс не продолжить — новый
                error = await self._ensure_process()
                if error:
                    raise ControlError("restart", str(getattr(error, "error", error)))
                return False
            if error:
                raise ControlError("restart", str(error))
            return self.has_context

    async def _send(self, content, on_text, timeout_s: float, image_paths: list) -> AgentReply:
        deadline = time.monotonic() + timeout_s
        if not self.alive and self._persist and self._saved and not is_uuid(self.session_id):
            return self._resume_failed(f"неверный id сеанса: {self.session_id!r}")
        error = await self._ensure_process()
        if isinstance(error, AgentReply):
            return error
        if error:
            return AgentReply(text="", error=error)
        if self._cancel:  # «Стоп» до отправки: хода не было
            return self._cancelled([])
        blank = self._blank and self.turns == 0
        try:
            return await self._turn(content, on_text, deadline)
        except _Rejected as rejected:
            note = f"изображение не принято моделью: {rejected.detail[:200]}"
            dropped = {"dropped_images": [str(p) for p in image_paths],
                       "notes": [f"Изображение не отправлено: модель его не приняла ({rejected.detail[:200]})"]}
            self._log(f"диалог: {note}")
        # Ход с картинкой выброшен из сеанса — повторяем без неё.
        if self._persist and self.session_id and self._last_kept:
            self._fork = (self._last_kept, self._turn_uuid)
        elif blank:
            if self._persist:
                self.forget_session()  # пустой сеанс с плохим первым ходом бросаем
        else:
            # Точки усечения нет (сеанс продолжен из хранилища, ходов в этом
            # процессе не было) или процесс без сохранения: вызывающему — затравка.
            if self._persist:
                self.forget_session()
            reply = resume_failure(note)
            reply.dropped_images, reply.notes = dropped["dropped_images"], dropped["notes"]
            return reply
        error = await self._ensure_process()
        if isinstance(error, AgentReply):
            error.dropped_images, error.notes = dropped["dropped_images"], dropped["notes"]
            return error
        if error:
            return AgentReply(text="", error=error, **dropped)
        if self._cancel:
            return self._cancelled([])
        try:
            reply = await self._turn(_without_images(content, note), on_text, deadline)
        except _Rejected as again:  # без картинок отвергнуть не должны — но не зацикливаемся
            reply = AgentReply(text="", error=again.detail)
        reply.dropped_images, reply.notes = dropped["dropped_images"], dropped["notes"]
        return reply

    async def _turn(self, content, on_text, deadline: float) -> AgentReply:
        proc, queue = self._proc, self._queue
        resuming = self._resuming
        with_images = _image_blocks(content) > 0
        self._turn_uuid = turn_uuid = str(uuid.uuid4())
        started = time.monotonic()
        self._phase = "writing"
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "user", "uuid": turn_uuid, "message": {"role": "user", "content": content}})
        except (OSError, ValueError) as e:
            await asyncio.sleep(0.2)  # дать CLI дописать stderr («сеанс не найден» и т. п.)
            error = self._stderr_tail() or f"{type(e).__name__}: {e}"
            await asyncio.to_thread(self._kill_if, proc)
            if resuming:
                return self._resume_failed(error)
            return AgentReply(text="", error=netproxy.with_hint(f"процесс Claude Code не принял запрос: {error}"))
        self._phase = "running"
        if self._cancel:
            # «Стоп» пришёл, пока сообщение писалось: останавливаем сами.
            self._stop_task = asyncio.create_task(self._stop_turn(proc, INTERRUPT_WAIT_S))
        parts: list[str] = []
        tool_ids: list[str] = []      # tool_use хода — сверить, все ли прошли через хук
        streamed = False
        began = False  # был system/init: CLI начал ход, сеанс найден
        last_entry: str | None = None
        compacted: dict | None = None
        local: list[str] = []         # вывод локальной команды CLI
        self.last_first_text_s = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await asyncio.to_thread(self._kill_if, proc)
                return AgentReply(text="".join(parts).strip(), error=TIMEOUT_ERROR)
            try:
                msg = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                await asyncio.sleep(0.05)  # хвост stderr
                detail = self._stderr_tail() or "процесс завершился"
                await asyncio.to_thread(self._kill_if, proc)
                if self._cancel:
                    return self._cancelled(parts)
                if resuming and (not began or _RESUME_MISSING in detail.lower()):
                    return self._resume_failed(detail)
                return AgentReply(text="".join(parts).strip(),
                                  error=netproxy.with_hint(f"Claude Code: {detail}"))
            kind = msg.get("type")
            if kind == "system" and msg.get("subtype") == "init":
                began = True
                self._note_model(msg.get("model"))
                self._resuming = False  # сеанс найден: дальше сбои этого процесса — обычные
                if self._persist:
                    self.session_id = str(msg.get("session_id") or self.session_id)
                    self._saved = True
                # Режим, MCP-серверы и команды из init — `_observe`.
            elif kind == "system" and msg.get("subtype") == "compact_boundary":
                meta = msg.get("compact_metadata") if isinstance(msg.get("compact_metadata"), dict) else {}
                compacted = {"trigger": meta.get("trigger"), "pre_tokens": meta.get("pre_tokens"),
                             "post_tokens": meta.get("post_tokens")}
                self._log(f"диалог: контекст сжат ({compacted['trigger']}, было {compacted['pre_tokens']} токенов)")
            elif kind == "user" and isinstance((msg.get("message") or {}).get("content"), str):
                # Вывод локальной команды CLI (`/compact`, отказ `!`…`` и т. п.).
                found = _LOCAL_OUTPUT.search(msg["message"]["content"])
                if found and found.group(1).strip():
                    local.append(found.group(1).strip())
            elif kind == "stream_event":
                event = msg.get("event")
                if isinstance(event, dict) and event.get("type") == "message_start" and streamed:
                    # Новое сообщение модели после инструмента (собеседник):
                    # прежний текст был пояснением, ответ — дальше.
                    parts.clear()
                    streamed = False
                    if on_text is not None:
                        _safe_call(on_text, None, self._log)
                    continue
                delta = text_delta(event)
                if delta:
                    if self.last_first_text_s is None:
                        self.last_first_text_s = time.monotonic() - started
                    streamed = True
                    parts.append(delta)
                    if on_text is not None:
                        _safe_call(on_text, delta, self._log)
            elif kind == "user" and self._gate is not None:
                # Результат инструмента: выполнился ли вызов, который хук не видел?
                # (Отказ правилом CLI — результат с ошибкой и без хука — не дыра.)
                content = (msg.get("message") or {}).get("content")
                done = [str(b.get("tool_use_id")) for b in content if isinstance(b, dict)
                        and b.get("type") == "tool_result" and b.get("tool_use_id") and not b.get("is_error")] \
                    if isinstance(content, list) else []
                gap = self._gate.unseen(done)
                if gap:
                    self.unseen_tools = list(gap)
                    self._log(f"диалог: {GATE_GAP_ERROR} ({', '.join(gap[:5])}) — процесс остановлен")
                    await asyncio.to_thread(self._kill_if, proc)
                    return AgentReply(text="".join(parts).strip(),
                                      error=f"{GATE_GAP_ERROR} ({len(gap)}) — обновите Meet или Claude Code")
            elif kind == "assistant":
                if msg.get("uuid"):
                    last_entry = str(msg["uuid"])
                for block in (msg.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        tool_ids.append(str(block["id"]))
                if not streamed:
                    for block in (msg.get("message") or {}).get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(str(block.get("text") or ""))
            elif kind == "result":
                self._note_unseen(tool_ids)
                self.turns += 1
                usage = msg.get("usage") or {}
                self.context_tokens = sum(int(usage.get(k) or 0) for k in (
                    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                    "output_tokens"))
                if compacted and not self.context_tokens and isinstance(compacted.get("post_tokens"), int):
                    self.context_tokens = compacted["post_tokens"]   # ход сжатия без вызова модели
                result = msg.get("result")
                body = "".join(parts).strip() or (str(result).strip() if result else "")
                if not body and local and not compacted:
                    body = "\n".join(local)
                if not msg.get("is_error"):
                    # Успевший закончиться ход — обычный ответ, даже если «Стоп»
                    # пришёл следом: этот текст уже в сеансе модели.
                    self._resuming = False
                    self._blank = False
                    if last_entry:
                        self._last_kept = last_entry
                    return AgentReply(text=body, model=self.model, compacted=compacted)
                if self._cancel:
                    return self._cancelled(parts)
                errors = [str(e) for e in msg.get("errors") or [] if e]
                detail = "; ".join(errors) or str(result or msg.get("subtype") or "ошибка Claude Code")
                if resuming and (not began or _RESUME_MISSING in detail.lower()):
                    await asyncio.to_thread(self._kill_if, proc)
                    return self._resume_failed(detail)
                if with_images and _IMAGE_REJECTED.search(detail):
                    await asyncio.to_thread(self._kill_if, proc)
                    raise _Rejected(detail)
                return AgentReply(text=body, error=netproxy.with_hint(detail), model=self.model)

    def _note_model(self, value) -> None:
        """Модель из `system/init`: запомнить, в журнал — если не та, что задана."""
        actual = str(value or "").strip() or None
        if actual is None or actual == self.model:
            return
        self.model = actual
        if not model_matches(self._model, actual):
            self._log(f"диалог: Claude Code запустил модель {actual}, а задана {self._model}")
        if self._on_model is not None:
            _safe_call(self._on_model, actual, self._log)


def _gate_event(decision, tool: str, tool_use_id, via: str) -> dict:
    """Событие `gate` для чата: `id` (tool_use_id), `name`, `via` (hook /
    can_use_tool), `decision`: `auto` (решает CLI: правила пользователя,
    автомод), `allowed` (Meet разрешил сам), `approved` (спросили человека —
    разрешил; `grant` — «до конца встречи»), `declined` (спросили — отклонил
    или не ответил), `denied` (запрет Meet, `reason` — причина для модели);
    `why` — код причины ворот."""
    outcome = getattr(decision, "outcome", "")
    why = getattr(decision, "why", "") or ""
    if outcome == _AUTO:
        label = "auto"
    elif outcome == "allow":
        label = "approved" if why in ("confirmed", "granted-now") else "allowed"
    elif why in ("declined", "timeout", "cancelled"):
        label = "declined"
    else:
        label = "denied"
    event = {"type": "gate", "id": str(tool_use_id) if tool_use_id else None, "name": tool, "via": via,
             "decision": label, "reason": getattr(decision, "reason", "") or "", "why": why}
    if why == "granted-now":
        event["grant"] = True
    return event


def _gate_answer(gate, request: dict) -> tuple[dict, dict | None]:
    """Ответ на `can_use_tool` или хук PreToolUse по решению ворот (может
    ждать карточку подтверждения — вызывается в своём потоке) и событие
    `gate` для чата (None — запрос не о вызове инструмента).

    Хук (0.4): `auto` → `{}` (дальше — правила пользователя и режим CLI:
    автомод или `can_use_tool`); `allow` → `permissionDecision: "allow"`
    (CLI больше не спрашивает; его правила `deny` всё равно сильнее); иное
    (`deny`, а также `ask`, который ворота должны были решить карточкой) —
    отказ с причиной. `can_use_tool` — разрешение только при `allow`."""
    if request.get("subtype") == "can_use_tool":
        data = request.get("input") if isinstance(request.get("input"), dict) else {}
        tool = str(request.get("tool_name") or "")
        decision = gate.check(tool, data, tool_use_id=request.get("tool_use_id"), via="can_use_tool")
        event = _gate_event(decision, tool, request.get("tool_use_id"), "can_use_tool")
        if decision.outcome == "allow":
            return {"behavior": "allow", "updatedInput": data}, event
        return {"behavior": "deny", "message": decision.reason or "Meet не разрешил этот вызов"}, event
    hook = request.get("input") if isinstance(request.get("input"), dict) else {}
    if request.get("callback_id") != GATE_HOOK_ID or hook.get("hook_event_name") not in (None, "PreToolUse"):
        return {}, None
    data = hook.get("tool_input") if isinstance(hook.get("tool_input"), dict) else {}
    tool = str(hook.get("tool_name") or "")
    tool_use_id = hook.get("tool_use_id") or request.get("tool_use_id")
    decision = gate.check(tool, data, tool_use_id=tool_use_id, via="hook")
    event = _gate_event(decision, tool, tool_use_id, "hook")
    if decision.outcome == _AUTO:
        return {}, event
    if decision.outcome == "allow":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}, event
    if decision.outcome != "deny":
        event["decision"] = "denied"
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": decision.reason
                                   or "Meet не разрешил этот вызов"}}, event


def _safe_call(on_text, piece, log) -> None:
    try:
        on_text(piece)
    except Exception as e:  # показ не обрывает ход
        log(f"диалог: сбой обработчика ({type(e).__name__})")


def _write(proc, data: bytes) -> None:
    proc.stdin.write(data)
    proc.stdin.flush()
