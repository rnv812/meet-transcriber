"""Замер офлайн-пайплайна расшифровки по ступеням, с выбором устройства.

Повторяет `_transcribe_two_track` из `meet/transcribe.py`, добавляя таймеры на
каждую ступень. Модули `meet` не изменяются: диаризация собирается здесь из её
же деталей (в `diarize_wav` устройство прибито к cuda), ASR — либо штатной
`transcribe_wav`, либо своей загрузкой модели на CPU.

    python scripts/bench_pipeline.py recordings/<папка> --label desktop-cuda
    python scripts/bench_pipeline.py recordings/<папка> --label laptop-diar \
        --device cpu --asr skip --threads 4

Результат: `bench_<label>_timings.json` и `bench_<label>_transcript.md`
в папке записи (или в --out).
"""
import argparse
import json
import os
import platform
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Stopwatch:
    """Тайминги ступеней по порядку прохождения."""

    def __init__(self) -> None:
        self.stages: list[tuple[str, float]] = []
        self._t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str):
        print(f">>> {name}", flush=True)
        t0 = time.perf_counter()
        yield
        dt = time.perf_counter() - t0
        self.stages.append((name, dt))
        print(f"<<< {name}: {dt:.1f} с", flush=True)

    def total(self) -> float:
        return time.perf_counter() - self._t0


def wav_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def diarize(wav: Path, device: str, num_speakers: int | None, exclusive: bool):
    """То же, что `meet.diarize.diarize_wav`, но с выбором устройства.

    При device="cuda" вызов совпадает со штатным — замер сопоставим."""
    import torch
    from pyannote.audio import Pipeline

    from meet.diarize import DIARIZATION_MODEL, _load_wav, _to_diarization

    # Токен нужен только для скачивания: при готовом кэше и HF_HUB_OFFLINE=1
    # pyannote берёт модель локально, и токен не требуется.
    token = os.environ.get("HF_TOKEN")
    if not token and os.environ.get("HF_HUB_OFFLINE") != "1":
        raise SystemExit(
            "Нет HF_TOKEN (нужен для скачивания моделей pyannote). "
            "Либо положить модели в кэш HF и запускать с HF_HUB_OFFLINE=1."
        )
    pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    pipe.to(torch.device(device))
    waveform, rate = _load_wav(wav)
    result = pipe(
        {"waveform": waveform, "sample_rate": rate},
        num_speakers=num_speakers,
    )
    return _to_diarization(result, exclusive=exclusive)


def asr_cpu(wav: Path, hotwords: str | None, compute_type: str, threads: int | None):
    """faster-whisper на CPU: та же модель и те же параметры декодирования,
    что в `meet.asr.transcribe_wav`, только устройство другое."""
    from faster_whisper import WhisperModel

    from meet.asr import MODEL_NAME, _segments_from_whisper

    model = WhisperModel(
        MODEL_NAME,
        device="cpu",
        compute_type=compute_type,
        cpu_threads=threads or 0,
    )
    segments, _ = model.transcribe(
        str(wav),
        language="ru",
        vad_filter=True,
        word_timestamps=True,
        hotwords=hotwords,
    )
    result = _segments_from_whisper(segments)
    del model
    return result


def run(args) -> dict:
    from meet.audio import to_wav16k
    from meet.diarize import split_by_speaker
    from meet.interleave import interleave_tracks
    from meet.output import speaker_names, to_markdown
    from meet.transcribe import (
        _apply_names,
        _find_track,
        _folder_dates,
        _match_names,
        _maybe_align,
        _load_hotwords,
    )

    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"Не папка записи: {folder}")
    sys_src, mic_src = _find_track(folder, "sys"), _find_track(folder, "mic")
    if not (sys_src and mic_src):
        raise SystemExit(f"В {folder} нет дорожек sys/mic")

    out_dir = Path(args.out) if args.out else folder
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_dir = Path(args.wav_dir) if args.wav_dir else out_dir
    wav_dir.mkdir(parents=True, exist_ok=True)

    hotwords = _load_hotwords(None)
    sw = Stopwatch()

    def transcribe_track(wav: Path) -> list:
        if args.asr == "skip":
            return []
        if args.asr == "cpu":
            return asr_cpu(wav, hotwords, args.asr_compute, args.threads)
        from meet.asr import transcribe_wav

        return transcribe_wav(wav, hotwords)

    with sw.stage("конвертация sys (ffmpeg + loudnorm)"):
        sys_wav = to_wav16k(sys_src, wav_dir / "sys16.wav", normalize=True)
    with sw.stage("конвертация mic (ffmpeg)"):
        mic_wav = to_wav16k(mic_src, wav_dir / "mic16.wav")

    audio_s = wav_seconds(sys_wav)

    with sw.stage(f"ASR sys ({args.asr})"):
        sys_segs = transcribe_track(sys_wav)
    with sw.stage("forced alignment sys"):
        sys_segs = _maybe_align(sys_segs, sys_wav, args.align and bool(sys_segs))
    with sw.stage(f"диаризация ({args.device})"):
        diar = diarize(sys_wav, args.device, args.speakers, not args.overlap)
    with sw.stage("матчинг голосов по базе"):
        name_map = _match_names(diar)
    with sw.stage("раскладка по спикерам"):
        sys_segs = split_by_speaker(
            sys_segs, _apply_names(diar.turns, name_map), diar.overlaps
        )
    with sw.stage(f"ASR mic ({args.asr})"):
        mic_segs = transcribe_track(mic_wav)
    for seg in mic_segs:
        seg.speaker = "Вы"
    with sw.stage("сборка транскрипта"):
        segments = interleave_tracks(sys_segs, mic_segs)
        iso, dmy = _folder_dates(folder.name)
        out_md = out_dir / f"bench_{args.label}_transcript.md"
        out_md.write_text(
            to_markdown(f"Замер {args.label} — {dmy}", segments, iso), encoding="utf-8"
        )

    total = sw.total()
    chars = sum(len(s.text) for s in segments)
    display = speaker_names(segments)  # SPEAKER_XX -> «Спикер N», имена из базы как есть
    speakers = sorted({display.get(s.speaker, s.speaker) for s in segments if s.speaker})
    report = {
        "label": args.label,
        "folder": str(folder),
        "host": platform.node(),
        "cpu_count": os.cpu_count(),
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device_diarization": args.device,
        "asr": args.asr,
        "asr_compute": args.asr_compute if args.asr == "cpu" else "float16",
        "threads": args.threads,
        "align": bool(args.align),
        "audio_seconds": round(audio_s, 1),
        "stages": [{"name": n, "seconds": round(t, 1)} for n, t in sw.stages],
        "total_seconds": round(total, 1),
        "rtf": round(total / audio_s, 3) if audio_s else None,
        "speakers": speakers,
        "segments": len(segments),
        "chars": chars,
        "transcript": str(out_md),
    }
    (out_dir / f"bench_{args.label}_timings.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", help="папка записи с дорожками sys/mic")
    p.add_argument("--label", required=True, help="метка прогона в именах файлов")
    p.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                   help="устройство диаризации (по умолчанию cuda, как в бою)")
    p.add_argument("--asr", default="cuda", choices=["cuda", "cpu", "skip"],
                   help="где считать ASR; skip — мерить только диаризацию")
    p.add_argument("--asr-compute", default="int8", help="compute_type для ASR на CPU")
    p.add_argument("--threads", type=int, default=None,
                   help="ограничить число потоков CPU (torch и ASR)")
    p.add_argument("--speakers", type=int, default=None, help="число говорящих")
    p.add_argument("--no-align", dest="align", action="store_false",
                   help="без forced alignment")
    p.add_argument("--no-overlap", dest="overlap", action="store_false",
                   help="exclusive-раскладка диаризации")
    p.add_argument("--out", default=None, help="куда писать отчёт (по умолчанию папка записи)")
    p.add_argument("--wav-dir", default=None, help="где оставить wav 16 кГц")
    args = p.parse_args()

    # Потоки ограничиваем до импорта тяжёлых библиотек: BLAS читает env на старте.
    if args.threads:
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ[var] = str(args.threads)

    os.chdir(ROOT)  # hotwords.txt и voices/ ищутся от корня проекта, как в CLI
    sys.path.insert(0, str(ROOT / "src"))

    if args.threads:
        import torch

        torch.set_num_threads(args.threads)

    if args.device == "cuda" or args.asr == "cuda":
        from meet.gpu_lock import hold_gpu_lock

        with hold_gpu_lock(f"bench {args.label}"):
            report = run(args)
    else:
        report = run(args)

    print("\n=== Итог ===")
    for stage in report["stages"]:
        print(f"{stage['seconds']:>8.1f} с  {stage['name']}")
    print(f"{report['total_seconds']:>8.1f} с  ВСЕГО "
          f"(аудио {report['audio_seconds']:.0f} с, RTF {report['rtf']})")
    print(f"спикеры: {', '.join(report['speakers']) or '—'}")
    print(f"транскрипт: {report['transcript']}")


if __name__ == "__main__":
    main()
