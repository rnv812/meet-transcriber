"""База голосов: имя человека → эмбеддинги его голоса (WeSpeaker-центроиды
из pyannote community-1). Только векторы, без звука; см. спеку
docs/superpowers/specs/2026-07-02-speaker-enrollment-design.md."""

import json
from pathlib import Path

import numpy as np

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
            print(f"голоса: {label} → {best_name} (cos {best_score:.2f})")
        else:
            print(f"голоса: {label} → не распознан (лучший кандидат: {best_name}, cos {best_score:.2f})")
    return matched
