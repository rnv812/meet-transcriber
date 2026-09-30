"""Люди базы голосов для окна приложения: список со статистикой, образец для
прослушивания, аватары и правка базы.

Голоса хранит voices.py (`<voices>/<имя>.json`); здесь — всё, что про человека,
а не про эмбеддинги. Аватар — `<voices>/<имя>.png`, квадрат 256×256.
Статистика не хранится, а считается из транскриптов записей, откуда взяты
образцы голоса: файлы — источник истины, и копия папки с другой машины не
разъезжается с «базой»."""

import hashlib
import io
import json
import re
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


def _meeting_folders(samples: list[dict], recordings: Path) -> list[Path]:
    """Папки записей из источников образцов, которые ещё существуют в
    библиотеке. Источник-файл (импорт до v1, расшифровка файла в «Загрузках»)
    — не запись библиотеки, его не считаем."""
    root = recordings.resolve()
    seen, out = set(), []
    for s in samples:
        try:
            folder = Path(str(s.get("source") or "")).resolve()
        except (OSError, ValueError):
            continue
        if folder.parent == root and folder.is_dir() and folder not in seen:
            seen.add(folder)
            out.append(folder)
    return sorted(out, key=lambda p: p.name)


def _duration(t: dict) -> float | None:
    """Длительность реплики или None, если start/end битые (руками правили
    transcript.json): одна такая реплика не должна ронять весь список."""
    try:
        return float(t["end"]) - float(t["start"])
    except (KeyError, TypeError, ValueError):
        return None


def _turns(folder: Path, name: str) -> list[dict]:
    data = library.read_transcript(folder) or {}
    segments = data.get("segments", [])
    return [s for s in segments if isinstance(s, dict)
            and s.get("speaker") == name and _duration(s) is not None]


def listing(voices: Path, recordings: Path) -> list[dict]:
    if not voices.is_dir():
        return []
    out = []
    for f in sorted(voices.glob("*.json"), key=lambda p: p.stem.lower()):
        name = f.stem
        samples = _samples(f)
        folders = _meeting_folders(samples, recordings)
        seconds = sum(_duration(t)
                      for folder in folders for t in _turns(folder, name))
        out.append({
            "name": name,
            "samples": len(samples),
            "meetings": len(folders),
            "seconds": round(seconds),
            "has_avatar": (voices / f"{name}.png").exists(),
            "color": color(name),
        })
    return out


def sample(name: str, voices: Path, recordings: Path) -> dict | None:
    """Самая длинная реплика человека в последней по дате встрече с ним."""
    folders = _meeting_folders(_samples(_voice_file(name, voices)), recordings)
    for folder in reversed(folders):
        turns = _turns(folder, name)
        if turns:
            best = max(turns, key=_duration)
            card = library.describe(folder)
            track = "sys" if card and "sys" in card.tracks else "source"
            return {"recording": folder.name, "start": best["start"],
                    "end": best["end"], "track": track}
    return None


def set_avatar(name: str, data: bytes, voices: Path) -> Path:
    """Картинка → квадрат 256×256 PNG (центральная обрезка). Круглой её делает
    UI: так файл пригоден и для круга, и для квадрата."""
    if not _voice_file(name, voices).exists():
        raise KeyError(name)
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            rgb = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("не изображение")
    side = min(rgb.size)
    left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
    square = rgb.crop((left, top, left + side, top + side)).resize(
        (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    path = avatar_path(name, voices)
    tmp = path.with_suffix(".png.tmp")
    square.save(tmp, "PNG")
    tmp.replace(path)
    return path


def clear_avatar(name: str, voices: Path) -> None:
    avatar_path(name, voices).unlink(missing_ok=True)


def rename(old: str, new: str, voices: Path) -> None:
    src, dst = _voice_file(old, voices), _voice_file(new, voices)
    if not src.exists():
        raise KeyError(old)
    if dst.exists():
        raise FileExistsError(new)
    old_avatar, new_avatar = avatar_path(old, voices), avatar_path(new, voices)
    # Картинка без голоса — сирота прежнего человека; она не должна ломать
    # переименование уже после того, как голос переехал.
    new_avatar.unlink(missing_ok=True)
    src.rename(dst)
    if old_avatar.exists():
        old_avatar.rename(new_avatar)


def merge(src_name: str, into: str, voices: Path) -> None:
    """Слить два голоса одного человека: образцы src дописываются в into."""
    src, dst = _voice_file(src_name, voices), _voice_file(into, voices)
    if src == dst:
        # Слияние с самим собой удалило бы человека целиком.
        raise ValueError("нельзя слить человека с самим собой")
    if not src.exists():
        raise KeyError(src_name)
    if not dst.exists():
        raise KeyError(into)
    merged = _samples(dst) + _samples(src)
    dst.write_text(json.dumps({"samples": merged}, ensure_ascii=False), encoding="utf-8")
    delete(src_name, voices)


def delete(name: str, voices: Path) -> None:
    _voice_file(name, voices).unlink(missing_ok=True)
    clear_avatar(name, voices)
