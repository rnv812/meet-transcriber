"""Поддельный Claude Code CLI в режиме stream-json — для тестов постоянного
диалога (`meet.llm.claude_stream`). Настоящую модель не зовёт.

Режим — переменная FAKE_CLAUDE_MODE:
  ok     — на каждое сообщение: init (один раз), куски текста, result с usage;
  crash  — на втором сообщении процесс падает;
  hang   — на сообщение не отвечает.
FAKE_CLAUDE_LOG — файл, куда пишутся argv, cwd, окружение (JSON) и сообщения.
"""

import json
import os
import sys


def main() -> int:
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    log = open(log_path, "a", encoding="utf-8") if log_path else None
    if log:
        log.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
                              "env": sorted(os.environ)}, ensure_ascii=False) + "\n")
        log.flush()
    out = sys.stdout
    n = 0
    for raw in sys.stdin:
        msg = json.loads(raw)
        n += 1
        if log:
            log.write(json.dumps({"message": msg}, ensure_ascii=False) + "\n")
            log.flush()
        if mode == "crash" and n == 2:
            sys.stderr.write("fatal: something broke\n")
            return 3
        if mode == "hang":
            continue
        if n == 1:
            out.write(json.dumps({"type": "system", "subtype": "init"}) + "\n")
        text = '{"op":"none"}\n'
        for piece in (text[:7], text[7:]):
            out.write(json.dumps({"type": "stream_event", "event": {
                "type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}}) + "\n")
            out.flush()
        out.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        out.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                              "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000 * n,
                                        "cache_creation_input_tokens": 200, "output_tokens": 30}}) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
