"""Проверка ворот согласия на НАСТОЯЩЕМ Claude Code — без вызова модели.

Свобода по согласию (0.3.7, A1) держится на договоре с CLI, которого нет в
`claude --help`: `--permission-prompt-tool stdio` (запросы `can_use_tool`) и
хук PreToolUse из `control_request initialize` (как у claude_agent_sdk). Этот
скрипт проверяет договор на установленной версии CLI без модели:

* поддельный Anthropic API на 127.0.0.1 (поток SSE): на сообщение «CALL
  {инструмент}» «модель» вызывает этот инструмент, на результат — отвечает
  текстом «RESULT err=… <начало результата>»;
* поддельный MCP-сервер `fakejira` (get_issue — чтение, create_issue —
  изменение) — в пользовательских настройках временной CLAUDE_CONFIG_DIR, там
  же правило allow для get_issue и `defaultMode: bypassPermissions` (Meet
  обязан их перекрыть);
* `claude_stream.Conversation` с `ConsentGate`, как у агента-участника;
  карточки подтверждения отвечает сценарий («Разрешить один раз» /
  «Отклонить»);
* хуки пользователя (PreToolUse, UserPromptSubmit) во временных настройках:
  с `--settings` (disableAllHooks) они не должны запуститься, а хук Meet —
  работать; обходы из ревью (перевод строки, `&`, `env`, шаблон в закрытую
  папку, токен Meet, локальный API, фоновая команда, подагент);
* 0.4 (`_probe_cli`, «ассистент как CLI»): автомод вместе с
  `--permission-prompt-tool stdio`, режим в `initialize` и `system/init`,
  `commands`/`models` в `initialize`, control-запросы `mcp_status`,
  `mcp_reconnect`, `mcp_toggle`, `set_model`, перезапуск с `--resume`,
  `/compact` и `compact_boundary`, `allow` хука без `can_use_tool` и правила
  deny сильнее него, `disableAutoMode` → default, хук на вызовы подагента,
  `` !`…` `` своей команды. Ворота там — сценарий исходов (`_ScriptGate`),
  а не таблица `consent.py`. Строки FACT — наблюдения для решений.

Случаи `_cases` — таблица ворот (`consent.py`) по уровням хода; меняется
таблица — меняются и они.

Настройки и вход человека не трогаются: своя пустая CLAUDE_CONFIG_DIR, ключ —
выдуманный, адрес API — локальный, остальная сеть — через мёртвый прокси.

    python scripts/probe_claude_gate.py            # из корня репозитория, PYTHONPATH=src
    python scripts/probe_claude_gate.py --cli      # только договор 0.4 с CLI

Код выхода 1, если хоть один исход не совпал с ожидаемым.
"""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

MARK = "MEETPROBE"

FAKE_MCP = r'''
import json, sys
TOOLS = [{"name": "get_issue", "description": "Get an issue", "inputSchema": {"type": "object"}},
         {"name": "create_issue", "description": "Create an issue", "inputSchema": {"type": "object"}}]
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") is None:
        continue
    m = msg.get("method")
    if m == "initialize":
        res = {"protocolVersion": msg["params"].get("protocolVersion", "2024-11-05"),
               "capabilities": {"tools": {}}, "serverInfo": {"name": "fakejira", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    elif m == "tools/call":
        res = {"content": [{"type": "text", "text": "MCP " + msg["params"]["name"] + " done"}]}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}) + "\n")
    sys.stdout.flush()
'''


def _sse(name, data) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def _sub_reply(body):
    """Подагент (0.4): «модель» подагента вызывает команду из «SUBTOOL <команда>»
    и, получив результат, отвечает текстом. Не подагент — None."""
    texts, result = [], None
    for m in body.get("messages") or []:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        for b in ([{"type": "text", "text": content}] if isinstance(content, str) else content or []):
            if b.get("type") == "tool_result":
                result = b
            elif b.get("type") == "text":
                texts.append(b.get("text") or "")
    found = next((m for m in (re.search(r"SUBTOOL ([^\n\"]+)", t) for t in texts) if m), None)
    if found is None:
        return None
    first = "SUBTOOL " + found.group(1)
    if result is not None:
        c = result.get("content")
        c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
        return {"type": "text", "text": f"SUBRESULT {c[:200]}"}, "end_turn"
    return ({"type": "tool_use", "id": "toolu_sub001", "name": "Bash", "input": {},
             "_input": {"command": first[len("SUBTOOL "):].strip(), "description": "x"}}, "tool_use")


class _Api(BaseHTTPRequestHandler):
    served = 0
    bodies: list = []

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens": 10}')
            return
        _Api.bodies = (_Api.bodies + [json.dumps(body.get("messages"), ensure_ascii=False)])[-30:]
        block, stop = {"type": "text", "text": "ok"}, "end_turn"
        sub = _sub_reply(body) if MARK not in json.dumps(body.get("system")) else None
        if sub is not None:
            block, stop = sub
        elif MARK in json.dumps(body.get("system")):
            calls, last_result = [], None
            for m in body.get("messages") or []:
                if m.get("role") != "user":
                    continue
                content = m.get("content")
                for b in ([{"type": "text", "text": content}] if isinstance(content, str) else content or []):
                    if b.get("type") == "tool_result":
                        last_result = b
                    elif b.get("type") == "text" and "CALL {" in (b.get("text") or ""):
                        calls.append(b["text"])
            if calls and len(calls) > _Api.served:
                _Api.served = len(calls)
                text = calls[-1]
                call = json.loads(text[text.rfind("CALL {") + 5:].strip().splitlines()[0])
                block = {"type": "tool_use", "id": f"toolu_{_Api.served:06d}", "name": call["name"], "input": {},
                         "_input": call["input"]}
                stop = "tool_use"
            elif last_result is not None:
                c = last_result.get("content")
                c = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                block = {"type": "text", "text": f"RESULT err={bool(last_result.get('is_error'))} {c[:300]}"}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        w = self.wfile.write
        w(_sse("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model", "x"), "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}))
        if block["type"] == "text":
            w(_sse("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "text", "text": ""}}))
            w(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            data = block.pop("_input")
            w(_sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}))
            w(_sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "input_json_delta", "partial_json": json.dumps(data)}}))
        w(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        w(_sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                 "usage": {"output_tokens": 5}}))
        w(_sse("message_stop", {"type": "message_stop"}))
        self.wfile.flush()


def _cases(work: Path, meeting: Path, downloads: Path, private: Path, data: Path):
    """(уровень, название, вызов, ответ на карточку или None, ожидаемое:
    ok / gate / declined / rule / err)."""
    note, spec = str(meeting / "notes.txt"), str(downloads / "spec.txt")
    kb = private.parent
    marker = str(work / "cwd" / "pwned.txt")
    bash = lambda c, **kw: {"name": "Bash", "input": {"command": c, "description": "x", **kw}}  # noqa: E731
    return [
        ("none", "чтение в папке встречи", {"name": "Read", "input": {"file_path": note}}, None, "ok"),
        ("none", "чтение «Загрузок» без согласия", {"name": "Read", "input": {"file_path": spec}}, None, "gate"),
        ("none", "MCP get_issue (allow в настройках) без согласия",
         {"name": "mcp__fakejira__get_issue", "input": {"key": "A-1"}}, None, "gate"),
        ("none", "команда ls без согласия", bash("ls"), None, "gate"),
        ("read", "чтение «Загрузок» по просьбе (без карточки)",
         {"name": "Read", "input": {"file_path": spec}}, None, "ok"),
        ("read", "относительный Glob вверх по просьбе",
         {"name": "Glob", "input": {"pattern": "../Downloads/*.txt"}}, None, "ok"),
        ("read", "MCP get_issue по просьбе (без карточки)",
         {"name": "mcp__fakejira__get_issue", "input": {"key": "A-1"}}, None, "ok"),
        ("read", "MCP create_issue → карточка → «Отклонить»",
         {"name": "mcp__fakejira__create_issue", "input": {"title": "t"}}, "deny", "declined"),
        ("read", "MCP create_issue → карточка → «Разрешить один раз»",
         {"name": "mcp__fakejira__create_issue", "input": {"title": "t"}}, "allow", "ok"),
        ("read", "команда с переводом строки → карточка → «Отклонить»",
         bash(f"ls\necho x > \"{marker}\""), "deny", "declined"),
        ("read", "команда «ls & запись» → карточка → «Отклонить»",
         bash(f"ls & echo x > \"{marker}\""), "deny", "declined"),
        ("read", "команда env → карточка → «Отклонить»", bash(f"env touch \"{marker}\""), "deny", "declined"),
        ("read", "команда записи → карточка → «Разрешить один раз»",
         bash("echo hi > out.txt"), "allow", "ok"),
        ("read", "фоновая команда (run_in_background)", bash("sleep 1", run_in_background=True), None, "gate"),
        ("read", "запись файла → карточка → «Разрешить один раз»",
         {"name": "Write", "input": {"file_path": str(downloads / "new.txt"), "content": "x"}}, "allow", "ok"),
        ("read", "закрытая папка (Read)", {"name": "Read", "input": {"file_path": str(private / "secret.txt")}},
         None, "rule"),
        ("read", "закрытая папка через Bash-шаблон", bash(f"cat \"{kb}\"/Лич*/secret.txt"), None, "gate"),
        ("read", "закрытая папка через grep -r по базе", bash(f"grep -r TOPSECRET \"{kb}\""), None, "gate"),
        ("read", "токен Meet (чувствительное)", {"name": "Read", "input": {"file_path": str(data / "api.token")}},
         None, "gate"),
        ("read", "WebFetch на локальный API Meet",
         {"name": "WebFetch", "input": {"url": "http://127.0.0.1:8766/live/chat", "prompt": "x"}}, None, "gate"),
        ("read", "WebFetch наружу → карточка → «Отклонить»",
         {"name": "WebFetch", "input": {"url": "https://example.com/?q=1", "prompt": "x"}}, "deny", "declined"),
        ("read", "длинные пробелы прячут хвост → в карточке хвост виден → «Отклонить»",
         bash("ls" + " " * 3000 + f"; echo x > \"{marker}\""), "deny-if-tail", "declined"),
        ("read", "огромный вызов (больше 100 000 симв.)", bash("echo " + "x" * 100_001), None, "gate"),
        ("read", "безопасная команда ls — без карточки", bash("ls"), None, "ok"),
        ("read", "безопасная команда cat файла из «Загрузок» — без карточки",
         bash(f"cat {spec.replace(chr(92), '/')}"), None, "ok"),
        ("read", "пустой хангыль прячет хвост (U+3164)", bash("ls" + "ㅤ" * 300 + "; touch x"), None, "gate"),
        ("read", "команда без песочницы → карточка с предупреждением → «Отклонить»",
         bash("ls", dangerouslyDisableSandbox=True), "deny-if-warned", "declined"),
        ("read", "MCP create_issue → «Разрешать такое до конца встречи»",
         {"name": "mcp__fakejira__create_issue", "input": {"title": "first"}}, "allow_meeting", "ok"),
        ("read", "MCP create_issue ещё раз — уже без карточки",
         {"name": "mcp__fakejira__create_issue", "input": {"title": "second"}}, None, "ok"),
        ("read", "команда hostname → «Разрешать такое до конца встречи» («Bash: hostname»)",
         bash("hostname"), "allow_meeting", "ok"),
        ("read", "hostname ещё раз — уже без карточки", bash("hostname"), None, "ok"),
        ("read", "запись в папку встречи → «Разрешать такое до конца встречи» («изменение файлов в …»)",
         {"name": "Write", "input": {"file_path": str(meeting / "probe.txt"), "content": "one"}}, "allow_meeting",
         "ok"),
        ("read", "Edit того же файла после разрешения на Write — без карточки",
         {"name": "Edit", "input": {"file_path": str(meeting / "probe.txt"), "old_string": "one",
                                    "new_string": "two"}}, None, "ran"),
        ("read", "python -c — карточка без «до конца встречи» → «Отклонить»",
         bash('python -c "print(1)"'), "deny-if-no-grant", "declined"),
        ("read", "curl к api.example.com → «Разрешать такое до конца встречи»",
         bash("curl -s https://api.example.com/a"), "allow_meeting", "ran"),
        ("read", "curl --resolve на чужой IP при разрешённом хосте → карточка → «Отклонить»",
         bash("curl --resolve api.example.com:443:6.6.6.6 https://api.example.com/a"), "deny", "declined"),
        ("read", "curl -XPOST при разрешённом GET → карточка → «Отклонить»",
         bash("curl -XPOST https://api.example.com/a"), "deny", "declined"),
        ("read", "скрытый символ смены направления (U+202E)", bash("echo ok #‮dcba"), None, "gate"),
        ("read", "MCP get_issue с локальным адресом в аргументе",
         {"name": "mcp__fakejira__get_issue", "input": {"key": "http://127.0.0.1:8766/?token=x"}}, None, "gate"),
        ("read", "WebFetch на 127.1 (короткая запись)",
         {"name": "WebFetch", "input": {"url": "http://127.1:8766/live/chat", "prompt": "x"}}, None, "gate"),
        ("read", "подагент (Agent)", {"name": "Agent", "input": {"description": "x", "prompt": "ls",
                                                                 "subagent_type": "general-purpose"}},
         None, "err"),
    ]


def _outcome(text: str) -> str:
    if "err=False" in text:
        return "ok"
    if "Пользователь отклонил" in text:
        return "declined"
    if "Meet заблокировал" in text:
        return "gate"
    if "denied by your permission settings" in text:
        return "rule"
    if "err=True" in text:
        return "err"
    return "?"


async def _probe(work: Path, port: int, out=print) -> int:
    import os

    cfg, meeting, downloads, kb = work / "cfg", work / "meeting", work / "Downloads", work / "kb"
    data = work / "meet-data"
    private = kb / "Личное"
    for d in (cfg, meeting, downloads, private, work / "cwd", data):
        d.mkdir(parents=True, exist_ok=True)
    os.environ["MEET_DATA_DIR"] = str(data)   # служебная папка Meet — временная
    from meet.llm import consent
    from meet.llm.claude_stream import Conversation

    (meeting / "notes.txt").write_text("MEETING-NOTE", encoding="utf-8")
    (downloads / "spec.txt").write_text("DOWNLOADED-SPEC", encoding="utf-8")
    (private / "secret.txt").write_text("TOPSECRET-42", encoding="utf-8")
    (data / "api.token").write_text("TOKEN-SECRET", encoding="utf-8")
    hook_marker = work / "user-hook-ran.txt"
    mcp = work / "fake_mcp.py"
    mcp.write_text(FAKE_MCP, encoding="utf-8")
    (cfg / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "mcpServers": {
        "fakejira": {"type": "stdio", "command": sys.executable, "args": [str(mcp)]}}}), encoding="utf-8")
    user_hook = {"type": "command",
                 "command": f'"{sys.executable}" -c "open(r\'{hook_marker}\', \'a\').write(\'x\')"'}
    (cfg / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["mcp__fakejira__get_issue", "Bash(echo:*)"], "defaultMode": "bypassPermissions"},
        "hooks": {"PreToolUse": [{"matcher": "", "hooks": [user_hook]}],
                  "UserPromptSubmit": [{"hooks": [user_hook]}]}}), encoding="utf-8")

    def popen(cmd, **kw):
        env = {k: v for k, v in kw.pop("env").items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
        env.update({"CLAUDE_CONFIG_DIR": str(cfg), "ANTHROPIC_API_KEY": "sk-ant-fake-probe",
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "HTTPS_PROXY": "http://127.0.0.1:9",
                    "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        return subprocess.Popen(cmd, env=env, **kw)

    exe = shutil.which("claude")
    if not exe:
        out("claude не найден — проверять нечего")
        return 1
    answers: list[str] = []
    cards: list[dict] = []

    def confirmer(card):
        cards.append(card)
        answer = answers.pop(0) if answers else "deny"
        if answer == "deny-if-tail":     # хвост команды обязан быть в карточке
            return "deny" if "pwned.txt" in card["args"] and "⟨3000 пробелов⟩" in card["args"] else "allow"
        if answer == "deny-if-no-grant":  # интерпретатор — «до конца встречи» не предлагается
            return "deny" if not card.get("grant") else "allow"
        if answer == "deny-if-warned":   # «без песочницы» обязано быть в карточке
            return "deny" if any("dangerouslyDisableSandbox" in w for w in card.get("warnings") or []) else "allow"
        return answer

    gate = consent.ConsentGate(own_dirs=[meeting], deny_paths=[private], cwd=work / "cwd", confirmer=confirmer)
    conv = Conversation(system_prompt=f"{MARK} agent", cli=[exe], popen=popen, cwd=work / "cwd", responder=True,
                        add_dirs=[meeting], deny_paths=[private], gate=gate, mode="confirm")
    failed = 0
    try:
        for n, (level, title, call, answer, want) in enumerate(
                _cases(work, meeting, downloads, private, data), 1):
            answers[:] = [answer] if answer else []
            cards.clear()
            gate.begin(level)
            try:
                reply = await conv.send(f"шаг {n}: CALL " + json.dumps(call, ensure_ascii=False), timeout_s=120)
            finally:
                gate.end()
            got = _outcome(reply.text or "")
            if want == "ran" and got in ("ok", "err"):   # выполнилось (сети в пробе нет — ошибка curl не важна)
                got = "ran"
            ok = got == want and not reply.error and (bool(cards) == bool(answer))
            failed += not ok
            line = consent.denial_line(gate.take_denials())
            card = (f"  карточка: «{cards[0]['title']}» {(cards[0]['args'].splitlines() or [''])[0][:80]}"
                    if cards else "")
            out(f"{'PASS' if ok else 'FAIL'}  {level:4}  {title:<56} ждали {want:<8} получили {got}{card}"
                + (f"  ошибка: {reply.error}" if reply.error else ""))
            if line:
                out(f"            чат: {line}")
        marker = work / "cwd" / "pwned.txt"
        checks = [
            ("отклонённые команды не выполнились", not marker.exists()),
            ("хуки пользователя выключены (--settings disableAllHooks), хук Meet работает",
             not hook_marker.exists()),
            ("MCP-серверы из init", [s.get("name") for s in conv.mcp_servers or []] == ["fakejira"]),
        ]
        for title, ok in checks:
            failed += not ok
            out(f"{'PASS' if ok else 'FAIL'}  {title}")
    finally:
        conv.close()
    return 1 if failed else 0


class _ScriptGate:
    """Ворота с заданными исходами (0.4: allow / auto / deny) — проверяется
    договор с CLI, а не таблица `consent.py`. Записывает вызовы (инструмент,
    путь: hook / can_use_tool)."""

    def __init__(self, rules=None, default="auto"):
        self.rules, self.default = dict(rules or {}), default
        self.calls: list[tuple[str, str]] = []
        self._seen: set[str] = set()

    def begin(self, _level):
        self._seen = set()

    def end(self):
        pass

    def unseen(self, ids):
        return [t for t in ids or () if t and t not in self._seen]

    def check(self, tool, data=None, *, tool_use_id=None, via="hook"):
        from meet.llm import consent

        self.calls.append((tool, via))
        if via == "hook" and tool_use_id:
            self._seen.add(tool_use_id)
        outcome = self.rules.get(tool, self.default) if via == "hook" else "allow"
        return consent.Decision(outcome, reason="Meet заблокировал (проба)" if outcome == "deny" else "", what=tool)


def _popen_for(cfg: Path, port: int):
    def popen(cmd, **kw):
        env = {k: v for k, v in kw.pop("env").items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
        env.update({"CLAUDE_CONFIG_DIR": str(cfg), "ANTHROPIC_API_KEY": "sk-ant-fake-probe",
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "HTTPS_PROXY": "http://127.0.0.1:9",
                    "HTTP_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        return subprocess.Popen(cmd, env=env, **kw)
    return popen


def _cli_config(cfg: Path, mcp: Path, settings: dict | None = None) -> None:
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "mcpServers": {
        "fakejira": {"type": "stdio", "command": sys.executable, "args": [str(mcp)]},
        "deadsrv": {"type": "stdio", "command": "meet-probe-no-such-exe", "args": []}}}), encoding="utf-8")
    (cfg / "settings.json").write_text(json.dumps(settings or {}), encoding="utf-8")


async def _probe_cli(work: Path, port: int, exe: str, out=print) -> int:
    """Договор 0.4 с CLI (спецификация §3, §6, «Пробник»): автомод вместе с
    `--permission-prompt-tool stdio`; `permissionMode` в init и
    `current_permission_mode`, `commands`, `models` в `initialize`; подтипы
    `mcp_status` / `mcp_reconnect` / `mcp_toggle` / `set_model`; перезапуск с
    `--resume`; `/compact` и `compact_boundary`; `allow` хука без
    `can_use_tool`, правила deny сильнее; автомод без карточки для записи в
    рабочей папке; хук на вызовы подагента; `` !`…` `` своей команды.
    PASS/FAIL — проверка; FACT — наблюдение для решения (не ошибка)."""
    from meet.llm import claude_stream
    from meet.llm.claude_stream import ControlError, Conversation

    failed = 0

    def check(title: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        failed += not ok
        out(f"{'PASS' if ok else 'FAIL'}  0.4  {title}" + (f"  ({detail})" if detail else ""))

    def fact(title: str, detail) -> None:
        out(f"FACT  0.4  {title}: {detail}")

    mcp = work / "fake_mcp_cli.py"
    mcp.write_text(FAKE_MCP, encoding="utf-8")
    cfg, cwd = work / "cfg-cli", work / "cwd-cli"
    _cli_config(cfg, mcp)
    commands = cwd / ".claude" / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    (commands / "hello.md").write_text("---\ndescription: say hello\nargument-hint: [name]\n---\nSay hello $ARGUMENTS\n",
                                       encoding="utf-8")
    (commands / "bangok.md").write_text("---\ndescription: bang allowed\nallowed-tools: Bash(echo:*)\n---\n"
                                        "Out: !`echo BANG-ALLOWED-RAN`\n", encoding="utf-8")
    (commands / "bangask.md").write_text("---\ndescription: bang needs approval\n---\n"
                                         "Out: !`echo x > bang-marker.txt`\n", encoding="utf-8")
    popen = _popen_for(cfg, port)
    gate = _ScriptGate()
    events: list[dict] = []
    conv = Conversation(system_prompt=f"{MARK} cli", cli=[exe], popen=popen, cwd=cwd, responder=True,
                        add_dirs=[cwd], gate=gate, mode="auto", persist=True, model="sonnet")

    async def turn(text: str, level="user"):
        gate.begin(level)
        try:
            return await conv.send(text, timeout_s=120, on_event=events.append)
        finally:
            gate.end()

    try:
        # initialize — до первого хода, модель не зовётся.
        await conv.control("mcp_status")
        check("автомод вместе с --permission-prompt-tool stdio: initialize → current_permission_mode",
              conv.permission_mode == "auto", str(conv.permission_mode))
        check("initialize → commands (name, description, argumentHint)",
              any(c["name"] == "hello" and c["hint"] == "name" for c in conv.commands),
              f"{len(conv.commands)} команд")
        check("initialize → models", bool(conv.models), ", ".join(m.get("value", "") for m in conv.models))
        reply = await turn("hello")
        check("system/init → permissionMode, slash_commands, mcp_servers",
              conv.permission_mode == "auto" and "compact" in (conv.slash_commands or [])
              and {s["name"] for s in conv.mcp_servers or []} == {"fakejira", "deadsrv"},
              f"{conv.permission_mode}; {conv.mcp_servers}")
        servers = await conv.mcp_status(settle_s=claude_stream.MCP_SETTLE_S)
        state = {s["name"]: s for s in servers}
        check("mcp_status: fakejira connected, deadsrv failed с ошибкой",
              state.get("fakejira", {}).get("status") == "connected"
              and state.get("deadsrv", {}).get("status") == "failed" and bool(state["deadsrv"].get("error")),
              str(servers))
        res = await conv.mcp_reconnect("fakejira")
        check("mcp_reconnect живого сервера — успех без перезапуска",
              res["results"] == {"fakejira": None} and not res["restarted"], str(res["results"]))
        try:
            await conv.control("mcp_reconnect", {"serverName": "nope"})
            check("mcp_reconnect неизвестного сервера — ошибка", False)
        except ControlError as e:
            check("mcp_reconnect неизвестного сервера — ошибка", not e.unsupported, e.message)
        pid, spawns = conv.pid, conv.spawns
        res = await conv.mcp_reconnect("deadsrv")
        check("mcp_reconnect сбойного — ошибка → перезапуск с --resume, разговор тот же",
              res["restarted"] and res["context"] and conv.spawns == spawns + 1 and conv.pid != pid
              and res["results"]["deadsrv"] is not None, str(res["results"]))
        reply = await turn("after restart")
        check("ход после перезапуска — без ошибки «сеанс не продолжить»", reply.error is None, reply.error or "")
        await conv.mcp_toggle("fakejira", False)
        off = {s["name"]: s["status"] for s in await conv.mcp_status()}
        await conv.mcp_toggle("fakejira", True)
        on = {s["name"]: s["status"] for s in await conv.mcp_status(settle_s=claude_stream.MCP_SETTLE_S)}
        check("mcp_toggle: disabled и обратно connected",
              off.get("fakejira") == "disabled" and on.get("fakejira") == "connected", f"{off} → {on}")
        resolved = await conv.set_model("opus")
        check("set_model opus — полное имя из initialize.models", resolved.startswith("claude-opus"), resolved)
        try:
            await conv.control("compact")
            fact("control-запрос compact", "есть")
        except ControlError as e:
            fact("control-запрос compact", f"нет ({e.message}) — /compact уходит сообщением")
        reply = await conv.compact()
        check("/compact → system/compact_boundary (pre_tokens/post_tokens)",
              bool(reply.compacted) and isinstance(reply.compacted.get("pre_tokens"), int), str(reply.compacted))
        # Своя команда с !`…`: разрешённая allowed-tools выполняется CLI сама — мимо хука?
        gate.calls.clear()
        await turn("/bangok")
        ran = any("BANG-ALLOWED-RAN" in b and "echo BANG-ALLOWED-RAN" not in b for b in _Api.bodies[-3:])
        hooked = ("Bash", "hook") in gate.calls
        fact("!`…` своей команды при allowed-tools", f"выполнено={ran}, хук PreToolUse={hooked}")
        gate.calls.clear()
        await turn("/bangask")
        deferred = any("run this first" in b and "bang-marker" in b for b in _Api.bodies[-3:])
        fact("!`…` без разрешения в автомоде", f"не выполнено (маркер {(cwd / 'bang-marker.txt').exists()}), "
             f"отдано модели текстом={deferred}, хук={('Bash', 'hook') in gate.calls}")
        # Запись в рабочей папке в автомоде: хук отвечает {} — без can_use_tool.
        gate.calls.clear()
        events.clear()
        reply = await turn("auto CALL " + json.dumps({"name": "Bash", "input": {
            "command": "echo hi > auto-out.txt", "description": "x"}}))
        cli = next((e.get("cli_decision") for e in events if e["type"] == "tool_result"), None)
        check("автомод: запись в рабочей папке после {} хука — без can_use_tool",
              (cwd / "auto-out.txt").exists() and ("Bash", "can_use_tool") not in gate.calls, str(cli))
    finally:
        conv.close()

    # confirm (default): allow хука — без can_use_tool; правила deny CLI сильнее allow.
    _Api.served = 0
    cfg2, cwd2 = work / "cfg-confirm", work / "cwd-confirm"
    cwd2.mkdir(parents=True, exist_ok=True)
    _cli_config(cfg2, mcp)
    private = work / "private-cli"
    private.mkdir(exist_ok=True)
    (private / "s.txt").write_text("PRIVATE", encoding="utf-8")
    gate2 = _ScriptGate(default="allow")
    conv2 = Conversation(system_prompt=f"{MARK} confirm", cli=[exe], popen=_popen_for(cfg2, port), cwd=cwd2,
                         responder=True, add_dirs=[cwd2], deny_paths=[private], gate=gate2, mode="confirm")
    try:
        outside = work / "outside-cli.txt"
        gate2.begin("user")
        reply = await conv2.send("c1 CALL " + json.dumps({"name": "Bash", "input": {
            "command": f"echo hi > \"{outside}\"", "description": "x"}}), timeout_s=120)
        gate2.end()
        check("confirm: allow хука — запись вне рабочих папок без can_use_tool",
              outside.exists() and ("Bash", "can_use_tool") not in gate2.calls,
              f"{conv2.permission_mode}; {reply.text[:80]}")
        gate2.begin("user")
        reply = await conv2.send("c2 CALL " + json.dumps({"name": "Read", "input": {
            "file_path": str(private / "s.txt")}}), timeout_s=120)
        gate2.end()
        check("правило deny CLI сильнее allow хука", _outcome(reply.text or "") == "rule" and
              "PRIVATE" not in (reply.text or ""), reply.text[:100])
    finally:
        conv2.close()

    # Автомод выключен настройкой — CLI молча в default.
    cfg3, cwd3 = work / "cfg-noauto", work / "cwd-noauto"
    cwd3.mkdir(parents=True, exist_ok=True)
    _cli_config(cfg3, mcp, {"permissions": {"disableAutoMode": "disable"}})
    conv3 = Conversation(system_prompt=f"{MARK} noauto", cli=[exe], popen=_popen_for(cfg3, port), cwd=cwd3,
                         responder=True, add_dirs=[cwd3], gate=_ScriptGate(), mode="auto")
    try:
        await conv3.control("mcp_status")
        check("disableAutoMode — CLI стартует в default, auto_unavailable",
              conv3.permission_mode == "default" and conv3.auto_unavailable, str(conv3.permission_mode))
    finally:
        conv3.close()

    # Подагент: хук PreToolUse на его вызовы; запускается ли он в фоне.
    cfg4, cwd4 = work / "cfg-sub", work / "cwd-sub"
    cwd4.mkdir(parents=True, exist_ok=True)
    _cli_config(cfg4, mcp)
    _Api.served = 0
    saved = claude_stream.FREE_DISALLOWED
    claude_stream.FREE_DISALLOWED = tuple(t for t in saved if t not in ("Agent", "Task"))
    gate4 = _ScriptGate(default="allow")
    events4: list[dict] = []
    conv4 = Conversation(system_prompt=f"{MARK} sub", cli=[exe], popen=_popen_for(cfg4, port), cwd=cwd4,
                         responder=True, add_dirs=[cwd4], gate=gate4, mode="confirm")
    try:
        gate4.begin("user")
        await conv4.send("s1 CALL " + json.dumps({"name": "Agent", "input": {
            "description": "probe", "prompt": "SUBTOOL echo SUBRAN", "subagent_type": "general-purpose"}}),
            timeout_s=120, on_event=events4.append)
        for _ in range(40):                       # подагент мог уйти в фон — подождать его вызов
            if ("Bash", "hook") in gate4.calls:
                break
            await asyncio.sleep(0.25)
        gate4.end()
        agent_out = next((e["output"] for e in events4 if e["type"] == "tool_result"), "")
        fact("Agent без run_in_background", "в фоне (Async agent launched)" if "Async agent" in agent_out
             else "на переднем плане")
        fact("хук PreToolUse на вызов подагента", ("Bash", "hook") in gate4.calls)
    finally:
        conv4.close()
        claude_stream.FREE_DISALLOWED = saved
    return failed


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        version = subprocess.run([shutil.which("claude") or "claude", "--version"], capture_output=True,
                                 text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = "?"
    print(f"claude {version}; поддельный API, модель не вызывается")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="meet-probe-gate-"))
    exe = shutil.which("claude")
    try:
        failed = 0
        if exe:
            failed += asyncio.run(_probe_cli(work, server.server_address[1], exe))
        if "--cli" not in sys.argv[1:]:
            _Api.served = 0
            failed += asyncio.run(_probe(work, server.server_address[1]))
        return 1 if failed or not exe else 0
    finally:
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
