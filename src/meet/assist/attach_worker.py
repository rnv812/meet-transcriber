"""Разбор вложения к чату записи после встречи — отдельным коротким процессом
(ревью after-chat, I1).

Документы (pypdf, OOXML) и картинки (Pillow) пользователя разбираются не в
резиденте, который ведёт запись: сбой разборщика или всплеск памяти гасит
только этот процесс, а резидент убивает его по сроку и убирает то, что он
успел положить в папку встречи. Разбор, пределы и квоты — те же, что у
агента во время встречи (`participant.parse_attachment`).

    python -m meet.assist.attach_worker <папка встречи> --vision=1 --path=<файл>
    python -m meet.assist.attach_worker <папка встречи> --vision=0 --name=<имя>  < байты картинки

В stdout — одна строка JSON: `{"ok": true, "fields": {…}}` (поля записи
журнала `attachment`) или `{"ok": false, "fields": {…, "status": "failed"}}`.
Журнал пишет резидент.
"""

import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="meet.assist.attach_worker")
    parser.add_argument("folder")
    parser.add_argument("--vision", choices=("0", "1"), default="1")
    parser.add_argument("--path")
    parser.add_argument("--name")
    args = parser.parse_args(argv)

    from meet.assist import participant

    if args.path:
        item = args.path
    else:
        item = {"data": sys.stdin.buffer.read(), "name": args.name or None}
    try:
        fields, _image = participant.parse_attachment(args.folder, item, vision=args.vision == "1")
        out = {"ok": True, "fields": fields}
    except (ValueError, OSError, TypeError) as e:
        out = {"ok": False, "fields": participant.failed_attachment(item, e)}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
