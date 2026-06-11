import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="meet", description="Локальный транскрибатор встреч"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="записать встречу (системный звук + микрофон)")
    p_rec.add_argument("--out", default="recordings", help="папка для записей")

    p_tr = sub.add_parser("transcribe", help="транскрибировать запись")
    p_tr.add_argument("path", help="папка записи (sys.wav+mic.wav) или аудио/видеофайл")

    args = parser.parse_args()
    if args.command == "record":
        from meet.recorder import record

        record(args.out)
    else:
        from meet.transcribe import transcribe

        transcribe(args.path)
