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

from meet import plat

# Что должно быть, чтобы расшифровка прошла целиком. Порядок — как в пайплайне:
# сначала распознавание, потом выравнивание, потом диаризация.
COMPONENTS = (
    ("faster_whisper", "распознавание речи (faster-whisper)"),
    ("gigaam", "распознавание речи (GigaAM)"),
    ("torch", "вычисления (torch)"),
    ("pyannote.audio", "диаризация (pyannote)"),
    ("transformers", "выравнивание по словам (wav2vec2)"),
    ("scipy", "обработка сигнала"),
)
# Без этих компонентов движок работает (распознаёт Whisper): в окне они видны
# с пометкой, но «движок не установлен» из-за них не показывается. GigaAM
# приезжает с обновлением движка (новая версия ставит окружение заново).
OPTIONAL_COMPONENTS = {"gigaam": "не установлена — будет установлена при обновлении движка"}
# Причина, по которой необязательный шаг установки GigaAM не прошёл (пишет
# оболочка, engine.rs GIGAAM_ERROR_FILE) — в окружении движка.
GIGAAM_ERROR_FILE = "gigaam-install-error.txt"

# Индексы колёс torch: CUDA-сборка тяжелее, но без неё расшифровка идёт на CPU
# и на часовой встрече это часы вместо минут.
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
# Профиль «Apple Silicon» (macOS, экспериментально): torch — с PyPI (колёса
# macOS arm64 там с поддержкой MPS), пакеты — как у CPU (extra engine-mac):
# faster-whisper на процессоре (int8), GigaAM на процессоре, pyannote — на MPS,
# если он есть. Индекса PyTorch у него нет.
MAC_PROFILE = "mac"
PROFILES = ("cuda", "cpu", MAC_PROFILE)
# torch для движка установщика — одна минорная версия на оба профиля: без пина
# CPU-индекс отдавал 2.14, CUDA-индекс — 2.11. Смена пина — вместе со
# scripts/build_release.ps1 ($TorchSpecs, тест следит).
TORCH_SPECS = ("torch==2.11.*", "torchaudio==2.11.*")


def torch_index(profile: str) -> str | None:
    """Индекс колёс torch профиля; None — PyPI (Apple Silicon)."""
    if profile == "cuda":
        return TORCH_CUDA_INDEX
    if profile == MAC_PROFILE:
        return None
    return TORCH_CPU_INDEX


def python_path(env_dir: str, profile: str) -> str:
    """Интерпретатор venv движка. Склейкой строк, а не через os.path: шаги
    воспроизводит инсталлятор на Rust, а фикстуры в tests/fixtures должны
    совпадать на любой ОС. Windows — `Scripts\\python.exe`, macOS —
    `bin/python`."""
    if profile == MAC_PROFILE:
        return env_dir + "/bin/python"
    return env_dir + chr(92) + "Scripts" + chr(92) + "python.exe"

# GigaAM (MIT, salute-developers/GigaAM) — русское распознавание для CPU. На
# PyPI его нет; ставим архивом зафиксированного коммита, а не git+https: у
# пользователя может не быть git, а uv тянет git-зависимости через него.
# Смена коммита — осознанно, после проверки на замерах (docs/release-notes).
# SHA-256 архива сверяют и pip, и uv (фрагмент #sha256= в адресе): подменённый
# или перепакованный архив не установится. GitHub обещает стабильность
# архивов коммитов; если он их всё же перепакует, установка упадёт с
# несовпадением хеша — тогда хеш обновляют здесь и в pyproject.toml.
GIGAAM_COMMIT = "7447938d791c4f3e643386ee22c33777004293a5"
GIGAAM_SHA256 = "17c9a57a8c76659feb112b4a6299391757d137fc50e5375d5613c46c379f3653"
GIGAAM = (
    "gigaam @ https://github.com/salute-developers/GigaAM/archive/"
    f"{GIGAAM_COMMIT}.zip#sha256={GIGAAM_SHA256}"
)

PACKAGES = (
    "faster-whisper>=1.2,<2",
    # faster-whisper 1.2 передаёт av.open(metadata_errors=...), которого нет в
    # av 19: без пина чистая установка падает на первом же файле.
    "av>=11,<19",
    # community-1 (пайплайн диаризации) требует pyannote 4.x.
    "pyannote.audio>=4.0,<5",
    "transformers>=4.40,<6",
    "scipy>=1.11",
)
# Необязательные пакеты — отдельным extra `gigaam` и отдельным шагом
# установки (uv_gigaam_step): архив с GitHub может не скачаться или
# оказаться перепакованным (хеш не сойдётся) — движок ставится и без него,
# расшифровка тогда идёт Whisper.
OPTIONAL_PACKAGES = (GIGAAM,)
CUDA_RUNTIME = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")

# Порядок величин для честного предупреждения: скачивание идёт гигабайтами, и
# человек должен узнать об этом до нажатия, а не по счётчику трафика. CUDA —
# замер 01.10.2026: колёса окружения весят 4,4 ГБ (torch cu128 2,75 ГБ,
# nvidia-cudnn 0,74, nvidia-cublas 0,55).
DOWNLOAD_HINT_GB = {"cuda": 4.5, "cpu": 0.6, MAC_PROFILE: 0.6}

# Время расшифровки / длительность записи — по устройству и движку. Whisper —
# замер 30.09.2026 на 6-минутном фрагменте встречи
# (docs/2026-09-30-cpu-profile-bench.md), GigaAM — замер 02.10.2026 на том же
# фрагменте, весь пайплайн с диаризацией: CPU 172 с (распознавание 27 с,
# диаризация 144 с, выравнивания нет), CUDA 34 с. Оценка для UI, не обещание: на
# коротком фрагменте загрузка моделей весит больше, так что на длинных записях
# фактическое время обычно ниже.
SPEED_FACTOR = {
    "cuda": {"faster-whisper": 0.22, "gigaam": 0.09},
    "cpu": {"faster-whisper": 1.26, "gigaam": 0.48},
}
# Движок по умолчанию для профиля — им же считается оценка в мастере, пока
# настроек ещё нет.
DEFAULT_BACKEND = {"cuda": "faster-whisper", "cpu": "gigaam"}


def speed_factor(device: str, backend: str | None = None) -> float:
    """Коэффициент «время расшифровки / длительность записи». Неизвестное
    устройство — как CPU; движок не указан — движок профиля по умолчанию;
    любой не-GigaAM — Whisper."""
    profile = device if device in SPEED_FACTOR else "cpu"
    table = SPEED_FACTOR[profile]
    if not backend:
        backend = DEFAULT_BACKEND[profile]
    return table["gigaam" if backend == "gigaam" else "faster-whisper"]


def _backend(device: str) -> str:
    """Движок из настроек для устройства; без настроек — по умолчанию."""
    try:
        from meet import settings

        return settings.load().asr.backend_for(device)
    except Exception:
        return DEFAULT_BACKEND.get(device, "faster-whisper")


def _device(gpu_available: bool) -> str:
    """Устройство для оценки времени — без ctranslate2/torch: state() зовут в
    резиденте и в воркере установки, где загруженная DLL мешает pip обновить её.
    Явный выбор из настроек побеждает, «auto» опирается на nvidia-smi."""
    try:
        from meet import settings

        setting = settings.load().asr.device
    except Exception:
        setting = "auto"
    if setting in ("cuda", "cpu"):
        return setting
    return "cuda" if gpu_available else "cpu"


def estimate_seconds(duration_s: float, device: str, backend: str | None = None) -> float:
    return duration_s * speed_factor(device, backend)


def _free_gb(path: Path) -> float | None:
    """Свободное место на диске пути, ГБ; None — узнать нельзя.

    Отключённый диск или недоступная UNC-шара — штатная ситуация (ноутбук
    отстыкован от сети), а не сбой: снимок состояния без этого числа
    собирается, с исключением — нет вовсе."""
    target = path
    try:
        while not target.exists() and target != target.parent:
            target = target.parent
        return round(shutil.disk_usage(target).free / 1024**3, 1)
    except OSError:
        return None


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


def flavor_for(gpu_available: bool) -> str:
    """Сборка движка для этой машины: macOS — «Apple Silicon», иначе по карте."""
    from meet import plat

    if plat.is_macos():
        return MAC_PROFILE
    return "cuda" if gpu_available else "cpu"


def state() -> dict:
    """Состояние движка для настроек: чего не хватает и куда встанет."""
    components = []
    for module, title in COMPONENTS:
        item = {"module": module, "title": title, "installed": installed(module)}
        if module in OPTIONAL_COMPONENTS:
            item["optional"] = True
            if not item["installed"]:
                item["note"] = gigaam_install_error() or OPTIONAL_COMPONENTS[module]
        components.append(item)
    missing = [c["module"] for c in components
               if not c["installed"] and not c.get("optional")]
    card = gpu()
    device = _device(card["available"])
    return {
        "installed": not missing,
        "missing": missing,
        "components": components,
        "gpu": card,
        "flavor": flavor_for(card["available"]),
        "download_gb": DOWNLOAD_HINT_GB[flavor_for(card["available"])],
        "python": sys.executable,
        "target": str(Path(sys.prefix)),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "device": device,
        "backend": _backend(device),
        "speed_factor": speed_factor(device, _backend(device)),
        "disk_free_gb": _free_gb(Path(sys.prefix)),
    }


def install_steps(flavor: str | None = None) -> list[list[str]]:
    """Команды установки по шагам.

    Два шага, а не один: torch живёт на своём индексе, и смешивать его с
    остальными пакетами в одной команде — верный способ утянуть не ту сборку.
    """
    flavor = flavor or flavor_for(gpu()["available"])
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    index = torch_index(flavor)
    steps = [pip + ["torch"] + (["--index-url", index] if index else [])]
    extras = list(PACKAGES) + (list(CUDA_RUNTIME) if flavor == "cuda" else [])
    steps.append(pip + extras)
    steps.append(pip + list(OPTIONAL_PACKAGES))
    return steps


def gigaam_install_error(prefix: Path | None = None) -> str | None:
    """Почему GigaAM не установилась («GigaAM не установилась: … —
    используется Whisper») или None. Причину пишет установщик в окружение."""
    path = Path(prefix or sys.prefix) / GIGAAM_ERROR_FILE
    try:
        reason = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return f"GigaAM не установилась: {reason} — используется Whisper" if reason else None


def install(flavor: str | None = None, on_line=None, runner=None) -> int:
    """Поставить движок, отдавая вывод построчно.

    Прогресса в процентах здесь нет и не будет: pip не сообщает общий объём
    заранее. Показываем, что именно сейчас качается, — это честнее выдуманной
    шкалы (тот же принцип, что у ступеней расшифровки). Последний шаг
    (GigaAM) необязателен: его сбой не валит установку.
    """
    run = runner or _run
    steps = install_steps(flavor)
    for step in steps[:-1]:
        code = run(step, on_line)
        if code != 0:
            return code
    if run(steps[-1], on_line) != 0 and on_line:
        on_line("GigaAM не установилась — расшифровка пойдёт Whisper")
    return 0


def profile_for(gpu: dict) -> str:
    """Профиль зависимостей по снимку `gpu()`: карта видна — cuda, иначе cpu;
    на macOS — всегда «Apple Silicon» (`mac`)."""
    return flavor_for(bool(gpu.get("available")))


def estimate_text(duration_s: float, profile: str) -> str:
    """«38 мин встречи ≈ 18 мин обработки» — для экрана выбора профиля."""
    def minutes(seconds: float) -> int:
        return max(1, round(seconds / 60))

    return (
        f"{minutes(duration_s)} мин встречи ≈ "
        f"{minutes(estimate_seconds(duration_s, profile))} мин обработки"
    )


def uv_steps(uv: str, env_dir: str, wheel: str, profile: str,
             constraints: str | None = None) -> list[list[str]]:
    """Команды установки колеса в приватный venv через uv.

    Путь к python — `python_path` (склейкой строк: шаги воспроизводит
    инсталлятор на Rust, фикстуры в tests/fixtures совпадают на любой ОС).
    Профиль `mac` (Apple Silicon) ставит torch с PyPI, без `--index-url`.

    `constraints` — файл точных версий всего дерева (`uv pip compile` при
    сборке установщика, ресурс `constraints-<профиль>.txt`): без него каждый
    пользователь получал бы те версии, что вышли к дню установки.
    """
    index = torch_index(profile)
    pip = [uv, "pip", "install", "--python", python_path(env_dir, profile)]
    pinned = ["--constraint", constraints] if constraints else []
    return [
        [uv, "python", "install", "3.12"],
        [uv, "venv", "--python", "3.12", env_dir],
        pip + list(TORCH_SPECS) + (["--index-url", index] if index else []) + pinned,
        pip + [f"{wheel}[engine-{profile}]"] + pinned,
    ]


def uv_gigaam_step(uv: str, env_dir: str, wheel: str,
                   constraints: str | None = None, profile: str = "cpu") -> list[str]:
    """Необязательный шаг установщика после uv_steps: extra `gigaam` того же
    колеса. Зеркало engine.rs `uv_gigaam_step` (фикстуры uv_gigaam_step*.json).
    Профиль нужен только раскладке venv (`python_path`)."""
    python = python_path(env_dir, profile)
    pinned = ["--constraint", constraints] if constraints else []
    return [uv, "pip", "install", "--python", python, f"{wheel}[gigaam]"] + pinned


def _run(argv: list[str], on_line) -> int:
    from meet import netproxy

    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # Прокси из настроек (llm.proxy / прокси Windows): uv и pip сами
            # системный прокси Windows не читают.
            env=netproxy.settings_env(),
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=plat.no_window(),
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
