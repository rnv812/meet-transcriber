import argparse
import sys

from meet import settings


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


OUT_HELP = "папка для записей (по умолчанию — из настроек)"


def _yes_no_unknown(value) -> str:
    """Сигнал детектора: None значит «ответить нечем» (ключа в реестре нет,
    pycaw не встал) — это не то же самое, что «нет»."""
    return "неизвестно" if value is None else ("да" if value else "нет")


def print_status() -> None:
    """Состояние резидента человеку в консоль.

    Идёт через тот же control API, что и панель: если печать здесь врёт, врёт и
    панель — один источник правды вместо двух."""
    from meet import control
    from meet.output import fmt_ts

    try:
        snap = control.request("/state")
    except RuntimeError as e:
        raise SystemExit(f"Статус недоступен: {e}")
    if snap.get("status") == "recording":
        source = "вручную" if snap.get("source") == "manual" else "автоматически"
        print(f"Идёт запись ({source}): {snap.get('folder')}")
        print(f"Длительность: {fmt_ts(snap.get('elapsed_s') or 0)}")
        levels = snap.get("levels") or {}
        if levels:
            print("Уровни: " + ", ".join(
                f"{name} {value:.2f}" for name, value in sorted(levels.items())
            ))
    else:
        print("Записи нет.")
    auto = snap.get("auto_record") or {}
    print(
        f"Автозапись: {'включена' if auto.get('enabled') else 'выключена'}"
        f", процессы: {', '.join(auto.get('processes') or []) or 'нет'}"
    )
    print(
        f"Детектор: состояние {auto.get('state')}, "
        f"микрофон {_yes_no_unknown(auto.get('mic'))}, "
        f"звук {_yes_no_unknown(auto.get('render'))}"
    )
    print(f"Папка записей: {snap.get('recordings_dir')}")
    if snap.get("gpu_busy"):
        print("GPU занят: идёт транскрибация или живой режим")


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
    p_rec.add_argument("--out", default=None, help=OUT_HELP)

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
        default=None,
        help="без forced alignment (быстрее, но грубее стыки спикеров)",
    )
    p_tr.add_argument(
        "--no-overlap",
        dest="overlap",
        action="store_false",
        default=None,
        help="без overlap-aware диаризации: прежний exclusive-режим, "
        "без пометок зон нахлёста",
    )

    p_live = sub.add_parser(
        "live", help="живой режим: запись + потоковая расшифровка для ассистента"
    )
    p_live.add_argument("--out", default=None, help=OUT_HELP)
    p_live.add_argument(
        "--window", type=float, default=None, help="длина окна расшифровки, сек"
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
    p_as.add_argument("--out", default=None, help=OUT_HELP)
    p_as.add_argument(
        "--window", type=float, default=None, help="длина окна расшифровки, сек"
    )
    p_as.add_argument(
        "--hotwords", help="термины через запятую, подсказка распознаванию"
    )
    p_as.add_argument("--task", help="задача из заметок — контекст встречи")
    p_as.add_argument(
        "--vault",
        default=None,
        help="папка заметок (по умолчанию — из настроек или env MEET_VAULT)",
    )
    p_as.add_argument("--port", type=int, default=None, help="порт веб-страницы")
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

    sub.add_parser(
        "status",
        help="состояние резидента: идёт ли запись, что видит детектор звонка",
    )

    p_cmp = sub.add_parser(
        "compare", help="пословный диф спикеров между двумя транскриптами"
    )
    p_cmp.add_argument("a", help="транскрипт A (например, текущий пайплайн)")
    p_cmp.add_argument("b", help="транскрипт B (вариант для сравнения)")

    args = parser.parse_args()
    # Настройки — источник значений по умолчанию: флаг командной строки их
    # перекрывает, но не дублирует. Так одно и то же (папка записей, окно,
    # порт, лексика) настраивается в приложении и одинаково видно из CLI.
    cfg = settings.load()
    # Команды без --out (transcribe/enroll/compare) значение просто не используют.
    out_root = getattr(args, "out", None) or str(cfg.recording.recordings)
    if args.command == "record":
        from meet.recorder import record

        record(out_root)
    elif args.command == "live":
        from meet.gpu_lock import hold_gpu_lock
        from meet.live import run_live

        with hold_gpu_lock("live"):
            run_live(out_root,
                     window_seconds=args.window or cfg.assist.window_seconds,
                     hotwords=args.hotwords,
                     no_voices=args.no_voices or not cfg.assist.voices)
    elif args.command == "assist":
        from meet.assist.app import run_assist
        from meet.gpu_lock import hold_gpu_lock

        vault = args.vault or (str(cfg.assist.vault) if cfg.assist.vault else None)
        with hold_gpu_lock("assist"):
            run_assist(out_root,
                       window_seconds=args.window or cfg.assist.window_seconds,
                       hotwords=args.hotwords,
                       task=args.task, vault=vault,
                       port=args.port or cfg.assist.port,
                       no_voices=args.no_voices or not cfg.assist.voices)
    elif args.command == "status":
        print_status()
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
                align=cfg.asr.align if args.align is None else args.align,
                overlap=cfg.asr.overlap if args.overlap is None else args.overlap,
            )
