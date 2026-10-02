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

COMMANDS = ("import", "export", "voices", "summary", "ask", "notes", "kb-export", "merge", "fix")
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


def _recording_now(root: Path) -> Path | None:
    """Папка, в которую сейчас пишет запись (по lock-файлу живого процесса)."""
    from meet.recorder import LOCK_NAME, _pid_alive

    try:
        data = json.loads((root / LOCK_NAME).read_text(encoding="utf-8"))
        pid, folder = int(data["pid"]), data.get("folder")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return Path(folder) if folder and _pid_alive(pid) else None


_FOLDER_JOBS = ("transcribe", "import", "merge", "summary", "ask")


def _resident_jobs() -> list[dict]:
    """Ждущие и идущие задачи резидента; резидента нет — пусто."""
    from meet import control

    try:
        items = control.request("/jobs").get("items") or []
    except Exception:
        return []
    return [j for j in items if isinstance(j, dict) and j.get("state") in ("queued", "running")]


def _merge_busy(folder: Path, root: Path, merging: Path | None = None) -> str | None:
    """Почему запись нельзя объединить или удалить: её пишут, над ней работает
    резидент или она — часть другого незавершённого объединения."""
    from meet import merge

    key = os.path.normcase(str(folder.resolve()))
    recording = _recording_now(root)
    if recording is not None and os.path.normcase(str(recording.resolve())) == key:
        return "запись ещё идёт"
    for job in _resident_jobs():
        try:
            same = os.path.normcase(str(Path(str(job.get("folder"))).resolve())) == key
        except OSError:
            same = False
        if same and job.get("kind") in _FOLDER_JOBS:
            return "над записью сейчас работает приложение — дождитесь"
    owner = merge.unfinished_owner(folder, merging)
    if owner:
        return (f"запись входит в объединение «{owner}», которое ещё не завершено. "
                "Дождитесь расшифровки объединённой записи или удалите её")
    return None


def _merge(args, cfg) -> None:
    """`meet merge`: то же, что «Объединить» в окне, но в этом процессе:
    собрать звук, расшифровать, выгрузить в базу знаний (если части уже там
    были) и удалить исходные, если не `--keep`. Расшифровка не удалась —
    объединённая запись остаётся, исходные тоже."""
    from types import SimpleNamespace

    from meet import events, merge

    folders = [_recording(a, cfg) for a in args.folders]
    parents = {os.path.normcase(str(f.resolve().parent)) for f in folders}
    if len(parents) > 1:
        raise CliError("Объединять можно только записи из одной папки записей")
    root = folders[0].resolve().parent
    for folder in folders:
        reason = _merge_busy(folder, root)
        if reason:
            raise CliError(f"{folder.name}: {reason}")
    try:
        target = merge.create(root, folders, keep_originals=args.keep)
    except merge.MergeError as e:
        raise CliError(str(e))
    errors: list[str] = []
    printer = _progress_printer(errors)
    bus = events.EventBus()
    bus.subscribe(lambda event: printer(event.to_dict()))
    try:
        merge.run(target, bus=bus)
    except merge.MergeError as e:
        shutil.rmtree(target, ignore_errors=True)  # пустая карточка была бы мусором
        raise CliError(f"Записи не объединены: {e}")
    try:
        transcript = _transcribe(target, SimpleNamespace(speakers=None, hotwords=None), cfg)
    except CliError as e:
        raise CliError(f"Записи объединены: {target}. {e} Исходные записи сохранены.")
    info = library.read_meta(target).get("merge") or {}
    kb_left = list(info.get("kb_exported") or [])
    if kb_left and cfg.export.meetings_dir:
        from meet import kb_export

        try:
            kb_export.export_recording(target, cfg)
        except Exception as e:
            _say(f"В базу знаний не выгружено: {e}")
    deleted: list[str] = []
    failed: list[str] = []
    if not args.keep and merge.state(target) == "merged":
        sources = merge.originals(target)
        # Пока мы расшифровывали, резидент мог взяться за исходные (или их
        # начали писать) — тогда не удаляем ни одной: все на месте лучше,
        # чем половина.
        busy = [(s.name, r) for s in sources if (r := _merge_busy(s, root, merging=target))]
        if busy:
            failed = [f"{name}: {reason}" for name, reason in busy]
        else:
            # Все или ни одной (папку может держать агент из окна приложения).
            try:
                library.remove_folders(sources)
                deleted = [source.name for source in sources]
            except (OSError, RuntimeError) as e:
                failed = [f"{source}: {e}" for source in sources]
    # Объединение завершено в любом случае: оставшиеся исходные — обычные
    # записи, а не «части незавершённого объединения», которые нельзя ни
    # удалить, ни объединить снова. Что не удалилось и почему — в meta.json.
    merge.mark_done(target, deleted, "; ".join(failed) or None)
    if kb_left:
        _say("Прежние папки частей в базе знаний не тронуты: " + ", ".join(kb_left))
    _result(args, {"folder": str(target), "id": target.name,
                   "merged_from": library.read_meta(target).get("merged_from"),
                   "transcript": str(transcript), "deleted": deleted, "kb_left": kb_left,
                   "not_deleted": failed},
            f"{target}\n")
    if failed:
        raise CliError("Записи объединены, но не все исходные удалены: " + "; ".join(failed))


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


def _fix(args, cfg) -> None:
    """`meet fix`: исправить распознанное слово или фразу во встрече — первое
    совпадение (или все с --all) одним шагом истории встречи, как «Исправить…»
    в окне (отменяется там же); --hotword — исправление в термины распознавания."""
    from meet import hotwords, paths, speakers, textfix

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    try:
        found = textfix.preview(folder, args.wrong, limit=1)
        if not found["count"]:
            raise CliError(f"Во встрече нет «{textfix.clean_text(args.wrong)}»")
        first = found["samples"][0]
        changed, step = 0, None
        try:
            got = textfix.apply(folder, args.wrong, args.right, "all" if args.all else "one",
                                cfg.recording.voices, segment=first["segment"], offset=first["offset"])
            changed, step = got["changed"], got["step"]["id"]
        except textfix.Unchanged:
            if not args.hotword:
                raise
    except speakers.SpeakerError as e:
        raise CliError(str(e)[:1].upper() + str(e)[1:])
    except OSError as e:
        raise CliError(f"Не удалось сохранить расшифровку: {e}")
    right = textfix.clean_text(args.right)
    term = hotwords.add_to_file(paths.hotwords_path(), right) if args.hotword else None
    doc = {"folder": str(folder), "found": found["count"], "changed": changed, "step": step,
           "hotword": term}
    lines = [f"Исправлено: {changed} из {found['count']} ({textfix.clean_text(args.wrong)} → {right})"]
    if not args.all and found["count"] > 1:
        lines.append("Остальные совпадения — с флагом --all")
    if changed:
        lines.append("Отменить — в карточке встречи: «Спикеры» → «История изменений»")
    if term:
        lines.append(term.get("error") or (f"Добавлено в термины распознавания: {right}" if term["added"]
                                           else f"Уже в терминах распознавания: {right}"))
    _result(args, doc, "\n".join(lines) + "\n")


_HANDLERS = {"import": _import, "export": _export, "summary": _summary,
             "ask": _ask, "notes": _kb_export, "kb-export": _kb_export, "merge": _merge, "fix": _fix}
_VOICES = {"list": _voices_list, "rename": _voices_rename, "merge": _voices_merge,
           "delete": _voices_delete, "avatar": _voices_avatar}
