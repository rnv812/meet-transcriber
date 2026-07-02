import re
import tempfile
from datetime import datetime
from pathlib import Path

from meet.asr import Segment, transcribe_wav
from meet.audio import to_wav16k
from meet.diarize import diarize_wav, split_by_speaker
from meet.output import speaker_names, to_markdown


HOTWORDS_FILE = Path("hotwords.txt")

# IMPORTANT: контекст Whisper — 448 токенов, и faster-whisper НЕЗАВИСИМО усекает
# до 223 токенов и hotwords, и предыдущий текст (condition_on_previous_text);
# вместе они переполняют окно (223+223+служебные > 448) и роняют декодирование
# («maximum decoding length must be > 0»). Поэтому держим hotwords заведомо ниже.
# Бюджет в символах: при замеренной плотности лексики ~2.4 симв./токен это ~160
# токенов, и даже при пессимистичных 2.0 симв./токен ≈198 — итог с предыдущим
# текстом остаётся < 448. Список можно пополнять и дальше: лишнее отсекается.
HOTWORDS_CHAR_BUDGET = 400


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


def _load_hotwords(extra: str | None, path: Path = HOTWORDS_FILE) -> str | None:
    """Подсказка лексики для распознавания: накопительный список из hotwords.txt
    (по термину на строку, # — комментарий) плюс разовые термины из --hotwords.

    Возвращает термины через запятую (как ждёт faster-whisper) или None.
    """
    terms: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.split("#", 1)[0].strip()
            if term:
                terms.append(term)
    if extra:
        terms += [t.strip() for t in extra.split(",") if t.strip()]
    seen = list(dict.fromkeys(terms))  # дедуп с сохранением порядка
    kept = _cap_hotwords(seen)
    return ", ".join(kept) if kept else None


# Форматы дорожек в порядке предпочтения: сейчас пишем .opus, но старые записи
# в .wav должны продолжать транскрибироваться.
_TRACK_EXTS = (".opus", ".wav", ".ogg", ".flac", ".mp3", ".m4a")


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


def _match_names(diar) -> dict[str, str]:
    """Уверенные имена из базы голосов voices/ для меток диаризации.

    Пустая/отсутствующая база и любые ошибки матчинга не роняют
    транскрибацию (паттерн как у forced alignment)."""
    if not diar.embeddings:
        return {}
    try:
        import meet.voices as voices

        base = voices.load_voices()
        if not base:
            return {}
        return voices.match_speakers(diar.embeddings, base)
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
) -> Path:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"Не найдено: {path}")

    hotwords = _load_hotwords(hotwords)

    if path.is_dir():
        segments, diar, name_map = _transcribe_two_track(path, speakers, hotwords, align)
        iso, dmy = _folder_dates(path.name)
        out_md = path / f"{iso}_transcript.md"
        title = f"Встреча — {dmy}"
    else:
        segments, diar, name_map = _transcribe_single(path, speakers, hotwords, align)
        iso, dmy = _file_dates(path)
        out_md = path.with_suffix(".md")
        title = f"{path.stem} — {dmy}"

    out_md.write_text(to_markdown(title, segments, iso), encoding="utf-8")
    _write_sidecar(out_md, path, iso, segments, diar, name_map)
    print(f"Готово: {out_md}")
    return out_md


def _write_sidecar(out_md, path, iso, segments, diar, name_map) -> None:
    """Сайдкар с эмбеддингами спикеров — сырьё для meet enroll.

    display повторяет имена транскрипта: уверенно распознанные — по базе,
    остальные — «Спикер N» той же нумерацией, что в выводе."""
    if not (diar and diar.embeddings):
        return
    from meet.voices import write_sidecar

    names = speaker_names(segments)
    speakers = [
        {
            "label": label,
            "display": name_map.get(label) or names.get(label, label),
            "embedding": [float(x) for x in emb],
        }
        for label, emb in diar.embeddings.items()
    ]
    p = write_sidecar(out_md, source=str(path), date=iso, speakers=speakers)
    print(f"Голосовые отпечатки: {p}")


def _transcribe_single(
    src: Path, speakers: int | None, hotwords: str | None, align: bool = True
):
    with tempfile.TemporaryDirectory() as td:
        wav = to_wav16k(src, Path(td) / "audio16.wav")
        segments = transcribe_wav(wav, hotwords)
        segments = _maybe_align(segments, wav, align)
        diar = diarize_wav(wav, num_speakers=speakers)
        name_map = _match_names(diar)
        segments = split_by_speaker(segments, _apply_names(diar.turns, name_map))
    return segments, diar, name_map


def _transcribe_two_track(
    folder: Path, speakers: int | None, hotwords: str | None, align: bool = True
):
    sys_src, mic_src = _find_track(folder, "sys"), _find_track(folder, "mic")
    if not (sys_src and mic_src):
        raise SystemExit(f"В {folder} нет дорожек sys/mic")
    with tempfile.TemporaryDirectory() as td:
        sys_wav = to_wav16k(sys_src, Path(td) / "sys16.wav", normalize=True)
        mic_wav = to_wav16k(mic_src, Path(td) / "mic16.wav")
        sys_segs = transcribe_wav(sys_wav, hotwords)
        # forced alignment только для sys: mic — один спикер («Вы»), стыки не важны
        sys_segs = _maybe_align(sys_segs, sys_wav, align)
        diar = diarize_wav(sys_wav, num_speakers=speakers)
        name_map = _match_names(diar)
        sys_segs = split_by_speaker(sys_segs, _apply_names(diar.turns, name_map))
        mic_segs = transcribe_wav(mic_wav, hotwords)
        for seg in mic_segs:
            seg.speaker = "Вы"
    return sorted(sys_segs + mic_segs, key=lambda s: s.start), diar, name_map
