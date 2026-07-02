"""База голосов: имя человека → эмбеддинги его голоса (WeSpeaker-центроиды
из pyannote community-1). Только векторы, без звука; см. спеку
docs/superpowers/specs/2026-07-02-speaker-enrollment-design.md."""

import json
from pathlib import Path

import numpy as np

from meet.diarize import DIARIZATION_MODEL

VOICES_DIR = Path("voices")
# Пороги матчинга; калибруются на реальных встречах (см. спеку, «Приёмка»).
THRESHOLD = 0.5
MARGIN = 0.05


def load_voices(folder: Path = VOICES_DIR) -> dict[str, list[np.ndarray]]:
    """Имя → список эмбеддингов. Битый файл пропускается с предупреждением."""
    out: dict[str, list[np.ndarray]] = {}
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            samples = [np.asarray(s["embedding"], dtype=np.float32) for s in data["samples"]]
        except Exception as e:
            print(f"голоса: пропускаю {f.name} (ошибка: {e})")
            continue
        if samples:
            out[f.stem] = samples
    return out


def add_sample(
    name: str, embedding: list[float], source: str, date: str, folder: Path = VOICES_DIR
) -> Path:
    """Дописать образец голоса; повтор из того же source заменяет старый образец."""
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{name}.json"
    data = {"samples": []}
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
    samples = [s for s in data["samples"] if s.get("source") != source]
    samples.append({"embedding": [float(x) for x in embedding], "source": source, "date": date})
    f.write_text(json.dumps({"samples": samples}, ensure_ascii=False), encoding="utf-8")
    return f


def sidecar_path(out_md: Path) -> Path:
    """Путь сайдкара эмбеддингов рядом с транскриптом."""
    return out_md.with_name(f"{out_md.stem.removesuffix('_transcript')}_speakers.json")


def write_sidecar(out_md: Path, source: str, date: str, speakers: list[dict]) -> Path:
    p = sidecar_path(out_md)
    p.write_text(
        json.dumps(
            {"model": DIARIZATION_MODEL, "source": source, "date": date, "speakers": speakers},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def read_sidecar(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else -1.0


def match_speakers(
    embeddings: dict[str, np.ndarray],
    voices: dict[str, list[np.ndarray]],
    threshold: float = THRESHOLD,
    margin: float = MARGIN,
) -> dict[str, str]:
    """Уверенные совпадения «метка диаризации → имя из базы».

    Уверенность: близость лучшего кандидата ≥ threshold И отрыв от лучшего
    ДРУГОГО человека ≥ margin (иначе честный «Спикер N», а не угаданное имя).
    Человек = максимум косинуса по его образцам."""
    if not voices:
        return {}
    matched: dict[str, str] = {}
    for label, emb in embeddings.items():
        scores = sorted(
            ((max(_cos(emb, s) for s in samples), name) for name, samples in voices.items()),
            reverse=True,
        )
        best_score, best_name = scores[0]
        second = scores[1][0] if len(scores) > 1 else None
        if best_score >= threshold and (second is None or best_score - second >= margin):
            matched[label] = best_name
            # IMPORTANT: в консольных логах только ASCII-пунктуация («->», не «→»):
            # консоль Windows (cp866) не кодирует стрелки/тире, print падает
            # UnicodeEncodeError, и except в _match_names глотает весь матчинг.
            print(f"голоса: {label} -> {best_name} (cos {best_score:.2f})")
        else:
            print(f"голоса: {label} -> не распознан (лучший кандидат: {best_name}, cos {best_score:.2f})")
    return matched


def _find_sidecar(path: Path) -> Path:
    """Сайдкар по папке записи, транскрипту или прямому пути к .json."""
    retry = "перетранскрибируй запись (meet transcribe) — сайдкар появится рядом с транскриптом"
    if path.is_dir():
        found = sorted(path.glob("*_speakers.json"))
        if not found:
            raise SystemExit(f"В {path} нет *_speakers.json — {retry}")
        return found[-1]
    if path.suffix == ".json" and path.exists():
        return path
    if path.suffix == ".md":
        p = sidecar_path(path)
        if p.exists():
            return p
        raise SystemExit(f"Рядом с {path.name} нет {p.name} — {retry}")
    raise SystemExit(f"Не найден сайдкар для {path} — дай папку записи или *_speakers.json")


def enroll(path_str: str, mappings: list[str], folder: Path = VOICES_DIR) -> None:
    """Перенести эмбеддинги из сайдкара записи в базу голосов.

    mappings: «Спикер 1=Демьян Петров» (имя из транскрипта или сырая метка SPEAKER_XX)."""
    sidecar = _find_sidecar(Path(path_str))
    try:
        data = read_sidecar(sidecar)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Битый сайдкар {sidecar.name}: {e}")
    by_key: dict[str, dict] = {}
    for s in data["speakers"]:
        # если два кластера авто-совпали с одним человеком, их display-имена
        # совпадают и побеждает последняя запись — приемлемо: авто-совпавших
        # спикеров в описанном сценарии повторно не энроллят
        by_key[s["display"]] = s
        by_key[s["label"]] = s
    source = data.get("source", str(sidecar))
    date = data.get("date", "")
    for m in mappings:
        who, sep, name = m.partition("=")
        who, name = who.strip(), name.strip()
        if not sep or not who or not name:
            raise SystemExit(f"Непонятное соответствие «{m}» — формат: \"Спикер 1=Демьян Петров\" (со знаком =)")
        entry = by_key.get(who)
        if entry is None:
            known = ", ".join(s["display"] for s in data["speakers"])
            raise SystemExit(f"В {sidecar.name} нет спикера «{who}»; есть: {known}")
        f = add_sample(name, entry["embedding"], source=source, date=date, folder=folder)
        total = len(json.loads(f.read_text(encoding="utf-8"))["samples"])
        print(f"голоса: {name} += образец из {source} (всего образцов: {total})")
