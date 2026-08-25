"""Движок расшифровки: что установлено и как это доставить.

Запись, панель и настройки работают без него — это осознанное разделение:
на ноутбуке встречу пишут, а расшифровывают на машине с картой. Поэтому тяжёлый
стек (torch, faster-whisper, pyannote) не тащится вместе с приложением, а
ставится по кнопке — здесь же, из настроек, и тем же кодом при первом запуске
инсталлятора.

Ставим в окружение того интерпретатора, которым запущен резидент: в dev это
venv репозитория, в установленном приложении — приватный venv движка
(`paths.engine_dir()`). Так «где движок» и «чем запускать задачи» — один ответ,
а не два.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

# Что должно быть, чтобы расшифровка прошла целиком. Порядок — как в пайплайне:
# сначала распознавание, потом выравнивание, потом диаризация.
COMPONENTS = (
    ("faster_whisper", "распознавание речи (faster-whisper)"),
    ("torch", "вычисления (torch)"),
    ("pyannote.audio", "диаризация (pyannote)"),
    ("transformers", "выравнивание по словам (wav2vec2)"),
    ("scipy", "обработка сигнала"),
)

# Индексы колёс torch: CUDA-сборка тяжелее, но без неё расшифровка идёт на CPU
# и на часовой встрече это часы вместо минут.
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

PACKAGES = (
    "faster-whisper>=1.1",
    "pyannote.audio>=3.3",
    "transformers>=4.40",
    "scipy>=1.11",
)
CUDA_RUNTIME = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")

# Порядок величин для честного предупреждения: скачивание идёт гигабайтами, и
# человек должен узнать об этом до нажатия, а не по счётчику трафика.
DOWNLOAD_HINT_GB = {"cuda": 3.0, "cpu": 0.6}


def installed(name: str) -> bool:
    """Есть ли модуль. `find_spec` не импортирует — проверка дешёвая и не
    тянет CUDA в память резидента."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def gpu() -> dict:
    """Карта NVIDIA, если её видно. Ошибки — это «не видно», а не сбой:
    отсутствие nvidia-smi нормально для ноутбука."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "name": None}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "name": None}
    name = (out.stdout or "").strip().splitlines()
    return {"available": bool(name), "name": name[0] if name else None}


def state() -> dict:
    """Состояние движка для настроек: чего не хватает и куда встанет."""
    components = [
        {"module": module, "title": title, "installed": installed(module)}
        for module, title in COMPONENTS
    ]
    missing = [c["module"] for c in components if not c["installed"]]
    card = gpu()
    return {
        "installed": not missing,
        "missing": missing,
        "components": components,
        "gpu": card,
        "flavor": "cuda" if card["available"] else "cpu",
        "download_gb": DOWNLOAD_HINT_GB["cuda" if card["available"] else "cpu"],
        "python": sys.executable,
        "target": str(Path(sys.prefix)),
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


def install_steps(flavor: str | None = None) -> list[list[str]]:
    """Команды установки по шагам.

    Два шага, а не один: torch живёт на своём индексе, и смешивать его с
    остальными пакетами в одной команде — верный способ утянуть не ту сборку.
    """
    flavor = flavor or ("cuda" if gpu()["available"] else "cpu")
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    index = TORCH_CUDA_INDEX if flavor == "cuda" else TORCH_CPU_INDEX
    steps = [pip + ["torch", "--index-url", index]]
    extras = list(PACKAGES) + (list(CUDA_RUNTIME) if flavor == "cuda" else [])
    steps.append(pip + extras)
    return steps


def install(flavor: str | None = None, on_line=None, runner=None) -> int:
    """Поставить движок, отдавая вывод построчно.

    Прогресса в процентах здесь нет и не будет: pip не сообщает общий объём
    заранее. Показываем, что именно сейчас качается, — это честнее выдуманной
    шкалы (тот же принцип, что у ступеней расшифровки).
    """
    run = runner or _run
    for step in install_steps(flavor):
        code = run(step, on_line)
        if code != 0:
            return code
    return 0


def _run(argv: list[str], on_line) -> int:
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        if on_line:
            on_line(f"не удалось запустить pip: {e}")
        return 1
    assert process.stdout is not None
    for line in process.stdout:
        if on_line:
            on_line(line.rstrip())
    return process.wait()
