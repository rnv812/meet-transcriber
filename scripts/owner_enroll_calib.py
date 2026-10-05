"""Калибровка порога трёх третей образца голоса (owner_enroll.THIRDS_MIN_COS).

Режет дорожки микрофона, где говорит только владелец, на окна по 25 с (как
запись образца) и прогоняет каждое через те же шаги, что meet.owner_enroll:
VAD образца, речь, уровень над фоном, три трети, попарный косинус. Порог
трёх третей выбирается не выше p5 худшего попарного косинуса годных окон.

Запускать только на записях, владелец которых согласен. Отчёт — одни числа:
окна обезличены («окно 3»), ни путей, ни текста.

    python scripts/owner_enroll_calib.py <mic.opus|wav> [<…> …] [--sys <sys.opus> …] \\
        [--window 25] [--max 12] [--out отчёт.json]

`--sys` (по одному на дорожку микрофона, в том же порядке) — брать только окна,
где звук собеседников почти молчит (не меньше QUIET_SHARE кадров тише QUIET_DB)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from meet import owner_enroll  # noqa: E402

RATE = owner_enroll.SAMPLE_RATE
QUIET_DB = -45.0
QUIET_SHARE = 0.8
THRESHOLDS = (0.60, 0.65, 0.70, 0.75, 0.80)


def window_stats(audio16: np.ndarray, *, vad, embed) -> dict:
    """Одно окно → речь, уровень над фоном и попарные косинусы третей (без
    отказов: калибровке нужны и окна, которые порог отбраковал бы)."""
    audio = np.asarray(audio16, dtype=np.int16).astype(np.float32) / 32768.0
    regions = sorted((float(a), float(b)) for a, b in vad(audio, RATE) if float(b) > float(a))
    seconds = sum(b - a for a, b in regions)
    out = {"speech_s": round(seconds, 2), "snr_db": round(owner_enroll._snr(audio, regions), 1),
           "cos": None, "min_cos": None}
    if seconds < owner_enroll.MIN_SPEECH_S:
        return out
    vectors = []
    for part in np.array_split(owner_enroll._speech(audio, regions), 3):
        vec = embed(np.ascontiguousarray(part, dtype=np.float32))
        if vec is None:
            return out
        vectors.append(owner_enroll._unit(np.asarray(vec, dtype=np.float64)))
    cos = [round(float(vectors[i] @ vectors[j]), 4) for i, j in ((0, 1), (0, 2), (1, 2))]
    return {**out, "cos": cos, "min_cos": min(cos)}


def quiet(sys16: np.ndarray, start: int, end: int) -> bool:
    db = owner_enroll._frame_db(np.asarray(sys16[start:end], dtype=np.float32) / 32768.0)
    return bool(db.size) and float(np.mean(db < QUIET_DB)) >= QUIET_SHARE


def summary(windows: list[dict]) -> dict:
    good = [w["min_cos"] for w in windows if w["min_cos"] is not None]
    out = {"windows": len(windows), "usable": len(good)}
    if good:
        arr = np.asarray(good)
        out.update({f"p{q}": round(float(np.percentile(arr, q)), 4) for q in (5, 10, 25, 50)})
        out["min"] = round(float(arr.min()), 4)
        out["pass"] = {f"{t:.2f}": int((arr >= t).sum()) for t in THRESHOLDS}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="owner_enroll_calib")
    parser.add_argument("mic", nargs="+", type=Path)
    parser.add_argument("--sys", nargs="*", type=Path, default=[])
    parser.add_argument("--window", type=float, default=25.0)
    parser.add_argument("--max", type=int, default=12)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    from meet import segvoices

    embed = owner_enroll.load_embedder()
    windows = []
    for n, mic in enumerate(args.mic):
        audio = segvoices.decode(mic, RATE)
        sys16 = segvoices.decode(args.sys[n], RATE) if n < len(args.sys) else None
        size = int(args.window * RATE)
        for start in range(0, len(audio) - size + 1, size):
            if len([w for w in windows if w["track"] == n + 1]) >= args.max:
                break
            if sys16 is not None and not quiet(sys16, start, start + size):
                continue
            stats = window_stats(audio[start:start + size], vad=owner_enroll._default_vad, embed=embed)
            windows.append({"track": n + 1, "window": len(windows) + 1, **stats})
    report = {"windows": windows, "summary": summary(windows)}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
