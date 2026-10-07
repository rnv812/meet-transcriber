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
import threading

# Строки пишут и основной поток, и опрос размера файлов (ByteWatch): одна
# строка JSON — одна запись под замком, иначе строки перемешаются.
_EMIT_LOCK = threading.Lock()


def _emit(payload: dict) -> None:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _EMIT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


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


def _diarize_log_to_resident() -> None:
    """Строки диаризации (время стадий, откат ускорения) — событием `log` с
    source="timing": такие очередь пишет в resident.log
    (jobs.RESIDENT_LOG_SOURCES), а голый print подпроцесса выбрасывает."""
    try:
        from meet import diarize
    except ImportError:
        return
    diarize.set_log_sink(lambda text: _emit({"kind": "log", "text": text, "source": "timing"}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meet-job")
    parser.add_argument("kind",
                        choices=["transcribe", "import", "install-engine", "download-model",
                                 "summary", "ask", "merge", "speaker_split", "rediarize",
                                 "analyze", "improve", "owner_voice", "chat"])
    parser.add_argument("path")
    parser.add_argument("--speakers", type=int)
    parser.add_argument("--hotwords")
    parser.add_argument("--no-align", dest="align", action="store_false", default=None)
    parser.add_argument("--no-overlap", dest="overlap", action="store_false",
                        default=None)
    parser.add_argument("--flavor", choices=["cuda", "cpu"])
    parser.add_argument("--question")
    parser.add_argument("--message")
    parser.add_argument("--label")
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--sensitivity", type=float)
    parser.add_argument("--wav")
    parser.add_argument("--device")
    parser.add_argument("--derive", action="store_true")
    parser.add_argument("--recordings")
    # Модель, выбранная человеком для одного действия (итоги, вопрос, анализ,
    # улучшение); нет — модель по умолчанию из настроек.
    parser.add_argument("--provider")
    args = parser.parse_args(argv)
    from meet import tempdirs

    # Свой корень временных файлов (и у ffmpeg, Claude Code, Codex — детей
    # задачи): задачу убьют отменой или выходом резидента — куски звука встречи
    # уйдут одной папкой (резидент удаляет её по pid).
    with tempdirs.own_root():
        return _dispatch(args)


def _dispatch(args) -> int:
    import os

    # Телеметрия pyannote — выключена до любого его импорта (как
    # meet.diarize.quiet_pyannote, но без импорта numpy в задачах без torch).
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    if args.kind in ("summary", "ask"):
        return _assistant(args.kind, args.path, args.question, args.provider or None)
    if args.kind == "chat":
        return _chat(args.path, args.message or "", args.provider or None)
    if args.kind == "analyze":
        return _analyze(args.path, args.provider or None)
    if args.kind == "improve":
        return _improve(args.path, args.provider or None)
    if args.kind == "owner_voice" and args.derive:
        return _owner_derive(args.path, args.recordings or "")
    if args.kind == "owner_voice":
        return _owner_voice(args.path, args.wav or "", args.device or None)

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
    _diarize_log_to_resident()

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
    try:
        size = src.stat().st_size
    except OSError:
        size = 0
    total = size or 1
    bus.progress("copy", label="копирование файла", done=0, total=total, note=src.name, unit="bytes")
    # Во временный .part и переименование в конце: оборванная копия (отмена
    # убивает процесс) не должна остаться валидной дорожкой source.<ext>.
    final = folder / f"source{src.suffix.lower()}"
    part = folder / (final.name + ".part")
    try:
        _copy_with_progress(src, part, lambda done: bus.progress(
            "copy", label="копирование файла", done=min(done, total), total=total, note=src.name,
            unit="bytes"))
        shutil.copystat(src, part)
        os.replace(part, final)
    except Exception as e:
        part.unlink(missing_ok=True)
        emit({"kind": "error", "text": f"не удалось скопировать файл: {e}"})
        return 3
    bus.progress("copy", label="копирование файла", done=total, total=total, note=src.name, unit="bytes")
    return 0


COPY_CHUNK = 4 * 1024 * 1024
COPY_GAP_S = 0.25  # не больше 4 событий хода в секунду


def _copy_with_progress(src, dst, report, clock=None) -> None:
    """Копировать файл кусками, сообщая скопированные байты (`report(done)`)
    не чаще раза в COPY_GAP_S: большой импорт не стоит на «0 %» минутами."""
    import time

    clock = clock or time.monotonic
    done = 0
    last = clock()
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(COPY_CHUNK)
            if not chunk:
                break
            fout.write(chunk)
            done += len(chunk)
            now = clock()
            if now - last >= COPY_GAP_S:
                last = now
                report(done)


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
    _diarize_log_to_resident()
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


def _owner_voice(voices_str: str, wav_str: str, device: str | None) -> int:
    """Образец голоса владельца из записи мастера или настроек
    (meet.owner_enroll). Негодная запись — понятным текстом, что сделать
    иначе. Запись удаляется в любом случае: хранится только отпечаток."""
    from pathlib import Path

    from meet import events, owner_enroll

    wav = Path(wav_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    try:
        # Чекпойнт модели закрыт условиями HF: токен — в окружение загрузчика.
        _apply_hf_token()
        sample = owner_enroll.enroll(wav, device=device, voices=Path(voices_str), bus=bus)
    except owner_enroll.QualityError as e:
        _emit({"kind": "error", "text": str(e)})
        return 3
    except ImportError as e:
        _emit({"kind": "error", "text": f"{jobs_hint()}: {e}"})
        return 2
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    finally:
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass  # папку записи удалит резидент после задачи
    _emit({"kind": "job.result", "path": sample.id})
    return 0


def _owner_derive(voices_str: str, recordings_str: str) -> int:
    """Поиск голоса владельца по прошлым встречам (meet.owner_derive). Итог —
    предложение или причина словами — пишется рядом с образцами; результат
    задачи — его статус. Ничего не применяется без «Да, это я»."""
    from pathlib import Path

    from meet import events, owner_derive

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    try:
        # Чекпойнт модели закрыт условиями HF: токен — в окружение загрузчика.
        _apply_hf_token()
        outcome = owner_derive.run(Path(recordings_str), Path(voices_str), bus=bus,
                                   log=lambda text: bus.emit("log", text=text))
    except ImportError as e:
        _emit({"kind": "error", "text": f"{jobs_hint()}: {e}"})
        return 2
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": outcome.status})
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


class ByteWatch:
    """Ход загрузки в байтах: загрузчики (huggingface_hub, GigaAM) своего хода
    не отдают, зато файлы растут на диске — их размер и опрашиваем. Событие —
    не чаще раза в `interval` секунд и только если размер изменился.

    `cap` — выше этого опрос не показывает: полная шкала значит «готово», а
    это известно, только когда загрузчик вернул управление (размер мог быть
    оценкой из каталога)."""

    def __init__(self, measure, total: int | None, report, interval: float = 0.5,
                 cap: int | None = None) -> None:
        import threading

        self.measure, self.total, self.report, self.interval = measure, total, report, interval
        self.cap = cap if cap is not None else total
        self._stop = threading.Event()
        self._last: int | None = None
        self._thread = threading.Thread(target=self._loop, name="meet-bytes", daemon=True)

    def tick(self) -> None:
        try:
            done = int(self.measure())
        except Exception:
            return
        if self.cap:
            done = min(done, self.cap)
        if done != self._last:
            self._last = done
            self.report(done, self.total)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()

    def __enter__(self):
        self.tick()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def _download_model(repo_id: str) -> int:
    """Скачать модель, отдавая ход: байты скачанного из размера файлов на
    диске (шкала в окне), строки загрузчика — в журнал задачи."""
    from meet import events, models

    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("model", label="загрузка модели", note=repo_id, step=1, steps=1)
    lines: list[str] = []

    def say(line: str) -> None:
        lines.append(line)
        _emit({"kind": "log", "text": line})

    def report(done: int, total: int | None) -> None:
        bus.progress("model", label="загрузка модели", note=repo_id, done=done if total else None,
                     total=total, step=1, steps=1,
                     fraction=round(done / total, 4) if total else None)

    # Шкала — прирост от того, что уже лежит на диске: «Обновить» скачанную
    # модель не показывает полную полоску с первой секунды. Докачивать нечего
    # (или размер — заниженная оценка) — «неизвестно», бегущий блик.
    start = models.size_on_disk(repo_id)
    full = models.download_total(repo_id)
    need = full - start if full and full > start else None
    with ByteWatch(lambda: max(0, models.size_on_disk(repo_id) - start), need, report,
                   cap=int(need * 0.99) if need else None):
        code = models.download(repo_id, on_line=say)
    if code != 0:
        _emit({"kind": "error", "text": lines[-1] if lines else "не скачалось"})
        return code
    if need:
        report(need, need)  # загрузчик вернул управление — теперь полная шкала честна
    _emit({"kind": "job.result", "path": repo_id})
    return 0


class _NoModel(Exception):
    """Модели для задачи нет: текст для человека."""


def _pick(cfg, chosen: str | None):
    """(провайдер, runner) задачи модели. `chosen` — модель, выбранная
    человеком для этого действия: только она — не включена или не найдена,
    задача падает с её ошибкой, модель по умолчанию не зовётся (U3). Без
    выбора — модель по умолчанию (`llm.resolve`). Нет никого — _NoModel."""
    from meet import assistant, llm

    try:
        if chosen:
            error = llm.choice_error(cfg, chosen)
            if error:
                raise _NoModel(error)
            provider, runner = llm.resolve(cfg, chosen)
        else:
            provider, runner = llm.resolve(cfg)
    except _NoModel:
        raise
    except Exception as e:
        raise _NoModel(f"{type(e).__name__}: {e}") from e
    if runner is None:
        raise _NoModel(assistant.NO_PROVIDER)
    return provider, runner


def _assistant(kind: str, folder_str: str, question: str | None, chosen: str | None = None) -> int:
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
        provider, runner = _pick(cfg, chosen)
    except _NoModel as e:
        _emit({"kind": "error", "text": str(e)})
        return 2
    origin = llm.describe(provider, cfg)
    folder = Path(folder_str)
    knowledge = cfg.assistant.knowledge_dir
    tracker, runner = _llm_tracker(bus, kind, "итоги встречи" if kind == "summary" else "ответ на вопрос",
                                   provider, runner, stage="llm")
    try:
        with tracker:
            if kind == "summary":
                out = assistant.summarize(folder, runner, knowledge, provider=provider,
                                          want_title=cfg.assistant.auto_title, origin=origin,
                                          context=_local_context(provider, cfg), bus=bus)
            else:
                if not (question or "").strip():
                    _emit({"kind": "error", "text": "пустой вопрос"})
                    return 3
                assistant.ask(folder, question.strip(), runner, knowledge, provider=provider,
                              origin=origin)
                out = folder / assistant.QA_JSONL
    except RuntimeError as e:
        _emit({"kind": "error", "text": str(e)})
        return 1
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    _emit({"kind": "job.result", "path": str(out)})
    return 0


# Заметка агенту в ход «Продолжить разговор»: встреча уже закончилась.
CHAT_AFTER_NOTE = ("Встреча уже закончилась: пользователь продолжает разговор с тобой после неё. "
                   "Новых реплик встречи не будет — отвечай на его сообщение.")
CHAT_TURNS_MAX = 6          # ходов на одно сообщение (запросы к Meet у локальной модели)
CHAT_PARTIAL_EVERY_S = 0.5  # кусок ответа — строкой chat.updated не чаще


def _chat(folder_str: str, message_id: str, chosen: str | None = None, *,
          runner=None, provider: str | None = None, conversation=None) -> int:
    """«Продолжить разговор» после встречи (V4, `jobs.CHAT`): ответ агента на
    сообщение пользователя `message_id`, которое резидент уже записал в журнал
    записи. Тот же агент-участник, что во время встречи (`participant`):
    продолжение его сеанса (`assistant/sessions.json`), не вышло или его нет —
    затравка из журнала и расшифровки встречи. Ответ — в журнал; строки
    `chat.updated` — окну по ходу. `runner`/`provider`/`conversation` —
    подмена модели в тестах."""
    import asyncio
    import time
    from pathlib import Path

    from meet import events, settings
    from meet.assist import participant as participant_mod
    from meet.assist.bus import TranscriptBus
    from meet.assist.chatlog import ChatLog

    folder = Path(folder_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("chat", label="ассистент отвечает")
    cfg = settings.load()
    if not message_id:
        _emit({"kind": "error", "text": "не сказано, на какое сообщение отвечать"})
        return 3
    chatlog = ChatLog(folder, log=lambda text: _emit({"kind": "log", "text": text}))
    if runner is None and conversation is None:
        try:
            provider, runner = _pick(cfg, chosen)
        except _NoModel as e:
            _chat_failed(chatlog, message_id, str(e))
            _emit({"kind": "error", "text": str(e)})
            return 2
        tracker, runner = _llm_tracker(bus, "chat", "ответ ассистента", provider, runner,
                                       stage="chat")
    else:
        tracker = _NullContext()
    tbus = TranscriptBus()
    for line, entry in _meeting_lines(folder):
        tbus.publish(line, entry)
    extra = {"conversation": conversation} if conversation is not None else {}
    agent = participant_mod.from_settings(cfg, tbus, folder, provider, runner, chatlog=chatlog,
                                          log=lambda text: _emit({"kind": "log", "text": text}),
                                          after_meeting=True, **extra)
    agent.skip_existing()   # расшифровка — контекст затравки, а не новые реплики
    last = {"partial": 0.0}

    def on_event(name: str, data) -> None:
        if name == "chat":
            _emit({"kind": "chat.updated"})
        elif name == "chat_partial":
            now = time.monotonic()
            if now - last["partial"] >= CHAT_PARTIAL_EVERY_S:
                last["partial"] = now
                _emit({"kind": "chat.updated", "partial": data})

    agent.add_listener(on_event)

    async def answer() -> str | None:
        try:
            await agent.start()
            await agent.queue_existing(message_id)
            agent.add_note(CHAT_AFTER_NOTE)
            turns = 0
            while turns < CHAT_TURNS_MAX and await agent.tick():
                turns += 1
            return agent.error if agent.state == participant_mod.ERROR else None
        finally:
            await agent.shutdown()

    try:
        with tracker:
            error = asyncio.run(answer())
    except ValueError as e:     # нет такого сообщения
        _emit({"kind": "error", "text": str(e)})
        return 3
    except Exception as e:
        _emit({"kind": "error", "text": f"{type(e).__name__}: {e}"})
        return 1
    if error:
        _emit({"kind": "error", "text": error})
        return 1
    _emit({"kind": "job.result", "path": str(chatlog.path)})
    return 0


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_failed(chatlog, message_id: str, error: str) -> None:
    """Ответа не будет (модель не подключена): видимая ошибка у сообщения."""
    try:
        chatlog.append("agent", mode="reply", re=message_id, text="", status="failed",
                       error=error[:300])
        _emit({"kind": "chat.updated"})
    except Exception:
        pass


def _meeting_lines(folder) -> list[tuple[str, dict]]:
    """Реплики встречи для затравки агента: точная расшифровка, а если её
    ещё нет — лента живого режима (`live_transcript.md`)."""
    import re

    from meet import library

    out: list[tuple[str, dict]] = []
    try:
        data = library.read_transcript(folder)
    except Exception:
        data = None
    for seg in (data or {}).get("segments") or []:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        t = seg.get("start") if isinstance(seg.get("start"), (int, float)) else 0.0
        speaker = str(seg.get("speaker") or "Спикер")
        out.append((f"{speaker}: {text}", {"t": float(t), "speaker": speaker, "text": text}))
    if out:
        return out
    try:
        feed = (folder / "live_transcript.md").read_text(encoding="utf-8")
    except OSError:
        return []
    pattern = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] ([^:]+): (.*)$")
    for line in feed.splitlines():
        m = pattern.match(line)
        if m:
            h, mi, sec, speaker, said = m.groups()
            out.append((line, {"t": float(int(h) * 3600 + int(mi) * 60 + int(sec)),
                               "speaker": speaker, "text": said}))
    return out


def _analyze(folder_str: str, chosen: str | None = None) -> int:
    """«Анализ встречи» (meet.analysis) — тем же провайдером, что итоги
    (`llm.resolve`, прокси из настроек, без инструментов). Ошибка остаётся в
    meta.json записи (`analysis_error`): окно покажет «Повторить»."""
    from pathlib import Path

    from meet import analysis, events, settings

    folder = Path(folder_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("analyze", label="анализ встречи")
    cfg = settings.load()

    def fail(text: str, code: int) -> int:
        analysis.mark_failed(folder, text, chosen)
        _emit({"kind": "error", "text": text})
        return code

    try:
        provider, runner = _pick(cfg, chosen)
    except _NoModel as e:
        return fail(str(e), 2)
    tracker, runner = _llm_tracker(bus, "analyze", "анализ встречи", provider, runner)
    try:
        with tracker:
            out = analysis.analyze(folder, runner, cfg, provider=provider, bus=bus)
    except (analysis.AnalysisError, RuntimeError) as e:
        return fail(str(e), 1)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 1)
    modes = ()
    if provider == "openai-compatible":
        from meet.llm import openai_compat

        modes = openai_compat.accepted_modes()
    # Итог в resident.log: чего модель не дала и что отброшено — видно и без analysis.json.
    _emit({"kind": "log", "source": "analysis", "text": analysis.outcome_line(analysis.read(folder), modes)})
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _improve(folder_str: str, chosen: str | None = None) -> int:
    """«Улучшить расшифровку» (meet.improve) — тем же провайдером, что итоги и
    анализ (`llm.resolve`, без инструментов). Ошибка — в meta.json записи
    (`improve_error`): окно покажет «Повторить»."""
    from pathlib import Path

    from meet import events, improve, settings

    folder = Path(folder_str)
    bus = events.EventBus()
    bus.subscribe(lambda event: _emit(event.to_dict()))
    bus.progress("improve", label="улучшение расшифровки")
    cfg = settings.load()

    def fail(text: str, code: int) -> int:
        improve.mark_failed(folder, text, chosen)
        _emit({"kind": "error", "text": text})
        return code

    try:
        provider, runner = _pick(cfg, chosen)
    except _NoModel as e:
        return fail(str(e), 2)
    tracker, runner = _llm_tracker(bus, "improve", "улучшение расшифровки", provider, runner)
    try:
        with tracker:
            out = improve.improve(folder, runner, cfg, provider=provider, bus=bus)
    except (improve.ImproveError, RuntimeError) as e:
        return fail(str(e), 1)
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}", 1)
    _emit({"kind": "job.result", "path": str(out)})
    return 0


def _local_context(provider: str | None, cfg) -> int | None:
    """Окно контекста локальной модели — итогам длинной встречи (по частям,
    если не влезает); у остальных провайдеров — None."""
    if provider != "openai-compatible":
        return None
    from meet.analysis import UNKNOWN_CONTEXT, local_context

    # Не узнать — как для 8K (как анализ и улучшение), а не один огромный вызов.
    return local_context(cfg) or UNKNOWN_CONTEXT


def _llm_tracker(bus, kind: str, label: str, provider: str | None, runner, stage: str | None = None):
    """Ход задачи модели (meet.llm_progress): трекер на шине и runner,
    сообщающий начало, поток текста и конец каждого вызова."""
    from meet import llm_progress

    tracker = llm_progress.Tracker(bus, kind, label, stage=stage, provider=provider)
    llm_progress.attach(bus, tracker)
    return tracker, tracker.wrap(runner)


def jobs_hint() -> str:
    from meet.jobs import ENGINE_HINT

    return ENGINE_HINT


if __name__ == "__main__":
    sys.exit(main())
