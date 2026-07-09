"""Live-опознание голоса по сегменту аудио: эмбеддинг WeSpeaker (та же модель,
что внутри pyannote community-1 — подпапка embedding того же чекпойнта, поэтому
векторы косинус-совместимы с центроидами базы voices/) + тихий матч против базы.
Работает без диаризации: один сегмент — один эмбеддинг — одно имя."""

import os

import numpy as np

from meet.diarize import DIARIZATION_MODEL
from meet.voices import MARGIN, THRESHOLD, best_match, load_voices

# Короче секунды эмбеддинг неустойчив (и упирается в min_num_samples модели —
# та возвращает NaN, не ошибку); такие сегменты честно остаются «Собеседник».
MIN_SECONDS = 1.0
SAMPLE_RATE = 16000


def _load_embedder():
    """Реальный эмбеддер на GPU: np.float32 16 кГц -> np.ndarray (256,)."""
    import torch
    from pyannote.audio.pipelines.speaker_verification import (
        PretrainedSpeakerEmbedding,
    )

    model = PretrainedSpeakerEmbedding(
        {"checkpoint": DIARIZATION_MODEL, "subfolder": "embedding"},
        device=torch.device("cuda"),
        token=os.environ.get("HF_TOKEN"),
    )

    def embed(audio: np.ndarray) -> np.ndarray:
        wav = torch.from_numpy(audio).float()[None, None, :]
        return np.asarray(model(wav)[0])

    return embed


class VoiceMatcher:
    """name_for(audio) для живого режима: имя из базы или None.

    embed_fn инжектируется в тестах; в бою load() строит реальный эмбеддер.
    Пустая база — матчер выключен, модель не грузится."""

    def __init__(self, base=None, embed_fn=None, min_seconds: float = MIN_SECONDS,
                 threshold: float = THRESHOLD, margin: float = MARGIN) -> None:
        self.base = base
        self._embed = embed_fn
        self.min_seconds = min_seconds
        self.threshold = threshold
        self.margin = margin
        self._announced: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.base) and self._embed is not None

    def load(self) -> None:
        if self.base is None:
            self.base = load_voices()
        if not self.base:
            print("голоса: база пуста - live-имена выключены")
            return
        if self._embed is None:
            self._embed = _load_embedder()
        print(f"голоса: live-имена включены ({len(self.base)} чел. в базе)")

    def name_for(self, audio: np.ndarray) -> "str | None":
        if not self.enabled or len(audio) < self.min_seconds * SAMPLE_RATE:
            return None
        emb = self._embed(audio)
        if emb is None or not np.isfinite(emb).all():
            return None
        score, name, second = best_match(emb, self.base)
        if score >= self.threshold and (second is None or score - second >= self.margin):
            if name not in self._announced:
                self._announced.add(name)
                print(f"голоса: live {name} (cos {score:.2f})")
            return name
        return None
