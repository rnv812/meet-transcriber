import re
import time
from datetime import datetime
from pathlib import Path

from meet import asr, events, hotwords, paths
from meet.progress import WEIGHTS, Stages, Step
from meet.asr import Segment, transcribe_wav
from meet.audio import to_wav16k
from meet.diarize import SKIPPED_NO_ACCESS, SKIPPED_NO_TOKEN, diarize_wav, split_by_speaker
from meet.interleave import interleave_tracks
from meet.jobs import temp_dir
from meet.output import speaker_names, to_markdown

# Бюджет подсказок в символах; почему именно столько — в meet.hotwords. Живёт
# там, чтобы счётчик в настройках резидента брал его, не импортируя этот модуль.
HOTWORDS_CHAR_BUDGET = hotwords.HOTWORDS_CHAR_BUDGET

# Без токена Hugging Face (или без доступа к гейтед-модели) диаризации нет, но
# расшифровка идёт: системная дорожка и импорт — один «Собеседник», микрофон —
# владелец машины. Транскрипт получает пометку (`diarization`: skipped_no_token
# / skipped_no_access), по которой окно говорит «без разделения на спикеров —
# настройте Hugging Face».
INTERLOCUTOR = "Собеседник"
_SKIPPED_NOTES = {
    SKIPPED_NO_TOKEN: "пропущено: нет токена Hugging Face",
    SKIPPED_NO_ACCESS: "пропущено: нет доступа к модели Hugging Face",
}


def _cap_hotwords(terms: list[str], budget: int = HOTWORDS_CHAR_BUDGET) -> list[str]:
    """Ограничить набор подсказок бюджетом символов, чтобы он не переполнял
    контекст Whisper. Приоритет — более свежим терминам (конец списка: разовые
    --hotwords и свежие строки внизу hotwords.txt); итоговый порядок исходный."""
    kept_reversed: list[str] = []
    used = 0
    for term in reversed(terms):
        extra = len(term) + (2 if kept_reversed else 0)  # ", " между терминами
        if used + extra > budget:
            break
        kept_reversed.append(term)
        used += extra
    if len(kept_reversed) < len(terms):
        print(
            f"hotwords: оставлено {len(kept_reversed)} из {len(terms)} терминов "
            f"(бюджет {budget} симв.), чтобы не переполнить контекст Whisper"
        )
    return list(reversed(kept_reversed))


def _hotword_terms(extra: str | None, path: Path | None = None) -> list[str]:
    """Все термины распознавания (hotwords.txt и разовые --hotwords) без
    бюджета: для возврата латиницы после GigaAM (meet.translit) лимит
    контекста Whisper не нужен."""
    path = path or paths.hotwords_path()
    terms: list[str] = []
    try:
        if path.exists():
            terms = hotwords.terms(path.read_text(encoding="utf-8"))
    except OSError:
        terms = []
    if extra:
        terms += [t.strip() for t in extra.split(",") if t.strip()]
    return list(dict.fromkeys(terms))


class _Run:
    """Состояние одной расшифровки: каким движком распознаём (решается один
    раз на встречу, по первой дорожке) и сколько секунд заняли ступени —
    распознавание, выравнивание, диаризация. Время уходит в журнал строкой
    «время ступеней: …» (и событием `log` в шину — его пишет резидент)."""

    def __init__(self, extra_hotwords: str | None = None, bus=None) -> None:
        self.bus = bus if bus is not None else events.EventBus()
        self.choice: asr.Choice | None = None
        # Модель Whisper вместо выбранной, когда GigaAM не загрузилась: уже
        # скачанная (asr.local_whisper_model), чтобы не качать гигабайты.
        self.whisper_model: str | None = None
        self.extra_hotwords = extra_hotwords
        self.seconds = {"asr": 0.0, "align": 0.0, "diarize": 0.0}
        self.started = time.monotonic()
        # Ход одной шкалой (meet.progress): план задают _transcribe_single/_two_track.
        self.stages: Stages | None = None

    def choose(self, wav: Path) -> asr.Choice:
        if self.choice is None:
            self.choice = asr.choose(wav)
            self._estimate(wav)
        return self.choice

    def _estimate(self, wav: Path) -> None:
        """Ожидаемая длительность всей расшифровки — по SPEED_FACTOR выбранного
        движка и длительности дорожки (WAV 16 кГц моно: 32 000 байт в секунду)."""
        if self.stages is None or self.choice is None:
            return
        try:
            seconds = max(0, wav.stat().st_size - 44) / 32000
            from meet import engine

            self.stages.estimate(seconds * engine.speed_factor(self.choice.device, self.choice.backend))
        except (OSError, ValueError, TypeError):
            pass

    def part(self, value: float) -> None:
        """Доля текущего шага (распознавание, диаризация) — в шкалу хода."""
        if self.stages is not None:
            self.stages.update(value)

    @property
    def gigaam(self) -> bool:
        return self.choice is not None and self.choice.backend == "gigaam"

    def timed(self, stage: str, call):
        start = time.monotonic()
        try:
            return call()
        finally:
            self.seconds[stage] += time.monotonic() - start

    def timing_line(self) -> str:
        choice = self.choice or asr.Choice()
        engine = "GigaAM" if choice.backend == "gigaam" else "Whisper"
        total = time.monotonic() - self.started
        s = self.seconds
        return (f"время ступеней ({engine}, {choice.device}): распознавание {s['asr']:.1f} с, "
                f"выравнивание {s['align']:.1f} с, диаризация {s['diarize']:.1f} с, "
                f"всего {total:.1f} с")


def _whisper(wav: Path, hotwords: str | None, run: "_Run") -> list[Segment]:
    if run.whisper_model:
        return run.timed("asr", lambda: transcribe_wav(wav, hotwords, model_name=run.whisper_model,
                                                       on_progress=run.part))
    return run.timed("asr", lambda: transcribe_wav(wav, hotwords, on_progress=run.part))


def _recognize(wav: Path, hotwords: str | None, run: "_Run") -> list[Segment]:
    """Распознать дорожку выбранным движком, засекая время. Whisper зовётся
    прежним образом (с подсказками), GigaAM — со своей нарезкой.

    GigaAM не скачалась или не загрузилась (нет сети, сервер недоступен,
    файл битый) — встреча не падает: её распознаёт Whisper, уже скачанной
    моделью, если такая есть, с пометкой GIGAAM_FAILED («GigaAM недоступна —
    использован Whisper») и причиной в журнале."""
    choice = run.choose(wav)
    if choice.backend != "gigaam":
        return _whisper(wav, hotwords, run)
    try:
        return run.timed("asr", lambda: transcribe_wav(wav, hotwords, choice=choice, on_progress=run.part))
    except Exception as e:
        return _fallback_to_whisper(wav, hotwords, run, choice.device, e)


def _reason(error: Exception) -> str:
    from meet import gigaam_asr

    return str(error) if isinstance(error, gigaam_asr.Unavailable) else f"{type(error).__name__}: {error}"


def _fallback_to_whisper(wav: Path, hotwords: str | None, run: "_Run", device: str,
                         error: Exception) -> list[Segment]:
    """GigaAM не вышла — Whisper. Уже скачанная модель (на CPU — самая
    лёгкая) — сразу. Нет ни одной — качаем выбранную, предупредив в ходе
    задачи («скачивается модель Whisper (около 1,5 ГБ)»); Hugging Face не
    отвечает — сразу одна понятная ошибка с обеими причинами, без попытки
    гигабайтной загрузки. Не скачалась — тоже одна ошибка с обеими причинами."""
    gigaam_reason = _reason(error)
    print(f"GigaAM недоступна ({gigaam_reason}) — распознаёт Whisper")
    run.choice = asr.Choice("faster-whisper", device, note=asr.GIGAAM_FAILED)
    try:
        run.whisper_model = asr.local_whisper_model(device)
    except Exception:
        run.whisper_model = None
    if run.whisper_model:
        return _whisper(wav, hotwords, run)
    name = asr.fallback_whisper_model(device)
    size = asr.model_size_text(name)
    note = f"GigaAM недоступна — скачивается модель Whisper{f' ({size})' if size else ''}"
    print(note)
    if run.stages is not None:
        run.stages.note(note)
    else:
        run.bus.progress("asr", note=note)
    advice = "Проверьте подключение или прокси в настройках"
    if not asr.hub_reachable():
        raise SystemExit(f"GigaAM недоступна ({gigaam_reason}), и модель Whisper не скачать: "
                         f"нет связи с Hugging Face. {advice}")
    run.whisper_model = name
    try:
        return _whisper(wav, hotwords, run)
    except Exception as w:
        raise SystemExit(f"GigaAM недоступна ({gigaam_reason}), и модель Whisper не скачалась "
                         f"({type(w).__name__}: {w}). {advice}") from w


def _restore_latin(segments: list[Segment], run: "_Run") -> None:
    """После GigaAM — латинские термины из кириллицы (meet.translit)."""
    try:
        from meet import translit

        n = translit.apply(segments, _hotword_terms(run.extra_hotwords))
        if n:
            print(f"термины латиницей: возвращено {n}")
    except Exception as e:  # термины не должны ронять расшифровку
        print(f"термины латиницей пропущены (ошибка: {e})")


def _align_enabled(align: bool, run: "_Run") -> bool:
    """Выравнивание wav2vec2 после GigaAM — только по `asr.align_after_gigaam`:
    у GigaAM свои пословные таймкоды, а выравнивание — минута на CPU."""
    if not align or not run.gigaam:
        return align
    from meet import settings

    try:
        return settings.load().asr.align_after_gigaam
    except Exception:
        return False


def _diarize(wav: Path, speakers, overlap: bool, run: "_Run"):
    return run.timed("diarize", lambda: diarize_wav(wav, num_speakers=speakers, exclusive=not overlap,
                                                    on_progress=run.part))


def _single_plan(align: bool) -> list[Step]:
    """Шаги расшифровки одной дорожки (импорт, файл)."""
    steps = [
        Step("convert", "convert", WEIGHTS["convert"]),
        Step("asr", "asr", WEIGHTS["asr"], measured=True),
        Step("align", "align", WEIGHTS["align"]),
        Step("diarize", "diarize", WEIGHTS["diarize"], measured=True),
        Step("voices", "voices", WEIGHTS["voices"]),
        Step("render", "render", WEIGHTS["render"]),
    ]
    return steps if align else [s for s in steps if s.key != "align"]


def _two_track_plan(align: bool) -> list[Step]:
    """Шаги встречи из двух дорожек: собеседники распознаются и делятся на
    спикеров, микрофон только распознаётся (на нём один человек); речи в нём
    обычно меньше — и вес меньше."""
    asr_w = WEIGHTS["asr"]
    steps = [
        Step("convert", "convert", WEIGHTS["convert"]),
        Step("asr-sys", "asr", asr_w * 0.6, label="распознавание собеседников", note="sys", measured=True),
        Step("align", "align", WEIGHTS["align"], note="sys"),
        Step("diarize", "diarize", WEIGHTS["diarize"], note="sys", measured=True),
        Step("voices", "voices", WEIGHTS["voices"]),
        Step("asr-mic", "asr", asr_w * 0.4, label="распознавание микрофона", note="mic", measured=True),
        Step("render", "render", WEIGHTS["render"]),
    ]
    return steps if align else [s for s in steps if s.key != "align"]


def _load_hotwords(extra: str | None, path: Path | None = None) -> str | None:
    """Подсказка лексики для распознавания: накопительный список из hotwords.txt
    (по термину на строку, # — комментарий) плюс разовые термины из --hotwords.

    Путь по умолчанию берётся из paths.hotwords_path() (корень репозитория в
    dev-режиме, data_dir в установленном) — раньше он был относительным и потому
    зависел от рабочей папки процесса.

    Возвращает термины через запятую (как ждёт faster-whisper) или None.
    """
    path = path or paths.hotwords_path()
    terms: list[str] = []
    if path.exists():
        terms = hotwords.terms(path.read_text(encoding="utf-8"))
    if extra:
        terms += [t.strip() for t in extra.split(",") if t.strip()]
    seen = list(dict.fromkeys(terms))  # дедуп с сохранением порядка
    kept = _cap_hotwords(seen)
    return ", ".join(kept) if kept else None


# Форматы дорожек в порядке предпочтения: сейчас пишем .opus, но старые записи
# в .wav должны продолжать транскрибироваться.
_TRACK_EXTS = (".opus", ".wav", ".ogg", ".flac", ".mp3", ".m4a",
               ".mp4", ".webm", ".mkv")


def _find_track(folder: Path, stem: str) -> Path | None:
    """Файл дорожки (sys/mic) в папке записи, независимо от формата."""
    for ext in _TRACK_EXTS:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def _maybe_align(segments: list[Segment], wav: Path, enabled: bool) -> list[Segment]:
    """При enabled — уточнить пословные таймкоды forced alignment'ом (точнее стыки
    спикеров). Ошибка выравнивания не должна ронять транскрибацию: откатываемся на
    исходные таймкоды whisper."""
    if not enabled:
        return segments
    try:
        from meet.align import align_segments

        return align_segments(segments, wav)
    except Exception as e:
        print(f"forced alignment пропущен (ошибка: {e}); беру таймкоды whisper")
        return segments


def _replacement_rules() -> list[dict]:
    from meet import settings

    try:
        return list(settings.load().asr.replacements)
    except Exception:
        return []


def _fix_terms(segments: list[Segment], run: "_Run | None" = None) -> list[Segment]:
    """Правила замены из настроек (`asr.replacements`, «Исправлять так же в
    будущих встречах») — сразу после распознавания и выравнивания: слова
    исправляются вместе с текстом, раздача реплик спикерам их уже видит.
    После GigaAM за ними — возврат латинских терминов (meet.translit): правило
    человека («апи» → «API-шлюз») важнее автоматической замены («апи» → «API»).
    Сбой правил не роняет расшифровку."""
    _apply_rules(segments)
    if run is not None and run.gigaam:
        _restore_latin(segments, run)
    return segments


def _apply_rules(segments: list[Segment]) -> list[Segment]:
    rules = _replacement_rules()
    if not rules:
        return segments
    try:
        from meet import textfix

        skipped: list[float] = []
        n = textfix.apply_rules(segments, rules, skipped)
    except Exception as e:
        print(f"правила замены пропущены (ошибка: {e})")
        return segments
    if n:
        print(f"правила замены: исправлено {n}")
    if skipped:
        print(f"правила замены: у {len(skipped)} сегм. время слов не выровнено — сняты, "
              "реплика делится по сегменту целиком")
    return segments


def voice_threshold(folder: Path | None = None) -> float:
    """Порог узнавания голоса: свой у встречи (панель «Спикеры», meta.json
    `voice_threshold`), иначе общий из настроек."""
    from meet import settings

    try:
        value = settings.load().asr.voice_threshold
    except Exception:
        value = settings.VOICE_THRESHOLD
    if folder is not None and Path(folder).is_dir():
        from meet import library

        own = library.read_meta(Path(folder)).get("voice_threshold")
        if isinstance(own, (int, float)) and not isinstance(own, bool):
            low, high = settings.VOICE_THRESHOLD_RANGE
            value = min(high, max(low, float(own)))
    return value


def _match_names(diar, threshold: float | None = None) -> dict[str, str]:
    """Уверенные имена из базы голосов voices/ для меток диаризации.

    Пустая/отсутствующая база и любые ошибки матчинга не роняют
    транскрибацию (паттерн как у forced alignment). Нет диаризации (нет
    токена или доступа) — нечего и сопоставлять."""
    if diar is None or diar.skipped or not diar.embeddings:
        return {}
    try:
        import meet.voices as voices

        base = voices.load_voices()
        if not base:
            return {}
        if threshold is None:
            return voices.match_speakers(diar.embeddings, base)
        return voices.match_speakers(diar.embeddings, base, threshold=threshold)
    except Exception as e:
        print(f"голоса: матчинг пропущен (ошибка: {e})")
        return {}


def _apply_names(
    turns: list[tuple[float, float, str]], name_map: dict[str, str]
) -> list[tuple[float, float, str]]:
    if not name_map:
        return turns
    return [(start, end, name_map.get(label, label)) for start, end, label in turns]


def _folder_dates(name: str) -> tuple[str, str]:
    """Дата из имени папки записи (recorder именует папки YYYY-MM-DD_...).

    → (ISO YYYY-MM-DD, ДД.ММ.ГГГГ); если префикс не распознан — текущая дата.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}", f"{d}.{mo}.{y}"
    dt = datetime.now()
    return f"{dt:%Y-%m-%d}", f"{dt:%d.%m.%Y}"


def _file_dates(p: Path) -> tuple[str, str]:
    dt = datetime.fromtimestamp(p.stat().st_mtime)
    return f"{dt:%Y-%m-%d}", f"{dt:%d.%m.%Y}"


def transcribe(
    path_str: str,
    speakers: int | None = None,
    hotwords: str | None = None,
    align: bool = True,
    overlap: bool = True,
    bus=None,
) -> Path:
    """Расшифровать папку записи или отдельный файл.

    `bus` — шина событий: ступени пайплайна уходят в неё как `progress`, чтобы
    UI показывал, на чём стоим. Без шины поведение прежнее (печать в stdout).
    """
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"Не найдено: {path}")

    bus = bus if bus is not None else events.EventBus()
    run = _Run(hotwords, bus)
    hotwords = _load_hotwords(hotwords)

    if path.is_dir() and _find_track(path, "source") and not _find_track(path, "sys"):
        # Папка импорта: одна дорожка чужой записи, но вывод — внутрь папки,
        # как у обычной записи, чтобы библиотека и редактор её видели.
        segments, diar, name_map = _transcribe_single(
            _find_track(path, "source"), speakers, hotwords, align, overlap, bus, run=run
        )
        iso, dmy = _folder_dates(path.name)
        out_md = path / f"{iso}_transcript.md"
        title = f"Встреча — {dmy}"
    elif path.is_dir():
        segments, diar, name_map = _transcribe_two_track(
            path, speakers, hotwords, align, overlap, bus, run=run
        )
        iso, dmy = _folder_dates(path.name)
        out_md = path / f"{iso}_transcript.md"
        title = f"Встреча — {dmy}"
    else:
        segments, diar, name_map = _transcribe_single(
            path, speakers, hotwords, align, overlap, bus, run=run
        )
        iso, dmy = _file_dates(path)
        out_md = path.with_suffix(".md")
        title = f"{path.stem} — {dmy}"

    if path.is_dir():
        # Объединённая встреча: на стыках частей — отметки перерыва.
        from meet import library, merge

        segments = merge.with_breaks(segments, library.read_meta(path).get("parts"))
    stages = run.stages or Stages(bus, _single_plan(False))
    stages.begin("render")
    out_md.write_text(to_markdown(title, segments, iso), encoding="utf-8")
    _write_sidecar(out_md, path, iso, segments, diar, name_map)
    _write_structured(path, segments, title, name_map,
                      diarization=diar.skipped if diar is not None else None,
                      choice=run.choice)
    timing = run.timing_line()
    print(timing)
    bus.emit(events.LOG, text=timing, source="timing")
    print(f"Готово: {out_md}")
    stages.finish(note=str(out_md))
    return out_md


def _write_structured(path: Path, segments, title: str, name_map: dict,
                      diarization: str | None = None, choice=None) -> None:
    """`transcript.json` рядом с записью — структурный источник для редактора.

    Markdown остаётся человеческим артефактом и форматом экспорта, но править
    в редакторе реплики и имена по разметке — гадание; здесь те же данные, что
    видит человек, но в виде данных. Пишем только для папки записи: у одиночного
    файла нет своей папки, и класть json рядом с чужим видео некрасиво.

    `diarization` — пометка о пропущенной диаризации (`skipped_no_token`,
    `skipped_no_access`); с диаризацией поля нет вовсе, как и раньше.

    `choice` (asr.Choice) — чем распознано: поле `asr` ({"backend", "device",
    "model"}) и, если движок не тот, что выбран, `asr_note` (например,
    `not_russian` — запись не на русском, GigaAM заменён Whisper)."""
    if not path.is_dir():
        return
    from meet import library

    # Как в Markdown и сайдкаре: «Спикер N», а не сырая SPEAKER_XX. Иначе
    # переименование из окна (ключи «Спикер N») не находит реплик, а статистика
    # людей не видит их речи.
    labels = speaker_names(segments)
    segments = [
        Segment(s.start, s.end, s.text, labels.get(s.speaker, s.speaker),
                words=list(getattr(s, "words", None) or []),
                uncertain=getattr(s, "uncertain", False), kind=getattr(s, "kind", None),
                track=getattr(s, "track", None))
        for s in segments
    ]
    raw = library.segments_to_raw(
        segments, speakers=name_map, title=title, source=str(path)
    )
    if diarization:
        raw["diarization"] = diarization
    if choice is not None:
        raw["asr"] = {"backend": choice.backend, "device": choice.device,
                      **({"model": choice.gigaam_model} if choice.backend == "gigaam" else {})}
        if choice.note:
            raw["asr_note"] = choice.note
    if _find_track(path, "sys") and _find_track(path, "mic"):
        # Микрофонные сегменты помечены пайплайном (`track`): выводить их по
        # подписям, как у старых расшифровок (meet.segvoices), не нужно.
        raw["track_marks"] = "pipeline"
    try:
        library.write_transcript(path, raw, words="replace")
    except OSError as e:  # транскрипт уже написан — это не повод падать
        print(f"structured: не записал transcript.json ({e})")


def _write_sidecar(out_md, path, iso, segments, diar, name_map) -> None:
    """Сайдкар с эмбеддингами спикеров — сырьё для meet enroll.

    display повторяет имена транскрипта: уверенно распознанные — по базе,
    остальные — «Спикер N» той же нумерацией, что в выводе."""
    if not (diar and diar.embeddings):
        if diar and diar.turns:
            # только ASCII-пунктуация: cp866-консоль не кодирует тире (см. voices.py)
            print("голоса: pyannote не вернул эмбеддинги, матчинг и сайдкар пропущены")
        return
    from meet.voices import write_sidecar

    names = speaker_names(segments)
    speakers = [
        {
            "label": label,
            # спикер с эмбеддингом, но без расшифрованных сегментов, остаётся под
            # сырой меткой «SPEAKER_XX» — enroll принимает и сырые метки тоже
            "display": name_map.get(label) or names.get(label, label),
            "embedding": [float(x) for x in emb],
        }
        for label, emb in diar.embeddings.items()
    ]
    # Абсолютный путь: образец голоса помнит, откуда взят, и относительный
    # (запуск `meet transcribe recordings/x`) зависел бы от рабочей папки.
    p = write_sidecar(out_md, source=str(Path(path).resolve()), date=iso, speakers=speakers)
    print(f"Голосовые отпечатки: {p}")


def _transcribe_single(
    src: Path,
    speakers: int | None,
    hotwords: str | None,
    align: bool = True,
    overlap: bool = True,
    bus=None,
    run: "_Run | None" = None,
):
    bus = bus if bus is not None else events.EventBus()
    run = run if run is not None else _Run(bus=bus)
    stages = run.stages = Stages(bus, _single_plan(align))
    with temp_dir() as td:
        stages.begin("convert")
        wav = to_wav16k(src, Path(td) / "audio16.wav")
        stages.update(1)
        stages.begin("asr")
        segments = _recognize(wav, hotwords, run)
        stages.update(1)
        align = _align_enabled(align, run)
        if align:
            stages.begin("align")
        else:
            stages.drop("align")
        segments = _fix_terms(run.timed("align", lambda: _maybe_align(segments, wav, align)), run)
        stages.begin("diarize")
        diar = _diarize(wav, speakers, overlap, run)
        if diar.skipped:
            stages.drop("voices")
            stages.note(_SKIPPED_NOTES.get(diar.skipped))
            for seg in segments:
                seg.speaker = INTERLOCUTOR
            return segments, diar, {}
        stages.begin("voices")
        name_map = _match_names(diar, voice_threshold(src.parent if src.stem == "source" else None))
        segments = split_by_speaker(
            segments, _apply_names(diar.turns, name_map), diar.overlaps
        )
    return segments, diar, name_map


def _transcribe_two_track(
    folder: Path,
    speakers: int | None,
    hotwords: str | None,
    align: bool = True,
    overlap: bool = True,
    bus=None,
    run: "_Run | None" = None,
):
    bus = bus if bus is not None else events.EventBus()
    run = run if run is not None else _Run(bus=bus)
    sys_src, mic_src = _find_track(folder, "sys"), _find_track(folder, "mic")
    if not (sys_src and mic_src):
        raise SystemExit(f"В {folder} нет дорожек sys/mic")
    stages = run.stages = Stages(bus, _two_track_plan(align))
    with temp_dir() as td:
        stages.begin("convert")
        sys_wav = to_wav16k(sys_src, Path(td) / "sys16.wav", normalize=True)
        stages.update(0.5)
        mic_wav = to_wav16k(mic_src, Path(td) / "mic16.wav")
        stages.update(1)
        stages.begin("asr-sys")
        sys_segs = _recognize(sys_wav, hotwords, run)
        stages.update(1)
        # forced alignment только для sys: mic — один спикер («Вы»), стыки не важны
        align = _align_enabled(align, run)
        if align:
            stages.begin("align")
        else:
            stages.drop("align")
        sys_segs = _fix_terms(run.timed("align", lambda: _maybe_align(sys_segs, sys_wav, align)), run)
        stages.begin("diarize")
        diar = _diarize(sys_wav, speakers, overlap, run)
        if diar.skipped:
            stages.drop("voices")
            stages.note(_SKIPPED_NOTES.get(diar.skipped))
            name_map = {}
            for seg in sys_segs:
                seg.speaker = INTERLOCUTOR
        else:
            stages.begin("voices")
            name_map = _match_names(diar, voice_threshold(folder))
            sys_segs = split_by_speaker(
                sys_segs, _apply_names(diar.turns, name_map), diar.overlaps
            )
        stages.begin("asr-mic")
        mic_segs = _fix_terms(_recognize(mic_wav, hotwords, run), run)
        stages.update(1)
        # Микрофонная дорожка — всегда владелец машины; как его подписывать,
        # решает настройка (по умолчанию «Вы»).
        from meet import settings

        speaker = settings.load().recording.speaker_name
        for seg in mic_segs:
            seg.speaker = speaker
            seg.track = "mic"
    return interleave_tracks(sys_segs, mic_segs), diar, name_map
