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

COMMANDS = ("import", "export", "voices", "summary", "ask", "notes", "kb-export", "merge", "fix",
            "analyze", "title")
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
                                   provider=provider, want_title=cfg.assistant.auto_title)
        markdown = path.read_text(encoding="utf-8")
    except (RuntimeError, OSError) as e:
        raise CliError(f"Итоги не получились: {e}")
    title = _apply_ai_title(folder, (library.read_meta(folder).get("summary_title") or {}).get("title"),
                            cfg)
    _result(args, {"path": str(path), "provider": provider, "markdown": markdown, "title": title},
            markdown)


def _apply_ai_title(folder: Path, title, cfg) -> str | None:
    """Название от модели без приложения — по тем же правилам (meet.titles):
    только при включённом «Придумывать название» и не вместо названия
    человека. Папку в базе знаний переименовывает следующая выгрузка."""
    from meet import titles

    if not title:
        return None
    try:
        applied = titles.apply_ai(folder, title, cfg)
    except OSError:
        return None
    if applied:
        _say(f"Название встречи: {applied}")
    return applied


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
    в окне (отменяется там же); --hotword — исправление в термины распознавания,
    --rule — правило замены для будущих расшифровок (asr.replacements)."""
    from meet import replacements

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    root = cfg.recording.recordings
    if _resident_root(folder, root):
        # Приложение запущено: правка — через него (его замок правок, отказ,
        # пока запись обрабатывают, обновление поиска и выгрузки в базу знаний).
        got = _fix_via_resident(folder.name, args)
    else:
        reason = _merge_busy(folder, root)
        if reason:
            raise CliError(f"{folder.name}: {reason}")
        got = _fix_here(folder, args, cfg)
    wrong, right = replacements.clean_text(args.wrong), replacements.clean_text(args.right)
    term, rule = got["hotword"], got["rule"]
    doc = {"folder": str(folder), **got}
    lines = [f"Исправлено: {got['changed']} из {got['found']} ({wrong} → {right})"]
    if not args.all and got["found"] > 1:
        lines.append("Остальные совпадения — с флагом --all")
    if got["changed"]:
        lines.append("Отменить — в карточке встречи: «Спикеры» → «История изменений»")
        if not got["via_app"]:
            lines.append("Выгрузку в базу знаний обновит `meet kb-export` (или приложение при следующей правке)")
    if term:
        lines.append(term.get("error") or (f"Добавлено в термины распознавания: {term['term']}" if term["added"]
                                           else f"Уже в терминах распознавания: {term['term']}"))
    if rule:
        lines.append(rule.get("error") or f"Правило для будущих расшифровок: {rule['from']} → {rule['to']}")
    _result(args, doc, "\n".join(lines) + "\n")


def _resident_root(folder: Path, root: Path) -> bool:
    """Запущено ли приложение, и запись — в его папке записей (там её id)."""
    from meet import control

    try:
        same = os.path.normcase(str(folder.resolve().parent)) == os.path.normcase(str(root.resolve()))
    except OSError:
        return False
    return same and control.alive()


def _fix_via_resident(rid: str, args) -> dict:
    from urllib.parse import quote

    from meet import control

    def call(path: str, payload: dict) -> dict:
        try:
            reply = control.request(f"/recordings/{quote(rid)}{path}", method="POST", payload=payload, timeout=30)
        except RuntimeError as e:
            text = str(e).split(": ", 1)[-1] if "ответил" in str(e) else str(e)
            raise CliError(text[:1].upper() + text[1:])
        if isinstance(reply.get("error"), str):
            raise CliError(reply["error"][:1].upper() + reply["error"][1:])
        return reply

    found = call("/text/preview", {"find": args.wrong, "whole_word": True, "limit": 1})
    if not found.get("count"):
        raise CliError(f"Во встрече нет «{args.wrong.strip()}»")
    first = found["samples"][0]
    reply = call("/text/apply", {
        "find": args.wrong, "replace": args.right, "scope": "all" if args.all else "one",
        "segment": first["segment"], "offset": first["offset"], "whole_word": True,
        "add_hotword": bool(args.hotword), "add_rule": bool(args.rule)})
    return {"found": found["count"], "changed": reply.get("changed", 0),
            "step": (reply.get("step") or {}).get("id"), "hotword": reply.get("hotword"),
            "rule": reply.get("rule"), "via_app": True}


def _fix_here(folder: Path, args, cfg) -> dict:
    """Приложение не запущено: правка в этом процессе (тот же шаг истории)."""
    from meet import hotwords, paths, replacements, settings, speakers, textfix

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
            if not (args.hotword or args.rule):
                raise
    except speakers.SpeakerError as e:
        raise CliError(str(e)[:1].upper() + str(e)[1:])
    except OSError as e:
        raise CliError(f"Не удалось сохранить расшифровку: {e}")
    wrong, right = textfix.clean_text(args.wrong), textfix.clean_text(args.right)
    term = (hotwords.add_to_file(paths.hotwords_path(), replacements.hotword_for(wrong, right))
            if args.hotword else None)
    rule = None
    if args.rule and wrong != right:
        current = settings.load().asr.replacements
        key = replacements.words_of(wrong)
        replaced = next((dict(r) for r in current if replacements.words_of(r["from"]) == key), None)
        settings.patch({"asr": {"replacements": replacements.with_rule(current, wrong, right)}})
        rule = {"from": wrong, "to": right, "replaced": replaced}
    return {"found": found["count"], "changed": changed, "step": step, "hotword": term, "rule": rule,
            "via_app": False}


# --- анализ встречи и название ------------------------------------------------

ANALYZE_POLL_S = 2.0
ANALYZE_WAIT_S = 1800.0


def _analysis_text(doc: dict | None) -> str:
    """Коротко о разметке для человека."""
    if not doc:
        return "Анализа нет\n"
    lines = []
    if doc.get("title"):
        lines.append(f"Название: {doc['title']}")
    category = doc.get("category")
    if isinstance(category, dict):
        lines.append(f"Категория: {category.get('id')} ({category.get('confidence')})")
    if "chapters" in doc:
        lines.append(f"Главы: {len(doc['chapters'])}")
        lines += [f"  #{c['start_i']}–#{c['end_i']} {c['title']}" for c in doc["chapters"]]
    if "insights" in doc:
        lines.append(f"Наблюдения: {len(doc['insights'])}")
        lines += [f"  [{i['kind']}] {i['text']}" for i in doc["insights"]]
    if "phrase_types" in doc:
        lines.append(f"Размечено реплик по типу: {len(doc['phrase_types'])}")
    if "importance" in doc:
        lines.append(f"Оценено реплик по важности: {len(doc['importance'])}")
    return "\n".join(lines) + "\n"


def _analyze(args, cfg) -> None:
    """`meet analyze`: разметить встречу (analysis.json). Приложение запущено —
    задачей через него (одна очередь модели, отказ во время записи, название
    и событие окну); иначе — в этом процессе."""
    from meet import analysis

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    if _resident_root(folder, cfg.recording.recordings):
        doc = _analyze_via_resident(folder.name)
        via_app = True
    else:
        provider, runner = _model(args, cfg)
        try:
            analysis.analyze(folder, runner, cfg, provider=provider,
                             bus=_cli_bus())
        except (analysis.AnalysisError, RuntimeError) as e:
            analysis.mark_failed(folder, str(e))
            raise CliError(f"Анализ не получился: {e}")
        except OSError as e:
            raise CliError(f"Не удалось сохранить анализ: {e}")
        doc = analysis.read(folder)
        _apply_ai_title(folder, (doc or {}).get("title"), cfg)
        via_app = False
    _result(args, {"folder": str(folder), "path": str(folder / analysis.ANALYSIS_JSON),
                   "via_app": via_app, "analysis": doc}, _analysis_text(doc))


def _cli_bus():
    from meet import events

    bus = events.EventBus()
    printer = _progress_printer([])
    bus.subscribe(lambda event: printer(event.to_dict()))
    return bus


def _resident_call(rid: str, path: str, method: str = "GET", payload: dict | None = None,
                   timeout: float = 30) -> dict:
    from urllib.parse import quote

    from meet import control

    try:
        reply = control.request(f"/recordings/{quote(rid)}{path}", method=method, payload=payload,
                                timeout=timeout)
    except RuntimeError as e:
        text = str(e).split(": ", 1)[-1] if "ответил" in str(e) else str(e)
        raise CliError(text[:1].upper() + text[1:])
    if set(reply) == {"error"} and isinstance(reply.get("error"), str):
        raise CliError(reply["error"][:1].upper() + reply["error"][1:])
    return reply


def _analyze_via_resident(rid: str, *, sleep=None, clock=None) -> dict | None:
    import time

    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    job = _resident_call(rid, "/analysis", "POST", {})
    _say(f"Анализ встречи поставлен в очередь приложения ({job.get('id')})")
    deadline = clock() + ANALYZE_WAIT_S
    while True:
        got = _resident_call(rid, "/analysis")
        state = got.get("state")
        if state not in ("queued", "running"):
            break
        if clock() > deadline:
            raise CliError("Анализ идёт дольше получаса — результат появится в приложении")
        sleep(ANALYZE_POLL_S)
    if state == "failed":
        raise CliError(f"Анализ не получился: {got.get('error') or 'без подробностей'}")
    return got.get("analysis")


def _title(args, cfg) -> None:
    """`meet title`: предложить название встречи (из свежего анализа или
    коротким вызовом модели); `--apply` — поставить его (происхождение "ai")."""
    from meet import titles

    folder = _recording(args.folder, cfg)
    _transcript(folder)
    via_app = _resident_root(folder, cfg.recording.recordings)
    if via_app:
        got = _resident_call(folder.name, "/title/suggest", "POST", {}, timeout=180)
    else:
        from meet import analysis

        try:
            if analysis.fresh_title(folder):
                got = titles.suggest(folder)
            else:
                got = titles.suggest(folder, _model(args, cfg)[1])
        except RuntimeError as e:
            raise CliError(f"Название не предложено: {e}")
    title = got.get("title")
    applied = False
    if args.apply and title:
        if via_app:
            _resident_call(folder.name, "", "PATCH", {"title": title, "title_source": "ai"})
        else:
            titles.write_title(folder, title, "ai")
        applied = True
    doc = {"folder": str(folder), "title": title, "from": got.get("from"), "applied": applied,
           "via_app": via_app}
    text = f"{title}\n"
    if applied:
        text += "Название поставлено\n" + ("" if via_app else
                                            "Папку в базе знаний переименует `meet kb-export`\n")
    _result(args, doc, text)


_HANDLERS = {"import": _import, "export": _export, "summary": _summary,
             "ask": _ask, "notes": _kb_export, "kb-export": _kb_export, "merge": _merge, "fix": _fix,
             "analyze": _analyze, "title": _title}
_VOICES = {"list": _voices_list, "rename": _voices_rename, "merge": _voices_merge,
           "delete": _voices_delete, "avatar": _voices_avatar}
