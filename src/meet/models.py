"""Модели: что нужно расшифровке, где это лежит и как это принести.

Источник тот же, что у Handy, — Hugging Face. Разница в том, кто качает: Handy
тянет ggml-файлы напрямую в свою папку, а у нас это делают библиотеки движка
(faster-whisper для распознавания, pyannote для диаризации, transformers для
выравнивания). Кэш у них общий — `huggingface_hub`, — и переносить его мы не
будем: уже скачанные гигабайты не должны качаться заново.

Две вещи, которые обязаны быть видны человеку до нажатия «скачать»:
**размер** (модели измеряются гигабайтами) и **гейт** — диаризация лежит за
принятием условий на сайте и требует токена. Молча упереться в 401 хуже, чем
заранее сказать, что нужен токен.
"""

import os
import re
from pathlib import Path

ASR = "asr"
DIARIZATION = "diarization"
ALIGN = "align"

# Каталог: то, что пайплайн умеет использовать сегодня. Не «все модели мира» —
# список, за который есть чем отвечать: каждая строка проверена на реальных
# встречах либо подтверждена замерами на машине без NVIDIA.
CATALOGUE = (
    {
        "id": "bzikst/faster-whisper-large-v3-russian",
        "kind": ASR,
        "backend": "faster-whisper",
        "title": "Whisper large-v3 — русский fine-tune",
        "note": "эталон качества на русском: пунктуация и термины заметно лучше стоковой",
        "size_gb": 3.1,
        "language": "ru",
        "recommended": True,
    },
    {
        "id": "Systran/faster-whisper-large-v3",
        "kind": ASR,
        "backend": "faster-whisper",
        "title": "Whisper large-v3 — стоковая",
        "note": "многоязычная; на русском слабее fine-tune, но берёт другие языки",
        "size_gb": 3.1,
        "language": "multi",
    },
    {
        "id": "Systran/faster-whisper-medium",
        "kind": ASR,
        "backend": "faster-whisper",
        "title": "Whisper medium",
        "note": "вдвое быстрее и легче; на терминах и именах ошибается заметно чаще",
        "size_gb": 1.5,
        "language": "multi",
    },
    {
        "id": "pyannote/speaker-diarization-community-1",
        "kind": DIARIZATION,
        "title": "Диаризация pyannote community-1",
        "note": "кто когда говорил; без неё транскрипт будет без имён спикеров",
        "size_gb": 0.1,
        "gated": True,
    },
    {
        "id": "jonatasgrosman/wav2vec2-large-xlsr-53-russian",
        "kind": ALIGN,
        "title": "Выравнивание по словам (wav2vec2, русский)",
        "note": "уточняет границы реплик: короткие «ага» реже уезжают соседу",
        "size_gb": 1.2,
        "language": "ru",
    },
)


def cache_root() -> Path:
    """Кэш Hugging Face. Тот же, что у библиотек движка: свой заводить нельзя —
    иначе уже скачанное качается заново."""
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.environ.get(env)
        if value:
            return Path(value)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _folder(repo_id: str) -> Path:
    return cache_root() / ("models--" + repo_id.replace("/", "--"))


def downloaded(repo_id: str) -> bool:
    """Есть ли модель в кэше.

    Проверяем по снапшотам, а не по самой папке: скачивание оставляет её и при
    обрыве, и «скачано» тогда означало бы «пусто»."""
    snapshots = _folder(repo_id) / "snapshots"
    if not snapshots.is_dir():
        return False
    return any(any(snapshot.iterdir()) for snapshot in snapshots.iterdir()
               if snapshot.is_dir())


def size_on_disk(repo_id: str) -> int:
    """Сколько занято на диске, байт. Нет — ноль."""
    folder = _folder(repo_id)
    if not folder.is_dir():
        return 0
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def token() -> str | None:
    """Токен Hugging Face: из настроек, иначе из переменной среды.

    Переменная остаётся рабочей — так живут нынешние запуски CLI, и отбирать
    этот путь незачем."""
    try:
        from meet import settings

        value = settings.load().integrations.hf_token
        if value:
            return value
    except Exception:
        pass
    for env in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(env)
        if value and value.strip():
            return value.strip()
    return None


def state(selected: str | None = None) -> dict:
    """Каталог с отметками «скачано» и «выбрано» — для окна настроек."""
    have_token = bool(token())
    items = []
    for model in CATALOGUE:
        items.append({
            **model,
            "downloaded": downloaded(model["id"]),
            "size_on_disk": size_on_disk(model["id"]),
            "selected": model["id"] == selected,
            # Гейтед-модель без токена скачать нельзя — это видно до нажатия,
            # а не по ошибке 401 в середине.
            "blocked": bool(model.get("gated")) and not have_token,
        })
    return {
        "items": items,
        "cache": str(cache_root()),
        "token": have_token,
        "selected": selected,
        # Скачивать модели можно только когда есть загрузчик, а он приходит с
        # движком. Без него UI показывает это, а не роняет кнопку в ошибку.
        "can_download": _hub_available(),
    }


def _hub_available() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("huggingface_hub") is not None
    except (ImportError, ValueError):
        return False


def download(repo_id: str, on_line=None) -> int:
    """Скачать модель в общий кэш.

    Тянет `huggingface_hub`, который приходит вместе с движком: без движка
    качать нечем и незачем — расшифровывать всё равно будет некому.
    """
    known = {model["id"] for model in CATALOGUE}
    if repo_id not in known:
        # Скачивать что попало по строке из сети — плохая идея: это путь на
        # диск и трафик. Каталог тут и есть список разрешённого.
        if on_line:
            on_line(f"модель не из каталога: {repo_id}")
        return 2
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        # huggingface_hub приходит вместе с движком (faster-whisper, transformers,
        # pyannote зависят от него). Без движка качать нечем и незачем.
        if on_line:
            on_line("сначала установите движок расшифровки — он приносит и загрузчик")
        return 3
    try:
        path = snapshot_download(repo_id, token=token())
    except Exception as e:
        if on_line:
            on_line(_explain(repo_id, e))
        return 1
    if on_line:
        on_line(f"скачано: {path}")
    return 0


def _explain(repo_id: str, error: Exception) -> str:
    """Понятный текст вместо трассировки библиотеки."""
    text = str(error)
    if re.search(r"401|403|gated|awaiting a review|access", text, re.I):
        return (
            f"{repo_id}: нет доступа. Модель за принятием условий — примите их на "
            "huggingface.co и укажите токен в настройках"
        )
    if re.search(r"connection|timeout|resolve|network", text, re.I):
        return f"{repo_id}: не дозвонились до Hugging Face ({type(error).__name__})"
    return f"{repo_id}: {type(error).__name__}: {text[:300]}"
