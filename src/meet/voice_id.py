"""Голоса живого режима: эмбеддинг WeSpeaker (та же модель, что внутри pyannote
community-1 — подпапка embedding того же чекпойнта, поэтому векторы
косинус-совместимы с центроидами базы voices/ и образцом владельца), база
голосов и образец владельца. Решения об именах — по накопленным онлайн-
кластерам (meet.live_voices), а не по одному сегменту: короткий клип против
центроида встречи порог узнавания почти никогда не проходит (калибровка T0)."""

import numpy as np

from meet.voices import load_voices

# Короче секунды эмбеддинг неустойчив (и упирается в min_num_samples модели —
# та возвращает NaN, не ошибку): такие сегменты берут голос соседнего
# (meet.live_voices.EMBED_MIN_S).
MIN_SECONDS = 1.0
SAMPLE_RATE = 16000
# Потоков torch на эмбеддинг: с потоками по умолчанию время на гибридных ядрах
# скачет 170–1300 мс на сегмент, с 4 — 60–110 мс (калибровка T0).
EMBED_THREADS = 4


def _capped(call):
    """`call()` с потоками torch не больше EMBED_THREADS, потом — как было.
    Распознавание и эмбеддинги идут в одном рабочем потоке живого режима
    по очереди, так что временное урезание чужих вызовов не задевает."""
    try:
        import torch

        before = torch.get_num_threads()
    except Exception:
        return call()
    if before <= EMBED_THREADS:
        return call()
    torch.set_num_threads(EMBED_THREADS)
    try:
        return call()
    finally:
        torch.set_num_threads(before)


def _embedder_device() -> str:
    """cuda — когда torch видит видеокарту (`asr.torch_device`: не профиль
    CPU, не «Процессор» в настройках, без сбоя CUDA у torch), чем бы ни
    распознавался текст; иначе cpu (на macOS — тоже cpu)."""
    from meet import asr

    return asr.torch_device()


def _build_embedder(device: str):
    import torch

    from meet.diarize import load_speaker_embedding

    # Скачанная модель — с диска, без запросов к Hugging Face и токена;
    # телеметрию pyannote выключает загрузчик (живой процесс — не задача).
    model = load_speaker_embedding(torch.device(device))

    def embed(audio: np.ndarray) -> np.ndarray:
        wav = torch.from_numpy(audio).float()[None, None, :]
        out = model(wav) if device == "cuda" else _capped(lambda: model(wav))
        return np.asarray(out[0])

    return embed


def _load_embedder():
    """Реальный эмбеддер: np.float32 16 кГц -> np.ndarray (256,). Видеокарта,
    если она рабочая; не нашлись библиотеки CUDA — один повтор на процессоре.

    torch грузит cuDNN лениво, на первой свёртке: поэтому на видеокарте сразу
    пробный эмбеддинг секунды тишины, а если библиотека всё же отвалится на
    живом сегменте — эмбеддер один раз переезжает на процессор там же."""
    from meet import asr

    device = _embedder_device()
    try:
        embed = _build_embedder(device)
        if device == "cuda":
            embed(np.zeros(SAMPLE_RATE, dtype=np.float32))
    except Exception as e:
        if device == "cuda" and asr.missing_cuda_library(e):
            asr.torch_cuda_failed(e)
            return _build_embedder("cpu")
        raise
    if device != "cuda":
        return embed
    current = {"embed": embed, "on_cuda": True}

    def guarded(audio: np.ndarray) -> np.ndarray:
        try:
            return current["embed"](audio)
        except Exception as e:
            if not (current["on_cuda"] and asr.missing_cuda_library(e)):
                raise
            asr.torch_cuda_failed(e)
            current["on_cuda"] = False
            current["embed"] = _build_embedder("cpu")
            return current["embed"](audio)

    return guarded


class VoiceMatcher:
    """База голосов, образец владельца и эмбеддер для живого режима.

    `load()` грузит эмбеддер, если есть база голосов или образец владельца
    (иначе называть некого и делить микрофон не по чему — модель не
    грузится). `live_voices()` — онлайн-кластеры дорожек (meet.live_voices).
    Порог имени — T_live из настроек (`mic_split.live_threshold(
    asr.voice_threshold)`), микрофон делится по голосам при
    `asr.mic_speakers`. `embed_fn` инжектируется в тестах."""

    def __init__(self, base=None, embed_fn=None, threshold: float | None = None,
                 owner=None, mic: bool | None = None, log=print) -> None:
        self.base = base
        self.owner = owner
        self._embed = embed_fn
        self.threshold = threshold
        self.mic = mic
        self._log = log

    @property
    def enabled(self) -> bool:
        return self._embed is not None and (bool(self.base) or (bool(self.owner) and self.mic is not False))

    def _settings(self) -> None:
        """Порог имени и «делить микрофон» — из настроек, если не заданы."""
        if self.threshold is not None and self.mic is not None:
            return
        from meet import mic_split

        try:
            from meet import settings

            asr = settings.load().asr
            voice_threshold, mic = asr.voice_threshold, asr.mic_speakers
        except Exception:
            from meet.settings import VOICE_THRESHOLD

            voice_threshold, mic = VOICE_THRESHOLD, True
        if self.threshold is None:
            self.threshold = mic_split.live_threshold(voice_threshold)
        if self.mic is None:
            self.mic = bool(mic)

    def load(self) -> None:
        if self.base is None:
            self.base = load_voices()
        if self.owner is None:
            try:
                from meet import owner_voice

                self.owner = owner_voice.load()
            except Exception as e:
                self._log(f"голоса: образец владельца не прочитан ({type(e).__name__}: {e})")
                self.owner = []
        self._settings()
        if not self.base and not (self.owner and self.mic):
            # Называть некого, микрофон делить не по чему (или выключено) —
            # эмбеддер не нужен ни одной дорожке.
            self._log("голоса: база пуста и микрофон не делится - live-имена выключены")
            return
        if self._embed is None:
            try:
                self._embed = _load_embedder()
            except Exception as e:
                self._log(f"голоса: опознание в живом режиме недоступно ({type(e).__name__}: {e})"
                          " — имена появятся после расшифровки")
                return
        parts = [f"{len(self.base)} чел. в базе"] if self.base else []
        if self.owner:
            parts.append("микрофон по голосам" if self.mic else "образец владельца есть, "
                         "микрофон не делится (настройки)")
        elif self.mic:
            # Без образца микрофон весь владельца: люди рядом (встреча за одним
            # ноутбуком) подписываются «Вы» — причина должна быть видна в журнале.
            parts.append("микрофон не делится: нет образца вашего голоса")
        self._log(f"голоса: live-имена включены ({', '.join(parts)}, порог {self.threshold:.2f})")

    def live_voices(self, defaults: dict | None = None, log=None):
        """Онлайн-кластеры дорожек (meet.live_voices.LiveVoices) или None —
        эмбеддера нет или называть некого."""
        if not self.enabled:
            return None
        from meet.live_voices import LiveVoices

        self._settings()
        return LiveVoices(self._embed, self.base, self.owner, name_threshold=self.threshold,
                          defaults=defaults, mic=self.mic, log=log or self._log)
