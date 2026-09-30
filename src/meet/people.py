"""Люди базы голосов для окна приложения: список со статистикой, образец для
прослушивания, аватары и правка базы.

Голоса хранит voices.py (`<voices>/<имя>.json`); здесь — всё, что про человека,
а не про эмбеддинги. Аватар — `<voices>/<имя>.png`, квадрат 256×256.
Статистика не хранится, а считается по transcript.json всех записей
библиотеки — по репликам, где спикер назван именем человека (узнан ли он сам
или назван вручную, неважно): файлы — источник истины, и копия папки с другой
машины не разъезжается с «базой». Поэтому переименование и слияние людей
переписывают имя и в транскриптах — иначе статистика и образец терялись бы."""

import hashlib
import io
import json
import os
import re
import uuid
from pathlib import Path

from meet import library

AVATAR_SIZE = 256
MAX_NAME = 80
# Недопустимое в имени файла Windows плюс управляющие символы.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Палитра аватаров-инициалов: достаточно контрастная на тёмном фоне окна.
_PALETTE = ("#4b6bd6", "#c0793a", "#3a9a6a", "#a04bb0", "#c94f63", "#2f93a8",
            "#8a7a2e", "#6a5acd", "#b3563a", "#3d7fbf")


def valid_name(name: str) -> str:
    """Имя человека = имя файла. Всё, что может выйти за папку голосов или
    сломать файловую систему, — отказ, а не «исправление» за человека."""
    cleaned = (name or "").strip()
    if not cleaned or cleaned in (".", "..") or len(cleaned) > MAX_NAME:
        raise ValueError("недопустимое имя")
    if _BAD_CHARS.search(cleaned) or cleaned.endswith("."):
        raise ValueError("недопустимые символы в имени")
    return cleaned


def color(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).digest()
    return _PALETTE[digest[0] % len(_PALETTE)]


def _voice_file(name: str, voices: Path) -> Path:
    return voices / f"{valid_name(name)}.json"


def avatar_path(name: str, voices: Path) -> Path:
    return voices / f"{valid_name(name)}.png"


def _samples(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [s for s in data.get("samples", []) if isinstance(s, dict)]
    except (OSError, ValueError, AttributeError):
        return []


def _duration(t: dict) -> float | None:
    """Длительность реплики или None, если start/end битые (руками правили
    transcript.json): одна такая реплика не должна ронять весь список."""
    try:
        return float(t["end"]) - float(t["start"])
    except (KeyError, TypeError, ValueError):
        return None


def _segments(data: dict | None) -> list[dict]:
    segments = (data or {}).get("segments")
    return [s for s in segments if isinstance(s, dict)] if isinstance(segments, list) else []


def _turns(data: dict | None, name: str) -> list[dict]:
    return [s for s in _segments(data)
            if s.get("speaker") == name and _duration(s) is not None]


def _speech_index(recordings: Path) -> dict[str, tuple[int, float]]:
    """Имя → (встреч, секунд речи) по всей библиотеке за один проход: каждый
    transcript.json разбирается один раз, сколько бы людей ни было в базе."""
    index: dict[str, tuple[int, float]] = {}
    for folder in library.recording_folders(recordings):
        per_name: dict[str, float] = {}
        for s in _segments(library.read_transcript(folder)):
            speaker, duration = s.get("speaker"), _duration(s)
            if isinstance(speaker, str) and duration is not None:
                per_name[speaker] = per_name.get(speaker, 0.0) + duration
        for speaker, seconds in per_name.items():
            meetings, total = index.get(speaker, (0, 0.0))
            index[speaker] = (meetings + 1, total + seconds)
    return index


def listing(voices: Path, recordings: Path) -> list[dict]:
    if not voices.is_dir():
        return []
    index = _speech_index(recordings)
    out = []
    for f in sorted(voices.glob("*.json"), key=lambda p: p.stem.lower()):
        name = f.stem
        meetings, seconds = index.get(name, (0, 0.0))
        out.append({
            "name": name,
            "samples": len(_samples(f)),
            "meetings": meetings,
            "seconds": round(seconds),
            "has_avatar": (voices / f"{name}.png").exists(),
            "color": color(name),
        })
    return out


def person(name: str, voices: Path, recordings: Path) -> dict:
    """Карточка человека: голос плюс встречи, где он говорил (новые сверху).
    Нет такого голоса — KeyError."""
    name = valid_name(name)
    voice = _voice_file(name, voices)
    if not voice.exists():
        raise KeyError(name)
    meetings = []
    for folder in reversed(library.recording_folders(recordings)):
        seconds = sum(_duration(s) for s in _turns(library.read_transcript(folder), name))
        if not seconds:
            continue
        card = library.describe(folder)
        meetings.append({
            "recording": folder.name,
            "title": card.title if card else None,
            "started_at": card.started_at if card else None,
            "seconds": round(seconds),
        })
    return {
        "name": name,
        "color": color(name),
        "has_avatar": avatar_path(name, voices).exists(),
        "samples": len(_samples(voice)),
        "meetings": meetings,
    }


def sample(name: str, voices: Path, recordings: Path) -> dict | None:
    """Самая длинная реплика человека в последней по дате записи, где он
    говорит. `voices` — для единообразия с остальными вызовами: образец берётся
    из транскриптов, а не из базы голосов."""
    name = valid_name(name)
    for folder in reversed(library.recording_folders(recordings)):
        turns = _turns(library.read_transcript(folder), name)
        if turns:
            best = max(turns, key=_duration)
            track = next((stem for stem in ("sys", "source", "mic")
                          if library.find_track(folder, stem)), "source")
            return {"recording": folder.name, "start": best["start"],
                    "end": best["end"], "track": track}
    return None


def _rewrite_speaker(recordings: Path, old: str, new: str) -> int:
    """Переписать имя спикера во всех транскриптах библиотеки (атомарно,
    файл за файлом). Возвращает число изменённых записей."""
    changed = 0
    for folder in library.recording_folders(recordings):
        data = library.read_transcript(folder)
        if not data:
            continue
        hit = False
        for segment in _segments(data):
            if segment.get("speaker") == old:
                segment["speaker"] = new
                hit = True
        # «names» (названо из окна) и «speakers» (узнано по базе): метка → имя.
        for key in ("names", "speakers"):
            mapping = data.get(key)
            if isinstance(mapping, dict):
                for label, value in mapping.items():
                    if value == old:
                        mapping[label] = new
                        hit = True
        if hit:
            library.write_transcript(folder, data)
            changed += 1
    return changed


def _same_file(a: Path, b: Path) -> bool:
    """Один и тот же файл под двумя именами — на Windows это «демьян» и «Демьян»."""
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def set_avatar(name: str, data: bytes, voices: Path) -> Path:
    """Картинка → квадрат 256×256 PNG (центральная обрезка). Круглой её делает
    UI: так файл пригоден и для круга, и для квадрата."""
    if not _voice_file(name, voices).exists():
        raise KeyError(name)
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            # Фото с телефона хранятся «на боку» с пометкой EXIF Orientation:
            # без поворота аватар выйдет лежащим.
            rgb = ImageOps.exif_transpose(img).convert("RGBA")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        # Бомба декомпрессии (крошечный файл на гигапиксели) — тоже «не
        # изображение», а не 500 и не съеденная память резидента.
        raise ValueError("не изображение")
    side = min(rgb.size)
    left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
    square = rgb.crop((left, top, left + side, top + side)).resize(
        (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    path = avatar_path(name, voices)
    tmp = path.with_suffix(".png.tmp")
    try:
        square.save(tmp, "PNG")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # огрызок не должен остаться рядом с голосом
        raise
    return path


def clear_avatar(name: str, voices: Path) -> None:
    avatar_path(name, voices).unlink(missing_ok=True)


def _move(src: Path, dst: Path, via_temp: bool) -> None:
    """Переименовать файл; при смене одного регистра — через временное имя:
    на регистронезависимой ФС прямой rename в «тот же» файл ничего не меняет."""
    if via_temp:
        tmp = src.with_name(f".{uuid.uuid4().hex}.renaming")
        src.rename(tmp)
        tmp.rename(dst)
    else:
        src.rename(dst)


def rename(old: str, new: str, voices: Path, recordings: Path) -> None:
    """Переименовать человека: голос, аватар и имя в транскриптах библиотеки."""
    old, new = valid_name(old), valid_name(new)
    src, dst = _voice_file(old, voices), _voice_file(new, voices)
    if not src.exists():
        raise KeyError(old)
    if old == new:
        return
    case_only = _same_file(src, dst)
    if dst.exists() and not case_only:
        raise FileExistsError(new)
    old_avatar, new_avatar = avatar_path(old, voices), avatar_path(new, voices)
    if not case_only:
        # Картинка без голоса — сирота прежнего человека; она не должна ломать
        # переименование уже после того, как голос переехал.
        new_avatar.unlink(missing_ok=True)
    _move(src, dst, case_only)
    if old_avatar.exists():
        _move(old_avatar, new_avatar, case_only)
    _rewrite_speaker(recordings, old, new)


def merge(src_name: str, into: str, voices: Path, recordings: Path) -> None:
    """Слить два голоса одного человека: образцы src дописываются в into, а
    его реплики в транскриптах переходят к into."""
    src_name, into = valid_name(src_name), valid_name(into)
    src, dst = _voice_file(src_name, voices), _voice_file(into, voices)
    if src == dst or _same_file(src, dst):
        # Слияние с самим собой удалило бы человека целиком.
        raise ValueError("нельзя слить человека с самим собой")
    if not src.exists():
        raise KeyError(src_name)
    if not dst.exists():
        raise KeyError(into)
    merged = _samples(dst) + _samples(src)
    # Атомарно: оборванная запись не должна оставить into без голоса.
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"samples": merged}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dst)
    delete(src_name, voices)
    _rewrite_speaker(recordings, src_name, into)


def delete(name: str, voices: Path) -> None:
    _voice_file(name, voices).unlink(missing_ok=True)
    clear_avatar(name, voices)
