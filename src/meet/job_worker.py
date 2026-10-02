"""Подпроцесс одной задачи: считает и печатает прогресс построчным JSON.

Запускается очередью резидента (`meet.jobs`), не человеком. Всё, что процесс
говорит наружу, — это строки JSON в stdout: события пайплайна как есть
(`meet.events`) плюс `job.result` с путём результата в конце.

Отдельный процесс, потому что здесь грузятся torch, ctranslate2 и pyannote:
их падение не должно ронять резидента с идущей записью, а видеопамять
освобождается завершением процесса.
"""

import argparse
import json
import sys


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _apply_hf_token() -> None:
    """Токен HF (meet.credentials: диспетчер учётных данных) — в переменную
    среды, если её ещё нет.

    Загрузчики HF (faster-whisper, transformers) читают его из окружения, а
    там его нет: он в диспетчере. Явный env приоритетнее — не перетираем."""
    import os

    if os.environ.get("HF_TOKEN"):
        return
    try:
        from meet import credentials

        token = credentials.get_hf_token()
        if token:
            os.environ["HF_TOKEN"] = token
            os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meet-job")
    parser.add_argument("kind",
                        choices=["transcribe", "import", "install-engine", "download-model",
                                 "summary", "ask", "merge", "speaker_split", "rediarize",
                                 "analyze", "improve"])
    parser.add_argument("path")
    parser.add_argument("--speakers", type=int)
    parser.add_argument("--hotwords")
    parser.add_argument("--no-align", dest="align", action="store_false", default=None)
    parser.add_argument("--no-overlap", dest="overlap", action="store_false",
                        default=None)
    parser.add_argument("--flavor", choices=["cuda", "cpu"])
    parser.add_argument("--question")
    parser.add_argument("--label")
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--sensitivity", type=float)
    args = parser.parse_args(argv)

    if args.kind in ("summary", "ask"):
        return _assistant(args.kind, args.path, args.question)
    if args.kind == "analyze":
        return _analyze(args.path)
    if args.kind == "improve":
        return _improve(args.path)

    if args.kind == "merge":
        return _merge(args.path)
    if args.kind == "speaker_split":
        return _speaker_split(args.path, args.label or "")
    if args.kind == "rediarize":
        return _rediarize(args.path, args)

    if args.kind == "install-engine":
        return _install_engine(args.flavor)
    if args.kind == "download-model":
        return _download_model(args.path)

    if args.kind == "import":
        code = _copy_import(args.path)
        if code != 0:
            return code

    from meet import events, settings

    # Токен HF в окружение до любой ступени: диаризация (pyannote) читает его
    # только из env, а он у нас в настройках. Ставим один раз на весь
    # подпроцесс, чтобы и распознавание, и диаризация его видели.
    _apply_hf_token()

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    cfg = settings.load()

    try:
        from meet.gpu_lock import hold_gpu_lock
        from meet.transcribe import transcribe
    except ImportError as e:
        # Движка нет — это штатное состояние машины, на которой только пишут.
        _emit({"kind": "error", "text": f"{jobs_hint()}: {e}"})
        return 2

    try:
        with hold_gpu_lock("transcribe"):
            out = transcribe(
                args.path,
                speakers=args.speakers,
                hotwords=args.hotwords,
                align=cfg.asr.align if args.align is None else args.align,
                overlap=cfg.asr.overlap if args.overlap is None else args.overlap,
                bus=bus,
            )
    except SystemExit as e:  # пайплайн говорит «не найдено», «нет дорожек» и т.п.
        _emit({"kind": "error", "text": str(e)})
        return 3
    except ImportError as e:
        _emit({"kind": "error", "text": f"{jobs_hint()}: {e}"})
        return 2
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _copy_import(folder_str: str, emit=None) -> int:
    """Скопировать исходник импорта в папку записи как source.<ext>.

    Оригинал не трогаем: человек мог импортировать файл из общей папки.
    `emit` — куда отдавать события (по умолчанию JSON в stdout для очереди;
    `meet import` печатает их человеку в stderr)."""
    import os
    import shutil
    from pathlib import Path

    from meet import events, library

    emit = emit or _emit
    bus = events.EventBus()
    bus.subscribe(lambda event: emit(event.to_dict()))
    folder = Path(folder_str)
    src = Path(library.read_meta(folder).get("original_path") or "")
    if not src.is_file():
        emit({"kind": "error", "text": f"исходный файл пропал: {src}"})
        return 3
    bus.progress("copy", label="копирование файла", done=0, total=1, note=src.name)
    # Во временный .part и переименование в конце: оборванная копия (отмена
    # убивает процесс) не должна остаться валидной дорожкой source.<ext>.
    final = folder / f"source{src.suffix.lower()}"
    part = folder / (final.name + ".part")
    try:
        shutil.copy2(src, part)
        os.replace(part, final)
    except Exception as e:
        part.unlink(missing_ok=True)
        emit({"kind": "error", "text": f"не удалось скопировать файл: {e}"})
        return 3
    bus.progress("copy", label="копирование файла", done=1, total=1, note=src.name)
    return 0


def _merge(folder_str: str) -> int:
    """Собрать звук объединённой встречи (meet.merge). Расшифровку следом
    ставит резидент — обычной задачей, со своим прогрессом и отменой."""
    from pathlib import Path

    from meet import events, merge

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    try:
        out = merge.run(Path(folder_str), bus=bus)
    except merge.MergeError as e:
        _emit({"kind": "error", "text": str(e)})
        return 3
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _speaker_voices(work, reason: str) -> int:
    """Общая обвязка задач правки спикеров: токен HF, маркер GPU, ошибки —
    понятным текстом."""
    from meet import events

    _apply_hf_token()
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    try:
        from meet.gpu_lock import hold_gpu_lock

        with hold_gpu_lock(reason):
            out = work(bus)
    except ImportError as e:
        _emit({"kind": "error", "text": f"{jobs_hint()}: {e}"})
        return 2
    except (SystemExit, RuntimeError, ValueError) as e:
        _emit({"kind": "error", "text": str(e)})
        return 3
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _speaker_split(folder_str: str, label: str) -> int:
    """Голоса реплик спикера для «Разделить спикера» (meet.segvoices)."""
    from pathlib import Path

    def work(bus):
        from meet import library, segvoices

        folder = Path(folder_str)
        data = library.read_transcript(folder) or {}
        segments = [s for s in data.get("segments") or [] if isinstance(s, dict)]
        idx = [i for i, s in enumerate(segments) if s.get("speaker") == label and s.get("kind") != "break"]
        if not idx:
            raise ValueError(f"в записи нет спикера «{label}»")
        segvoices.compute(folder, idx, bus=bus)
        return folder / segvoices.CACHE_NAME

    return _speaker_voices(work, "speaker_split")


def _rediarize(folder_str: str, args) -> int:
    """Повторная диаризация без распознавания (meet.rediarize): результат —
    предпросмотр рядом с записью, применяет его резидент."""
    from pathlib import Path

    def work(bus):
        from meet import rediarize

        return rediarize.run(Path(folder_str), num_speakers=args.num_speakers,
                             min_speakers=args.min_speakers, max_speakers=args.max_speakers,
                             sensitivity=args.sensitivity, bus=bus)

    return _speaker_voices(work, "rediarize")


def _install_engine(flavor: str | None) -> int:
    """Поставить движок расшифровки, отдавая вывод pip построчно.

    Ступени тут условные: шкалы у pip нет, зато видно, что именно качается.
    """
    from meet import engine, events

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    card = engine.state()
    chosen = flavor or card["flavor"]
    # Объём — выбранной сборки (CPU можно выбрать и при видеокарте), с
    # десятыми через запятую: «~4,5 ГБ», а не округлённое до «~4».
    gb = engine.DOWNLOAD_HINT_GB.get(chosen, card["download_gb"])
    gb_text = f"{gb:.1f}".replace(".", ",").removesuffix(",0")
    bus.progress("engine", label="установка движка", note=f"{chosen}, ~{gb_text} ГБ")
    code = engine.install(chosen, on_line=lambda line: _emit({"kind": "log", "text": line}))
    if code != 0:
        _emit({"kind": "error", "text": f"установка движка не удалась (код {code})"})
        return code
    after = engine.state()
    if not after["installed"]:
        _emit({"kind": "error",
               "text": "после установки не хватает: " + ", ".join(after["missing"])})
        return 1
    _emit({"kind": "job.result", "path": after["target"]})
    return 0


def _download_model(repo_id: str) -> int:
    """Скачать модель в общий кэш Hugging Face, отдавая ход построчно."""
    from meet import events, models

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("model", label="загрузка модели", note=repo_id)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        _emit({"kind": "log", "text": line})

    code = models.download(repo_id, on_line=say)
    if code != 0:
        _emit({"kind": "error", "text": lines[-1] if lines else "не скачалось"})
        return code
    _emit({"kind": "job.result", "path": repo_id})
    return 0


def _assistant(kind: str, folder_str: str, question: str | None) -> int:
    """Итоги или ответ на вопрос по записи (meet.assistant).

    Провайдер выбирается здесь, а не в резиденте: `llm.resolve` проверяет вход
    в CLI (секунды), а SDK провайдера резиденту не нужен вовсе."""
    from pathlib import Path

    from meet import assistant, events, llm, settings

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("llm", label="модель думает")
    cfg = settings.load()
    try:
        provider, runner = llm.resolve(cfg)
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 2
    if runner is None:
        _emit({"kind": "error", "text": assistant.NO_PROVIDER})
        return 2
    folder = Path(folder_str)
    knowledge = cfg.assistant.knowledge_dir
    try:
        if kind == "summary":
            out = assistant.summarize(folder, runner, knowledge, provider=provider,
                                      want_title=cfg.assistant.auto_title)
        else:
            if not (question or "").strip():
                _emit({"kind": "error", "text": "пустой вопрос"})
                return 3
            assistant.ask(folder, question.strip(), runner, knowledge, provider=provider)
            out = folder / assistant.QA_JSONL
    except RuntimeError as e:
        _emit({"kind": "error", "text": str(e)})
        return 1
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _analyze(folder_str: str) -> int:
    """«Анализ встречи» (meet.analysis) — тем же провайдером, что итоги
    (`llm.resolve`, прокси из настроек, без инструментов). Ошибка остаётся в
    meta.json записи (`analysis_error`): окно покажет «Повторить»."""
    from pathlib import Path

    from meet import analysis, assistant, events, llm, settings

    folder = Path(folder_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("analyze", label="анализ встречи")
    cfg = settings.load()

    def fail(text: str, code: int) -> int:
        analysis.mark_failed(folder, text)
        _emit({"kind": "error", "text": text})
        return code

    try:
        provider, runner = llm.resolve(cfg)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 2)
    if runner is None:
        return fail(assistant.NO_PROVIDER, 2)
    try:
        out = analysis.analyze(folder, runner, cfg, provider=provider, bus=bus)
    except (analysis.AnalysisError, RuntimeError) as e:
        return fail(str(e), 1)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 1)
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _improve(folder_str: str) -> int:
    """«Улучшить расшифровку» (meet.improve) — тем же провайдером, что итоги и
    анализ (`llm.resolve`, без инструментов). Ошибка — в meta.json записи
    (`improve_error`): окно покажет «Повторить»."""
    from pathlib import Path

    from meet import assistant, events, improve, llm, settings

    folder = Path(folder_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("improve", label="улучшение расшифровки")
    cfg = settings.load()

    def fail(text: str, code: int) -> int:
        improve.mark_failed(folder, text)
        _emit({"kind": "error", "text": text})
        return code

    try:
        provider, runner = llm.resolve(cfg)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 2)
    if runner is None:
        return fail(assistant.NO_PROVIDER, 2)
    try:
        out = improve.improve(folder, runner, cfg, provider=provider, bus=bus)
    except (improve.ImproveError, RuntimeError) as e:
        return fail(str(e), 1)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 1)
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def jobs_hint() -> str:
    from meet.jobs import ENGINE_HINT

    return ENGINE_HINT


if __name__ == "__main__":
    sys.exit(main())
