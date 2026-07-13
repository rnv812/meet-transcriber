import argparse
import os
import sys


def _quiet_known_warnings() -> None:
    """Глушит безобидные ворнинги зависимостей, чтобы не зашумлять вывод.

    Все они ожидаемы на Windows/GPU и на результат не влияют; см. CLAUDE.md.
    """
    import logging
    import warnings

    # torchcodec не грузится на Windows — diarize.py сам читает wav (см. CLAUDE.md).
    # Сообщение многострочное и начинается с переноса, поэтому ведущий \s*.
    warnings.filterwarnings(
        "ignore", message=r"\s*torchcodec is not installed correctly"
    )
    # pyannote осознанно выключает TF32 ради воспроизводимости.
    warnings.filterwarnings(
        "ignore", message=r"TensorFloat-32 .* has been disabled"
    )
    # Краевой случай пулинга эмбеддингов на сверхкоротком сегменте.
    warnings.filterwarnings(
        "ignore", message=r"std\(\): degrees of freedom is <= 0"
    )
    # triton не ставится на Windows; flop-профилирование не используется.
    logging.getLogger("torch.utils.flop_counter").setLevel(logging.ERROR)


def main() -> None:
    _quiet_known_warnings()
    # Страховка от UnicodeEncodeError: cp866-консоль Windows не кодирует часть
    # Юникода, а падение print не должно ломать пайплайн — лучше '?' в логе.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="meet", description="Локальный транскрибатор встреч"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rec = sub.add_parser("record", help="записать встречу (системный звук + микрофон)")
    p_rec.add_argument("--out", default="recordings", help="папка для записей")

    p_tr = sub.add_parser("transcribe", help="транскрибировать запись")
    p_tr.add_argument("path", help="папка записи (sys+mic) или аудио/видеофайл")
    p_tr.add_argument(
        "--speakers",
        type=int,
        help="число говорящих в записи (для папки записи — не считая вас); "
        "помогает не потерять тех, кто говорил мало",
    )
    p_tr.add_argument(
        "--hotwords",
        help="термины через запятую, подсказка распознаванию "
        "(например: 'джоба, экшен, коррелятор')",
    )
    p_tr.add_argument(
        "--no-align",
        dest="align",
        action="store_false",
        help="без forced alignment (быстрее, но грубее стыки спикеров)",
    )
    p_tr.add_argument(
        "--no-overlap",
        dest="overlap",
        action="store_false",
        help="без overlap-aware диаризации: прежний exclusive-режим, "
        "без пометок зон нахлёста",
    )

    p_live = sub.add_parser(
        "live", help="живой режим: запись + потоковая расшифровка для ассистента"
    )
    p_live.add_argument("--out", default="recordings", help="папка для записей")
    p_live.add_argument(
        "--window", type=float, default=20.0, help="длина окна расшифровки, сек"
    )
    p_live.add_argument(
        "--hotwords", help="термины через запятую, подсказка распознаванию"
    )
    p_live.add_argument(
        "--no-voices", action="store_true",
        help="не подписывать сегменты именами из базы голосов",
    )

    p_as = sub.add_parser(
        "assist", help="live-ассистент: расшифровка + дайджест + вопросы (веб)"
    )
    p_as.add_argument("--out", default="recordings", help="папка для записей")
    p_as.add_argument(
        "--window", type=float, default=20.0, help="длина окна расшифровки, сек"
    )
    p_as.add_argument(
        "--hotwords", help="термины через запятую, подсказка распознаванию"
    )
    p_as.add_argument("--task", help="задача из Obsidian — контекст встречи")
    p_as.add_argument(
        "--vault",
        default=os.environ.get("MEET_VAULT"),
        help="папка заметок Claude в Obsidian (default: env MEET_VAULT)",
    )
    p_as.add_argument("--port", type=int, default=8765, help="порт веб-страницы")
    p_as.add_argument(
        "--no-voices", action="store_true",
        help="не подписывать сегменты именами из базы голосов",
    )

    p_en = sub.add_parser(
        "enroll", help="запомнить голоса: «Спикер N» из записи → имя в базе голосов"
    )
    p_en.add_argument("path", help="папка записи (или транскрипт / *_speakers.json)")
    p_en.add_argument(
        "mapping", nargs="+", help="соответствия вида 'Спикер 1=Демьян Петров'"
    )

    p_cmp = sub.add_parser(
        "compare", help="пословный диф спикеров между двумя транскриптами"
    )
    p_cmp.add_argument("a", help="транскрипт A (например, текущий пайплайн)")
    p_cmp.add_argument("b", help="транскрипт B (вариант для сравнения)")

    args = parser.parse_args()
    if args.command == "record":
        from meet.recorder import record

        record(args.out)
    elif args.command == "live":
        from meet.gpu_lock import hold_gpu_lock
        from meet.live import run_live

        with hold_gpu_lock("live"):
            run_live(args.out, window_seconds=args.window, hotwords=args.hotwords,
                     no_voices=args.no_voices)
    elif args.command == "assist":
        from meet.assist.app import run_assist
        from meet.gpu_lock import hold_gpu_lock

        with hold_gpu_lock("assist"):
            run_assist(args.out, window_seconds=args.window, hotwords=args.hotwords,
                       task=args.task, vault=args.vault, port=args.port,
                       no_voices=args.no_voices)
    elif args.command == "enroll":
        from meet.voices import enroll

        enroll(args.path, args.mapping)
    elif args.command == "compare":
        from meet.compare import run_compare

        run_compare(args.a, args.b)
    else:
        from meet.gpu_lock import hold_gpu_lock
        from meet.transcribe import transcribe

        with hold_gpu_lock("transcribe"):
            transcribe(
                args.path,
                speakers=args.speakers,
                hotwords=args.hotwords,
                align=args.align,
                overlap=args.overlap,
            )
