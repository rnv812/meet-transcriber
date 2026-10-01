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
    """Токен HF из настроек — в переменную среды, если её ещё нет.

    Все загрузчики HF (faster-whisper, pyannote, transformers) читают его из
    окружения; в настройках он есть, но подпроцессу не наследуется. Явный env
    приоритетнее — не перетираем."""
    import os

    if os.environ.get("HF_TOKEN"):
        return
    try:
        from meet import models

        token = models.token()
        if token:
            os.environ["HF_TOKEN"] = token
            os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meet-job")
    parser.add_argument("kind",
                        choices=["transcribe", "import", "install-engine", "download-model",
                                 "summary", "ask"])
    parser.add_argument("path")
    parser.add_argument("--speakers", type=int)
    parser.add_argument("--hotwords")
    parser.add_argument("--no-align", dest="align", action="store_false", default=None)
    parser.add_argument("--no-overlap", dest="overlap", action="store_false",
                        default=None)
    parser.add_argument("--flavor", choices=["cuda", "cpu"])
    parser.add_argument("--question")
    args = parser.parse_args(argv)

    if args.kind in ("summary", "ask"):
        return _assistant(args.kind, args.path, args.question)

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


def _copy_import(folder_str: str) -> int:
    """Скопировать исходник импорта в папку записи как source.<ext>.

    Оригинал не трогаем: человек мог импортировать файл из общей папки."""
    import os
    import shutil
    from pathlib import Path

    from meet import events, library

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    folder = Path(folder_str)
    src = Path(library.read_meta(folder).get("original_path") or "")
    if not src.is_file():
        _emit({"kind": "error", "text": f"исходный файл пропал: {src}"})
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
        _emit({"kind": "error", "text": f"не удалось скопировать файл: {e}"})
        return 3
    bus.progress("copy", label="копирование файла", done=1, total=1, note=src.name)
    return 0


def _install_engine(flavor: str | None) -> int:
    """Поставить движок расшифровки, отдавая вывод pip построчно.

    Ступени тут условные: шкалы у pip нет, зато видно, что именно качается.
    """
    from meet import engine, events

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    card = engine.state()
    chosen = flavor or card["flavor"]
    bus.progress("engine", label="установка движка",
                 note=f"{chosen}, ~{card['download_gb']:.0f} ГБ")
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
            out = assistant.summarize(folder, runner, knowledge, provider=provider)
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


def jobs_hint() -> str:
    from meet.jobs import ENGINE_HINT

    return ENGINE_HINT


if __name__ == "__main__":
    sys.exit(main())
