"""Замер диаризации на Mac (Apple Silicon): где уходит время и что быстрее —
MPS или процессор. Одна команда, ~5 минут, ничего не меняет и не ставит.

Запуск — интерпретатором установленного движка Meet (в нём torch и pyannote):

    "$(ls -d ~/Library/Application\\ Support/meet/engine/*/bin/python | tail -1)" diar_bench_mac.py

Что делает:

1. Время импорта torch и pyannote, чтения токена из связки ключей и загрузки
   модели диаризации: с диска (как Meet 0.3.3) и по имени репозитория с
   проверкой на huggingface.co (как Meet до 0.3.3; `--no-online` — пропустить).
2. Звук — пример из пакета pyannote (30 с, два голоса), повторенный до 5 минут:
   запись встречи никуда не уходит. Своя запись — `--wav файл.wav` (16 кГц моно).
3. Варианты по очереди, `--rounds` раз: MPS как в 0.3.2 (штатные голоса,
   пачка 32), MPS как в 0.3.3 (голоса за один проход на окно, пачка 16),
   процессор как в 0.3.3 (потоков — производительных ядер), сегментация на
   процессоре + голоса на MPS. `--full` добавляет процессор как в 0.3.2 (долго).
4. По каждому прогону — сегментация / голоса / кластеризация, секунд на
   секунду звука и расхождение с MPS 0.3.2 (DER, %; 0.00 — тот же результат).

Итог печатается таблицей и сохраняется в JSON (путь — в конце): его и
пришлите. В сеть ходит только шаг 1 по имени репозитория; телеметрия pyannote
выключена.
"""

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

REPO = "pyannote/speaker-diarization-community-1"
SAMPLE_RATE = 16000


def say(text: str = "") -> None:
    print(text, flush=True)


def sysctl(key: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def cache_root() -> Path:
    """Кэш Hugging Face — по тем же правилам, что meet.models.cache_root."""
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(env):
            return Path(os.environ[env])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def local_snapshot() -> Path | None:
    folder = cache_root() / ("models--" + REPO.replace("/", "--"))
    try:
        ref = (folder / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    snap = folder / "snapshots" / ref
    return snap if ref and (snap / "config.yaml").is_file() else None


def read_token() -> tuple[str | None, float, str]:
    """Токен HF так, как его берёт Meet (связка ключей), и время чтения."""
    t = time.perf_counter()
    try:
        from meet import credentials

        token, where = credentials.get_hf_token(), "meet.credentials (связка ключей)"
    except Exception:
        token, where = os.environ.get("HF_TOKEN"), "HF_TOKEN"
    return token, time.perf_counter() - t, where


def load_audio(path: str | None, seconds: float):
    import numpy as np
    import torch

    if path:
        with wave.open(path, "rb") as wf:
            if wf.getframerate() != SAMPLE_RATE or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                sys.exit(f"{path}: нужен WAV 16 кГц моно 16 бит "
                         "(ffmpeg -i запись -ac 1 -ar 16000 -sample_fmt s16 out.wav)")
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        source = Path(path).name
    else:
        import pyannote.audio

        sample = Path(pyannote.audio.__file__).parent / "sample" / "sample.wav"
        with wave.open(str(sample), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        pcm = np.tile(pcm, math.ceil(seconds * SAMPLE_RATE / len(pcm)))[: int(seconds * SAMPLE_RATE)]
        source = f"пример pyannote x{math.ceil(seconds / 30)}"
    waveform = torch.from_numpy(pcm.astype(np.float32) / 32768.0).unsqueeze(0)
    return waveform, source


# --- голоса за один проход на окно: та же правка, что meet.diarize 0.3.3 -----


def shared_embeddings(self, file, binary_segmentations, exclude_overlap=False, hook=None):
    """Копия meet.diarize._shared_embeddings (pyannote-audio#2050): в движке
    0.3.2 её ещё нет."""
    import numpy as np
    import torch

    duration = binary_segmentations.sliding_window.duration
    num_chunks, num_frames, num_speakers = binary_segmentations.data.shape
    raw = binary_segmentations.data
    if exclude_overlap:
        min_num_samples = self._embedding.min_num_samples
        num_samples = duration * self._embedding.sample_rate
        min_num_frames = math.ceil(num_frames * min_num_samples / num_samples)
        clean = raw * (np.sum(raw, axis=2, keepdims=True) < 2)
    else:
        min_num_frames = -1
        clean = raw
    masks = np.nan_to_num(raw, nan=0.0).astype(np.float32)
    clean = np.nan_to_num(clean, nan=0.0).astype(np.float32)
    use_clean = np.sum(clean, axis=1) > min_num_frames
    used = np.where(use_clean[:, None, :], clean, masks).transpose(0, 2, 1).copy()
    chunks = [chunk for chunk, _ in binary_segmentations]
    todo = [c for c in range(num_chunks) if used[c].any()]
    batch_size = self.embedding_batch_size
    total = math.ceil(len(todo) / batch_size)
    out = np.full((num_chunks, num_speakers, self._embedding.dimension), np.nan, dtype=np.float32)
    if hook is not None:
        hook("embeddings", None, total=total, completed=0)
    for i, start in enumerate(range(0, len(todo), batch_size), 1):
        idx = todo[start:start + batch_size]
        waveforms = torch.vstack([self._audio.crop(file, chunks[c], mode="pad")[0][None] for c in idx])
        batch = self._embedding(waveforms, masks=torch.from_numpy(used[idx]))
        out[idx] = batch
        if hook is not None:
            hook("embeddings", batch, total=total, completed=i)
    valid = ~np.isnan(out).any(axis=2)
    out[~valid] = out[valid][0] if valid.any() else 0.0
    return out


def fast_embeddings_impl():
    """Правка из установленного Meet (0.3.3+), иначе копия выше."""
    try:
        from meet.diarize import _shared_embeddings

        return _shared_embeddings, "meet.diarize"
    except Exception:
        return shared_embeddings, "копия в скрипте"


# --- прогон ---------------------------------------------------------------------


def run_variant(pipe, stock_get_embeddings, fast_impl, waveform, variant, default_threads):
    import torch

    seg_dev, emb_dev = torch.device(variant["seg"]), torch.device(variant["emb"])
    pipe._segmentation.to(seg_dev)
    pipe._embedding.to(emb_dev)
    threads = variant.get("threads") or default_threads
    torch.set_num_threads(threads)
    pipe.embedding_batch_size = variant["batch"]
    if variant["fast"]:
        pipe.get_embeddings = types.MethodType(fast_impl, pipe)
    else:
        pipe.get_embeddings = stock_get_embeddings
    marks = []

    def hook(step_name, step_artifact=None, file=None, total=None, completed=None):
        marks.append((step_name, time.perf_counter()))

    t0 = time.perf_counter()
    c0 = time.process_time()
    result = pipe({"waveform": waveform, "sample_rate": SAMPLE_RATE}, hook=hook)
    end = time.perf_counter()
    first = lambda n: next((t for s, t in marks if s == n), None)  # noqa: E731
    last = lambda n: next((t for s, t in reversed(marks) if s == n), None)  # noqa: E731
    seg, emb, emb_end = first("segmentation"), first("embeddings"), last("embeddings")
    audio_s = waveform.shape[1] / SAMPLE_RATE
    turns = [(s.start, s.end, label) for s, _, label in result.speaker_diarization.itertracks(yield_label=True)]
    return {
        "name": variant["name"], "threads": threads, "total": end - t0, "cpu": time.process_time() - c0,
        "s_per_s": (end - t0) / audio_s,
        "before_segmentation": (seg or end) - t0,
        "segmentation": ((emb or end) - seg) if seg else 0.0,
        "embeddings": (emb_end - emb) if emb else 0.0,
        "clustering": (end - emb_end) if emb else 0.0,
        "speakers": len(result.speaker_diarization.labels()),
        "turns": turns,
    }


def der(reference, hypothesis) -> float | None:
    try:
        from pyannote.core import Annotation, Segment
        from pyannote.metrics.diarization import DiarizationErrorRate
    except Exception:
        return None

    def annotation(turns):
        a = Annotation()
        for i, (s, e, label) in enumerate(turns):
            a[Segment(s, e), i] = label
        return a

    return 100.0 * DiarizationErrorRate()(annotation(reference), annotation(hypothesis))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wav", help="своя запись: WAV 16 кГц моно (по умолчанию — пример pyannote)")
    ap.add_argument("--minutes", type=float, default=5.0, help="длина примера, минут (по умолчанию 5)")
    ap.add_argument("--rounds", type=int, default=2, help="сколько раз прогнать варианты (по умолчанию 2)")
    ap.add_argument("--no-online", action="store_true", help="не замерять загрузку с huggingface.co")
    ap.add_argument("--full", action="store_true", help="добавить процессор как в 0.3.2 (долго)")
    ap.add_argument("--out", help="куда сохранить JSON (по умолчанию — Рабочий стол)")
    args = ap.parse_args()
    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv[1:]}

    t = time.perf_counter()
    import torch

    report["import_torch_s"] = time.perf_counter() - t
    t = time.perf_counter()
    import pyannote.audio
    from pyannote.audio import Pipeline

    report["import_pyannote_s"] = time.perf_counter() - t
    mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    perf = sysctl("hw.perflevel0.physicalcpu")
    report["machine"] = {
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "macos": platform.mac_ver()[0], "arch": platform.machine(),
        "cores": sysctl("hw.physicalcpu"), "performance_cores": perf,
        "memory_gb": round(int(sysctl("hw.memsize") or 0) / 2**30, 1),
        "python": platform.python_version(), "torch": torch.__version__,
        "pyannote.audio": pyannote.audio.__version__, "mps": mps,
        "torch_threads_default": torch.get_num_threads(),
    }
    say(f"Машина: {report['machine']['cpu']}, macOS {report['machine']['macos']}, "
        f"ядер {report['machine']['cores']} (производительных {perf}), "
        f"{report['machine']['memory_gb']} ГБ; torch {torch.__version__}, "
        f"pyannote {pyannote.audio.__version__}, MPS {'есть' if mps else 'нет'}")
    say(f"Импорт: torch {report['import_torch_s']:.1f} с, pyannote {report['import_pyannote_s']:.1f} с")

    snap = local_snapshot()
    if snap is None:
        say("Модели диаризации нет в кэше Hugging Face: сначала скачайте её в Meet (Настройки -> Модели).")
        return 2
    t = time.perf_counter()
    pipe = Pipeline.from_pretrained(snap)
    report["load_from_disk_s"] = time.perf_counter() - t
    say(f"Загрузка модели с диска (как 0.3.3): {report['load_from_disk_s']:.1f} с")

    if not args.no_online:
        token, token_s, where = read_token()
        report["token_read_s"], report["token_from"] = token_s, where
        say(f"Чтение токена ({where}): {token_s:.1f} с" + ("" if token else " - токена нет"))
        say("Загрузка по имени репозитория с проверкой на huggingface.co (как до 0.3.3)...")
        t = time.perf_counter()
        try:
            online = Pipeline.from_pretrained(REPO, token=token)
            report["load_online_s"] = time.perf_counter() - t
            report["load_online_ok"] = online is not None
            del online
        except Exception as e:
            report["load_online_s"] = time.perf_counter() - t
            report["load_online_error"] = type(e).__name__
        say(f"  {report['load_online_s']:.1f} с" + (f" ({report['load_online_error']})"
                                                     if "load_online_error" in report else ""))

    waveform, source = load_audio(args.wav, args.minutes * 60)
    audio_s = waveform.shape[1] / SAMPLE_RATE
    report["audio"] = {"source": source, "seconds": audio_s}
    say(f"Звук: {source}, {audio_s:.0f} с")

    fast_impl, fast_from = fast_embeddings_impl()
    report["fast_embeddings_from"] = fast_from
    stock = pipe.get_embeddings
    default_threads = torch.get_num_threads()
    cpu_threads = int(perf) if perf and perf.isdigit() else min(default_threads, 8)
    variants = []
    if mps:
        variants += [
            {"name": "MPS, как 0.3.2", "seg": "mps", "emb": "mps", "fast": False, "batch": 32},
            {"name": "MPS, 0.3.3", "seg": "mps", "emb": "mps", "fast": True, "batch": 16},
            {"name": "сегм. CPU + голоса MPS, 0.3.3", "seg": "cpu", "emb": "mps", "fast": True, "batch": 16},
        ]
    variants.append({"name": "CPU, 0.3.3", "seg": "cpu", "emb": "cpu", "fast": True, "batch": 16,
                     "threads": cpu_threads})
    if args.full or not mps:
        variants.append({"name": "CPU, как 0.3.2", "seg": "cpu", "emb": "cpu", "fast": False, "batch": 32})
    reference_name = variants[0]["name"] if mps else "CPU, как 0.3.2"

    runs, outputs = [], {}
    for r in range(args.rounds):
        for variant in variants:
            say(f"[{r + 1}/{args.rounds}] {variant['name']}...")
            try:
                st = run_variant(pipe, stock, fast_impl, waveform, variant, default_threads)
            except Exception as e:
                say(f"  не прошло: {type(e).__name__}: {str(e)[:200]}")
                runs.append({"name": variant["name"], "round": r, "error": f"{type(e).__name__}: {str(e)[:300]}"})
                continue
            st["round"] = r
            outputs.setdefault(variant["name"], st.pop("turns"))
            runs.append(st)
            say(f"  всего {st['total']:.1f} с ({st['s_per_s']:.3f} с/с): сегментация {st['segmentation']:.1f}, "
                f"голоса {st['embeddings']:.1f}, кластеризация {st['clustering']:.1f}, спикеров {st['speakers']}")
    torch.set_num_threads(default_threads)

    quality = {}
    if reference_name in outputs:
        for name, turns in outputs.items():
            quality[name] = der(outputs[reference_name], turns)
    report.update(runs=runs, quality_der_percent=quality, reference=reference_name, outputs=outputs)

    say()
    say(f"{'вариант':32} {'прогон':>6} {'всего, с':>9} {'с/с':>6} {'сегм.':>6} {'голоса':>7} {'класт.':>6} {'DER %':>6}")
    for st in runs:
        if "error" in st:
            say(f"{st['name']:32} {st['round'] + 1:>6} {'ошибка: ' + st['error'][:40]}")
            continue
        d = quality.get(st["name"])
        say(f"{st['name']:32} {st['round'] + 1:>6} {st['total']:9.1f} {st['s_per_s']:6.3f} "
            f"{st['segmentation']:6.1f} {st['embeddings']:7.1f} {st['clustering']:6.1f} "
            f"{'' if d is None else f'{d:6.2f}':>6}")
    say(f"DER - расхождение с «{reference_name}» (0.00 - тот же результат).")

    out = Path(args.out) if args.out else (Path.home() / "Desktop" if (Path.home() / "Desktop").is_dir()
                                            else Path.cwd())
    if out.is_dir():
        out = out / f"meet-diar-bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"Сохранено: {out} - пришлите этот файл.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
