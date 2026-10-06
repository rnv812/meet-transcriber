"""Поддельный Claude Code CLI в режиме stream-json — для тестов постоянного
диалога (`meet.llm.claude_stream`). Настоящую модель не зовёт.

Режим — переменная FAKE_CLAUDE_MODE:
  ok     — на каждое сообщение: init (один раз), куски текста, result с usage;
  crash  — на втором сообщении процесс падает;
  hang   — на сообщение не отвечает;
  slow   — на сообщение: init и первый кусок текста, а result — только после
           остановки (`control_request` interrupt → `control_response`, затем
           result с ошибкой, как у claude 2.1.292);
  deaf   — как slow, но на остановку не отвечает вовсе;
  tool   — перед ответом спрашивает разрешение инструмента (`control_request`
           can_use_tool) и ждёт ответа хозяина; затем текст пояснения, новое
           сообщение модели и ответ.
`--resume=<id>`: id из FAKE_CLAUDE_KNOWN (через запятую) продолжается, иначе —
как настоящий CLI: «No conversation found with session ID» в stderr, result с
ошибкой, код 1 — сразу, до чтения stdin.
FAKE_CLAUDE_LOG — файл, куда пишутся argv, cwd, окружение (JSON) и сообщения.
"""

import json
import os
import sys
import uuid


def _arg(argv: list[str], name: str) -> str | None:
    for a in argv:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def main() -> int:
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    log = open(log_path, "a", encoding="utf-8") if log_path else None
    argv = sys.argv[1:]
    if log:
        log.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(),
                              "env": sorted(os.environ)}, ensure_ascii=False) + "\n")
        log.flush()
    out = sys.stdout

    def emit(obj) -> None:
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    resume = _arg(argv, "--resume")
    session = resume or _arg(argv, "--session-id") or str(uuid.uuid4())
    if resume is not None:
        known = [k for k in os.environ.get("FAKE_CLAUDE_KNOWN", "").split(",") if k]
        if resume not in known:
            text = f"No conversation found with session ID: {resume}"
            sys.stderr.write(text + "\n")
            sys.stderr.flush()
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "num_turns": 0, "session_id": resume, "errors": [text]})
            return 1

    def result(n: int, text: str | None, error: bool = False) -> None:
        emit({"type": "result", "subtype": "error_during_execution" if error else "success",
              "is_error": error, "result": text, "session_id": session,
              "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000 * n,
                        "cache_creation_input_tokens": 200, "output_tokens": 30}})

    def delta(piece: str) -> None:
        emit({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})

    n = 0
    pending = False  # ход начат и ждёт остановки (slow/deaf)
    for raw in sys.stdin:
        msg = json.loads(raw)
        if log:
            log.write(json.dumps({"message": msg}, ensure_ascii=False) + "\n")
            log.flush()
        if msg.get("type") == "control_request":
            if mode == "deaf":
                continue
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": msg.get("request_id"),
                "response": {"still_queued": []}}})
            if pending:
                emit({"type": "user", "message": {"role": "user", "content": [
                    {"type": "text", "text": "[Request interrupted by user]"}]}, "session_id": session})
                result(n, None, error=True)
                pending = False
            continue
        if msg.get("type") == "control_response":
            continue  # ответ хозяина на наш can_use_tool (записан в журнал выше)
        n += 1
        if mode == "crash" and n == 2:
            sys.stderr.write("fatal: something broke\n")
            return 3
        if mode == "hang":
            continue
        if n == 1:
            emit({"type": "system", "subtype": "init", "session_id": session})
        if mode in ("slow", "deaf"):
            delta("начало ответа")
            pending = True
            continue
        if mode == "tool":
            emit({"type": "control_request", "request_id": "cli_1", "request": {
                "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "dir"}}})
            reply = json.loads(sys.stdin.readline())
            if log:
                log.write(json.dumps({"message": reply}, ensure_ascii=False) + "\n")
                log.flush()
            emit({"type": "stream_event", "event": {"type": "message_start"}})
            delta("посмотрю заметки")
            emit({"type": "stream_event", "event": {"type": "message_start"}})
            delta("ответ")
            result(n, "ответ")
            continue
        text = '{"op":"none"}\n'
        for piece in (text[:7], text[7:]):
            delta(piece)
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
        result(n, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
