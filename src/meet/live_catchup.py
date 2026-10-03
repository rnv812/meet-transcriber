"""Догонялка ассистента, включённого посреди записи.

Ассистент слышит запись с момента подключения (отвод `meet.pcm_tap`), а всё,
что было раньше, лежит в дорожках папки записи (`sys.opus`, `mic.opus`) — они
растут, пока запись идёт. Догонялка распознаёт этот кусок в фоне тем же живым
распознаванием (GigaAM короткими окнами), чтобы сводка и подсказки знали
начало встречи:

* `plan()` — что догонять: по каждой дорожке от `max(0, позиция − CAP_S,
  конец уже услышанного)` до позиции подключения. Больше CAP_S не берём:
  старое и так войдёт в итоги по полной расшифровке.
* `PcmReader` — ffmpeg читает кусок растущего Ogg/Opus (страницы на диске
  сбрасываются каждые 0,5 с) в 16 кГц моно порциями — без загрузки всего
  куска в память.

Сами окна режет и распознаёт `meet.live.LiveEngine` (`start_catchup`):
между окнами живого звука, чтобы живая лента не отставала.
"""

import re
import subprocess
from pathlib import Path

from meet import plat

CAP_S = 30 * 60  # догоняем не больше последних 30 минут
MIN_S = 1.0  # кусок короче не догоняем
RATE = 16000
# Дорожки записи → ключи дорожек живого движка (`LiveEngine.SPEAKERS`).
TRACK_FILES = {"sys.wav": "sys.opus", "mic.wav": "mic.opus"}

_STAMP = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")


def line_time(line: str) -> float | None:
    """Секунды из `[чч:мм:сс]` в начале строки ленты; нет метки — None."""
    m = _STAMP.match(line)
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return float(h * 3600 + mi * 60 + s)


def chronological(lines: list[str]) -> list[str]:
    """Строки ленты по времени (устойчиво): строка без метки идёт за
    предыдущей, пустые выбрасываются."""
    keyed: list[tuple[float, int, str]] = []
    last = 0.0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        t = line_time(line)
        if t is not None:
            last = t
        keyed.append((last, i, line))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [line for _, _, line in keyed]


def heard_until(transcript: Path) -> float | None:
    """Докуда уже есть лента от прошлого включения ассистента в этой записи
    (последняя метка времени в `live_transcript.md`); ленты нет — None."""
    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return None
    times = [t for t in (line_time(line) for line in text.splitlines()) if t is not None]
    return max(times) if times else None


def plan(positions: dict[str, float], folder: Path, *, heard: float | None = None,
         cap_s: float = CAP_S) -> dict:
    """Что догонять. `positions` — с какой секунды дорожки (ключ движка
    `sys.wav`/`mic.wav`) идёт живой звук; `heard` — докуда уже есть лента
    прошлого включения. → {"tracks": {ключ: (путь, начало, конец)},
    "from_t", "to_t", "total_s", "capped"} (`capped` — начало записи (или
    прошлой ленты) не войдёт: дальше CAP_S)."""
    tracks: dict[str, tuple[Path, float, float]] = {}
    capped = False
    for key, pos in positions.items():
        name = TRACK_FILES.get(key)
        if name is None:
            continue
        path = Path(folder) / name
        floor = max(0.0, float(heard or 0.0))
        start = max(floor, pos - cap_s)
        if start > floor + MIN_S:
            capped = True
        if pos - start < MIN_S or not path.is_file():
            continue
        tracks[key] = (path, start, float(pos))
    starts = [s for _, s, _ in tracks.values()]
    ends = [e for _, _, e in tracks.values()]
    return {"tracks": tracks,
            "from_t": min(starts) if starts else None,
            "to_t": max(ends) if ends else None,
            "total_s": sum(e - s for _, s, e in tracks.values()),
            "capped": capped}


def _decode_argv(path: Path, start_s: float, end_s: float) -> list[str]:
    # -ss до -i: быстрый переход по страницам Ogg; -t — длина куска. Растущий
    # файл ffmpeg читает до последней целой страницы и выходит.
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{start_s:.3f}", "-i", str(path), "-t", f"{max(0.0, end_s - start_s):.3f}",
            "-f", "s16le", "-ac", "1", "-ar", str(RATE), "pipe:1"]


class PcmReader:
    """Кусок дорожки → 16 кГц моно float32 порциями (`read`). `spawn(argv)`
    подменяется в тестах (что угодно с `.stdout.read`, `.kill`, `.wait`)."""

    def __init__(self, path: Path, start_s: float, end_s: float, spawn=None) -> None:
        argv = _decode_argv(path, start_s, end_s)
        self._proc = (spawn or self._spawn)(argv)
        self.eof = False

    @staticmethod
    def _spawn(argv: list[str]):
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, creationflags=plat.no_window())

    def read(self, seconds: float):
        """До `seconds` секунд звука (меньше — у конца куска); конец — пустой
        массив и `eof`."""
        import numpy as np

        want = max(2, int(seconds * RATE) * 2)
        chunks: list[bytes] = []
        got = 0
        while got < want and not self.eof:
            data = self._proc.stdout.read(want - got)
            if not data:
                self.eof = True
                break
            chunks.append(data)
            got += len(data)
        raw = b"".join(chunks)
        if len(raw) % 2:
            raw = raw[:-1]
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.stdout.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass
