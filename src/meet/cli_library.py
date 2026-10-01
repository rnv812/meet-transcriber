"""Подкоманды CLI поверх библиотеки: всё, что умеет окно, — без окна.

`meet import / export / voices / summary / ask / notes` работают без резидента,
в текущем процессе, теми же функциями, что и окно (`library`, `people`,
`export`, `assistant`, `job_worker`). Ничего не спрашивают и не открывают:
опасное (удаление голоса) требует флага, а не вопроса.

Договор вывода: результат — в stdout (UTF-8 байтами: экспорт и JSON уходят в
файлы и в другие программы, cp1251 перенаправленного stdout портил бы их),
ход работы и ошибки — в stderr. Код выхода 0 — успех, 1 — ошибка данных или
человека (текст по-русски, без трейсбека), 2 — ошибка аргументов (argparse).
`--json` — ровно один JSON-документ в stdout.
"""

import errno
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from meet import library

COMMANDS = ("import", "export", "voices", "summary", "ask", "notes", "kb-export")
NO_PROVIDER_HINT = ("Подключите Claude Code или Codex: meet {command} … --provider codex "
                    "или настройка llm.provider")


class CliError(Exception):
    """Ошибка, понятная человеку: печатается одной строкой, код выхода 1."""


def run(args, cfg) -> int:
    handler = _VOICES[args.voices_command] if args.command == "voices" else _HANDLERS[args.command]
    try:
        handler(args, cfg)
    except CliError as e:
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # Ctrl+C посреди расшифровки — не трейсбек
        print("Прервано", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Читатель закрыл вывод (`meet export … | head`): он получил, что
        # хотел, — выходим тихо. stdout — в никуда, иначе его сброс при
        # выходе интерпретатора напечатал бы ту же ошибку.
        _silence_stdout()
        return 0
    return 0


# --- вывод -------------------------------------------------------------------


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass  # подменённый stdout (тесты) — сбрасывать нечего


def _out(text: str) -> None:
    """Текст в stdout как UTF-8. Интерактивная консоль Windows и так UTF-8,
    а перенаправленный stdout — cp1251: экспорт и JSON в нём теряли бы символы.
    Закрытый читателем пайп — BrokenPipeError (на Windows он приходит и как
    OSError EINVAL)."""
    try:
        sys.stdout.flush()
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
            return
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    except OSError as e:
        if isinstance(e, BrokenPipeError) or e.errno in (errno.EPIPE, errno.EINVAL):
            raise BrokenPipeError(errno.EPIPE, "читатель закрыл вывод") from None
        raise


def _result(args, doc, text: str) -> None:
    _out(json.dumps(doc, ensure_ascii=False, indent=1) + "\n" if args.json else text)


def _say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _progress_printer(errors: list[str]):
    """События пайплайна (`meet.events`) → строки человеку в stderr."""
    def emit(payload: dict) -> None:
        kind = payload.get("kind")
        if kind == "progress":
            line = str(payload.get("label") or payload.get("stage") or "")
            done, total = payload.get("done"), payload.get("total")
            if total:
                line += f" {done or 0:g}/{total:g}"
            if payload.get("note"):
                line += f" · {payload['note']}"
            _say(line)
        elif kind == "error":
            errors.append(str(payload.get("text") or ""))
        elif kind == "log":
            _say(str(payload.get("text") or ""))
    return emit


# --- записи ------------------------------------------------------------------


def _recording(arg: str, cfg) -> Path:
    """Папка записи по пути или по id (имени папки в папке записей)."""
    path = Path(arg)
    if path.is_dir():
        return path
    root = cfg.recording.recordings
    # id — только имя: «..» или путь в нём вывели бы за папку записей.
    if path.name == arg and arg not in (".", "..") and (root / arg).is_dir():
        return root / arg
    raise CliError(f"Нет такой записи: {arg} (нужна папка записи или её id в {root})")


def _transcript(folder: Path) -> dict:
    data = library.read_transcript(folder)
    if data is None:
        raise CliError(f"Транскрипта нет: {folder}. Сначала: meet transcribe \"{folder}\"")
    return data


def _import(args, cfg) -> None:
    from meet import job_worker

    src = Path(args.file)
    if not src.is_file():
        raise CliError(f"Файла нет: {src}")
    try:
        folder = library.create_import(cfg.recording.recordings, src)
    except ValueError as e:
        raise CliError(f"{src.name}: {e}")
    errors: list[str] = []
    if job_worker._copy_import(str(folder), emit=_progress_printer(errors)) != 0:
        # Папку только что создали мы, в ней один meta.json: пустая карточка
        # импорта в библиотеке была бы мусором, который потом удалять руками.
        shutil.rmtree(folder, ignore_errors=True)
        raise CliError(errors[-1] if errors else "копирование не удалось")
    transcript = None if args.no_transcribe else _transcribe(folder, args, cfg)
    # Пометка о пропущенной диаризации (`skipped_no_token`, `skipped_no_access`):
    # скрипту видно, что спикеры не разделены, без чтения transcript.json.
    data = library.read_transcript(folder) if transcript else None
    _result(args, {"folder": str(folder), "id": folder.name,
                   "transcribed": transcript is not None,
                   "transcript": str(transcript) if transcript else None,
                   "diarization": (data or {}).get("diarization")},
            f"{folder}\n")


def _transcribe(folder: Path, args, cfg) -> Path:
    """Расшифровка как у `meet transcribe` (в этом процессе, под GPU-маркером).
    Печать пайплайна уходит в stderr: в stdout — только результат команды."""
    from meet import events, job_worker
    from meet.gpu_lock import hold_gpu_lock

    job_worker._apply_hf_token()
    printer = _progress_printer([])
    bus = events.EventBus()
    bus.subscribe(lambda event: printer(event.to_dict()))
    retry = f"Файл уже в папке записи; повторить: meet transcribe \"{folder}\""
    try:
        from meet.transcribe import transcribe

        with redirect_stdout(sys.stderr), hold_gpu_lock("transcribe"):
            return transcribe(str(folder), speakers=args.speakers, hotwords=args.hotwords,
                              align=cfg.asr.align, overlap=cfg.asr.overlap, bus=bus)
    except ImportError as e:
        raise CliError(f"{job_worker.jobs_hint()}: {e}. {retry}")
    except (SystemExit, Exception) as e:
        reason = str(e) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"
        raise CliError(f"Расшифровка не удалась: {reason}. {retry}")


def _export(args, cfg) -> None:
    from meet import export

    folder = _recording(args.folder, cfg)
    # Как в окне: сырые SPEAKER_XX старых транскриптов — «Спикер N».
    data = library.with_display_names(_transcript(folder))
    title, date = library.title_and_date(folder, data)
    content = export.render({**data, "title": title}, args.format, date=date)
    if args.out_file is None:
        _result(args, {"format": args.format, "content": content}, content)
        return
    out = Path(args.out_file)
    try:
        # Байтами: тот же LF, что и в stdout, без перевода строк Windows.
        out.write_bytes(content.encode("utf-8"))
    except OSError as e:
        raise CliError(f"Не удалось записать {out}: {e}")
    _result(args, {"path": str(out)}, f"{out}\n")


# --- голоса ------------------------------------------------------------------


def _voices_list(args, cfg) -> None:
    from meet import people

    voices = cfg.recording.voices
    items = people.listing(voices, cfg.recording.recordings)
    if args.json or not items:
        _result(args, items, f"База голосов пуста: {voices}\n")
        return
    rows = [("имя", "встреч", "мин речи", "фото")] + [
        (p["name"], str(p["meetings"]), f"{p['seconds'] / 60:.1f}",
         "да" if p["has_avatar"] else "нет") for p in items]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    _out("".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() + "\n"
                 for row in rows))


def _person(name: str, voices: Path) -> str:
    """Имя существующего человека базы голосов, иначе — CliError."""
    from meet import people

    try:
        name = people.valid_name(name)
    except ValueError as e:
        raise CliError(f"{e}: {name}")
    if not (voices / f"{name}.json").exists():
        raise CliError(f"Нет такого человека: {name}")
    return name


def _people_call(call, *args) -> None:
    try:
        call(*args)
    except KeyError as e:
        raise CliError(f"Нет такого человека: {e.args[0]}")
    except FileExistsError as e:
        raise CliError(f"Человек с таким именем уже есть: {e.args[0]}")
    except ValueError as e:
        raise CliError(str(e))
    except OSError as e:
        raise CliError(f"Не удалось изменить базу голосов: {e}")


def _voices_rename(args, cfg) -> None:
    from meet import people

    _people_call(people.rename, args.old, args.new, cfg.recording.voices,
                 cfg.recording.recordings)
    _result(args, {"ok": True, "name": args.new.strip()},
            f"Переименовано: {args.old} → {args.new}\n")


def _voices_merge(args, cfg) -> None:
    from meet import people

    _people_call(people.merge, args.src, args.into, cfg.recording.voices,
                 cfg.recording.recordings)
    _result(args, {"ok": True, "name": args.into.strip()},
            f"Слито: {args.src} → {args.into}\n")


def _voices_delete(args, cfg) -> None:
    from meet import people

    if not args.yes:
        raise CliError("Удаление необратимо: добавьте --yes")
    name = _person(args.name, cfg.recording.voices)
    _people_call(people.delete, name, cfg.recording.voices)
    _result(args, {"ok": True, "name": name}, f"Удалено: {name}\n")


def _voices_avatar(args, cfg) -> None:
    from meet import people

    voices = cfg.recording.voices
    if bool(args.picture) == bool(args.clear):
        raise CliError("Укажите картинку или --clear (что-то одно)")
    name = _person(args.name, voices)
    if args.clear:
        _people_call(people.clear_avatar, name, voices)
        _result(args, {"ok": True, "name": name, "avatar": None}, f"Фото убрано: {name}\n")
        return
    picture = Path(args.picture)
    try:
        data = picture.read_bytes()
    except OSError:
        raise CliError(f"Файла нет: {picture}")
    try:
        path = people.set_avatar(name, data, voices)
    except ValueError as e:
        raise CliError(f"{picture}: {e}")
    except OSError as e:
        raise CliError(f"Не удалось сохранить фото: {e}")
    _result(args, {"ok": True, "name": name, "avatar": str(path)}, f"{path}\n")


# --- ассистент ---------------------------------------------------------------


def _model(args, cfg):
    """(провайдер, runner) — `--provider` перекрывает llm.provider настроек."""
    from meet import llm

    if args.provider:
        cfg = replace(cfg, llm=replace(cfg.llm, provider=args.provider))
    try:
        provider, runner = llm.resolve(cfg)
    except Exception as e:
        raise CliError(f"Не удалось выбрать модель: {type(e).__name__}: {e}")
    if runner is None:
        hint = NO_PROVIDER_HINT.format(command=args.command)
        raise CliError(f"Провайдер {args.provider} недоступен. {hint}" if args.provider else hint)
    _say(f"Модель думает ({provider})…")
    return provider, runner


def _summary(args, cfg) -> None:
    from meet import assistant

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    provider, runner = _model(args, cfg)
    try:
        path = assistant.summarize(folder, runner, cfg.assistant.knowledge_dir,
                                   provider=provider)
        markdown = path.read_text(encoding="utf-8")
    except (RuntimeError, OSError) as e:
        raise CliError(f"Итоги не получились: {e}")
    _result(args, {"path": str(path), "provider": provider, "markdown": markdown}, markdown)


def _ask(args, cfg) -> None:
    from meet import assistant

    question = args.question.strip()
    if not question:
        raise CliError("пустой вопрос")
    folder = _recording(args.folder, cfg)
    _transcript(folder)
    provider, runner = _model(args, cfg)
    try:
        item = assistant.ask(folder, question, runner, cfg.assistant.knowledge_dir,
                             provider=provider)
    except (RuntimeError, OSError) as e:
        raise CliError(f"Ответа нет: {e}")
    _result(args, item, item["a"] + "\n")


def _kb_export(args, cfg) -> None:
    """`meet kb-export` (и прежнее `meet notes`): встреча — в базу знаний."""
    from meet import kb_export

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    try:
        result = kb_export.export_recording(folder, cfg)
    except ValueError as e:
        hint = "" if cfg.export.meetings_dir else " (настройка export.meetings_dir)"
        raise CliError(f"{e}{hint}")
    except (RuntimeError, OSError) as e:
        raise CliError(f"Не удалось выгрузить встречу: {e}")
    _result(args, result, f"{result['path']}\n")


_HANDLERS = {"import": _import, "export": _export, "summary": _summary,
             "ask": _ask, "notes": _kb_export, "kb-export": _kb_export}
_VOICES = {"list": _voices_list, "rename": _voices_rename, "merge": _voices_merge,
           "delete": _voices_delete, "avatar": _voices_avatar}
