"""Замер шагов движка на своей записи (0.5.1): время, пик памяти и сверка с
прошлым прогоном — что ускорение не стоило качества.

Запуск — интерпретатором установленного движка Meet, из корня репозитория
(PYTHONPATH=src — проверяется код из исходников, а не установленный):

    PYTHONPATH=src <движок>/Scripts/python scripts/bench_engine.py запись.wav --save bench/до
    PYTHONPATH=src <движок>/Scripts/python scripts/bench_engine.py запись.wav --ref bench/до

Шаги (`--steps`, по умолчанию все): gigaam, whisper, align (выравнивание слов
текста Whisper, без него — GigaAM), diarize, diarize-fast («Быстрее разделять на
спикеров»). По каждому — секунд на час звука, пик частной памяти процесса и
видеопамяти; с `--ref` — доля изменившихся слов и сдвинутых больше чем на кадр
(20 мс), у разметки спикеров — расхождение (DER) и число спикеров.

Пороги — `scripts/bench_targets.json`: профиль по названию видеокарты (или
`--profile`); превышено — код 1. Запись никуда не уходит, в отчёт — только
числа; `--save` пишет слова и разметку в указанную папку (на диске у вас).
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = ("gigaam", "whisper", "align", "diarize", "diarize-fast")
SHIFT_S = 0.02  # кадр выравнивания
TARGETS = ROOT / "scripts" / "bench_targets.json"


# --- расчёты (проверяются тестами без движка) ------------------------------------------


def der(ref, hyp, step: float = 0.1) -> float:
    """Расхождение разметок спикеров: (пропуск + лишняя речь + путаница) / речь
    эталона, кадрами по `step` с; метки сопоставляются оптимально."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    if not ref:
        return 0.0
    n = int(max(t[1] for t in list(ref) + list(hyp)) / step) + 2

    def frames(turns):
        labels = sorted({t[2] for t in turns})
        m = np.zeros((max(1, len(labels)), n), dtype=bool)
        for s, e, lab in turns:
            m[labels.index(lab), int(round(s / step)):int(round(e / step))] = True
        return m

    r, h = frames(ref), frames(hyp)
    overlap = r.astype(np.int64) @ h.T.astype(np.int64)
    ri, hi = linear_sum_assignment(-overlap)
    mapped = np.zeros_like(r)
    for a, b in zip(ri, hi):
        mapped[a] = h[b]
    n_ref, n_hyp = r.sum(0), h.sum(0)
    correct = (r & mapped).sum(0)
    errors = (np.maximum(n_ref - n_hyp, 0).sum() + np.maximum(n_hyp - n_ref, 0).sum()
              + (np.minimum(n_ref, n_hyp) - correct).sum())
    return float(errors / max(1, n_ref.sum()))


def word_report(ref, hyp) -> dict:
    """Слова (текст, начало, конец): доля изменившихся (вставки, замены,
    пропуски — от числа слов эталона) и доля совпавших, сдвинутых больше SHIFT_S."""
    if not ref:
        return {"changed": 0.0 if not hyp else 1.0, "shifted": 0.0}
    sm = difflib.SequenceMatcher(None, [w[0] for w in ref], [w[0] for w in hyp], autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    pairs = [(ref[i + k], hyp[j + k]) for i, j, size in sm.get_matching_blocks() for k in range(size)]
    shifted = sum(1 for a, b in pairs if max(abs(a[1] - b[1]), abs(a[2] - b[2])) > SHIFT_S)
    return {"changed": changed / len(ref), "shifted": shifted / max(1, len(pairs))}


def check(results: dict, targets: dict) -> list[str]:
    """Превышения порогов: `max_<мера>` шага против `<мера>` его результата."""
    failures = []
    for step, limits in targets.items():
        got = results.get(step) or {}
        for key, limit in limits.items():
            measure = key.removeprefix("max_")
            value = got.get(measure)
            if value is not None and value > limit:
                failures.append(f"{step}: {measure} {value:.3g} > {limit:g}")
    return failures


# --- замер -------------------------------------------------------------------------------


class Peak:
    """Пик частной памяти процесса (Windows — commit, иначе RSS) за блок, ГБ.
    На Windows в неё входит и видеопамять (2 ГБ на карте — +2 ГБ частной):
    это та же граница выделения, о которую задача падает с MemoryError."""

    def __enter__(self):
        import psutil

        self._proc = psutil.Process()
        self.gb = self._now()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def _now(self) -> float:
        info = self._proc.memory_info()
        return getattr(info, "private", info.rss) / 2**30

    def _watch(self):
        while not self._stop.wait(0.1):
            self.gb = max(self.gb, self._now())

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.gb = max(self.gb, self._now())


def _wav16k(path: Path) -> Path:
    """16 кГц моно WAV: как есть или копия через ffmpeg во временной папке."""
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getframerate() == 16000 and wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                return path
    except (wave.Error, EOFError):
        pass
    out = Path(tempfile.mkdtemp(prefix="meet-bench-")) / "audio16k.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out)], check=True)
    return out


def _words(segments) -> list:
    return [(w.text, round(w.start, 3), round(w.end, 3)) for s in segments for w in (s.words or [])]


def _gpu():
    try:
        import torch

        return torch if torch.cuda.is_available() else None
    except ImportError:
        return None


def run_step(name: str, wav: Path, device: str, cache: dict):
    """Шаг → (данные для сверки, число спикеров или None)."""
    from meet import align, asr, diarize, gigaam_asr

    if name == "gigaam":
        model = gigaam_asr.load(gigaam_asr.MODEL_NAME, device, on_line=lambda s: None)
        cache["gigaam"] = gigaam_asr.transcribe(wav, model=model)
        del model
        return _words(cache["gigaam"]), None
    if name == "whisper":
        cache["whisper"] = asr.transcribe_wav(wav, choice=asr.Choice("faster-whisper", device))
        return _words(cache["whisper"]), None
    if name == "align":
        # Текст GigaAM — он воспроизводим, и сверка с прошлым прогоном честная;
        # Whisper от прогона к прогону расходится сам с собой на 24–28% слов.
        segments = cache.get("gigaam") or cache.get("whisper")
        if segments is None:
            raise SystemExit("align: нужен текст — шаг whisper или gigaam раньше")
        return _words(align.align_segments(segments, wav, device=device)), None
    fast = name == "diarize-fast"
    result = diarize.diarize_wav(wav, fast=fast)
    turns = [(float(s), float(e), str(lab)) for s, e, lab in result.turns]
    return turns, len({t[2] for t in turns})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--steps", default=",".join(STEPS))
    parser.add_argument("--device", default=None, help="cuda или cpu (по умолчанию — как у Meet)")
    parser.add_argument("--save", type=Path, help="папка: сохранить слова и разметку для будущей сверки")
    parser.add_argument("--ref", type=Path, help="папка прошлого прогона (--save) для сверки качества")
    parser.add_argument("--profile", help="профиль порогов из bench_targets.json")
    parser.add_argument("--json", type=Path, help="записать итог в файл")
    args = parser.parse_args(argv)

    from meet import asr, models

    models.use_meet_cache()
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = sorted(set(steps) - set(STEPS))
    if unknown:
        parser.error(f"неизвестные шаги: {', '.join(unknown)}")
    wav = _wav16k(args.audio)
    with wave.open(str(wav), "rb") as wf:
        hours = wf.getnframes() / wf.getframerate() / 3600
    device = args.device or asr.torch_device()
    torch = _gpu()
    gpu_name = torch.cuda.get_device_name(0) if torch is not None and device == "cuda" else "-"
    print(f"звук {hours * 60:.1f} мин, устройство {device} ({gpu_name})", flush=True)

    results: dict = {}
    cache: dict = {}
    from meet import memory

    for name in steps:
        # Пик шага, а не накопленный: на Windows видеопамять входит в частную
        # память процесса, а кэш torch держит её и после удаления модели.
        memory.release(torch)
        if torch is not None:
            torch.cuda.reset_peak_memory_stats()
        t = time.perf_counter()
        with Peak() as peak:
            data, speakers = run_step(name, wav, device, cache)
        took = time.perf_counter() - t
        row = {"seconds": round(took, 1), "s_per_hour": round(took / hours, 1), "private_gb": round(peak.gb, 2)}
        if torch is not None:
            row["gpu_gb"] = round(torch.cuda.max_memory_reserved() / 2**30, 2)
        if speakers is not None:
            row["speakers"] = speakers
        if name == "whisper" and cache.get("gigaam"):
            # Whisper невоспроизводим (повторы декодирования с температурой) —
            # его качество меряется расхождением с текстом GigaAM того же прогона.
            row["gigaam_diff"] = round(word_report(_words(cache["gigaam"]), data)["changed"], 4)
        if args.save:
            args.save.mkdir(parents=True, exist_ok=True)
            (args.save / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        ref_file = args.ref / f"{name}.json" if args.ref else None
        if ref_file is not None and ref_file.exists():
            ref = [tuple(x) for x in json.loads(ref_file.read_text(encoding="utf-8"))]
            if name.startswith("diarize"):
                row["der"] = round(der(ref, data), 4)
                row["ref_speakers"] = len({t[2] for t in ref})
            else:
                row.update({k: round(v, 4) for k, v in word_report(ref, data).items()})
        results[name] = row
        print(f"{name}: " + ", ".join(f"{k} {v}" for k, v in row.items()), flush=True)

    targets = json.loads(TARGETS.read_text(encoding="utf-8"))["profiles"]
    profile = args.profile or next((k for k, p in targets.items() if p.get("gpu") and p["gpu"] in gpu_name), None)
    failures = check(results, targets[profile]["steps"]) if profile in targets else []
    print(f"пороги: {profile or 'нет профиля для этой машины'}" + (f" — превышено: {'; '.join(failures)}"
                                                                    if failures else ""), flush=True)
    if args.json:
        args.json.write_text(json.dumps({"device": device, "gpu": gpu_name, "hours": hours, "steps": results,
                                         "profile": profile, "failures": failures}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    sys.exit(main())
