"""Калибровка порогов голосов (T0 дизайна «спикеры микрофона», 0.3.3).

Гоняет эмбеддер WeSpeaker по готовым записям и считает распределения, по
которым выбираются стартовые константы meet.mic_split, meet.mic_dedupe и
живых имён:

1. реплика sys против центроидов кластеров встречи (свой / лучший чужой) —
   насколько голос одной реплики ниже центроида за всю встречу;
2. «живой» центроид человека (накопленный по репликам: 4, 8, 16, 32 с речи)
   против базы голосов — свой человек / лучший другой / отрыв; образцы из
   этой же встречи не берутся;
3. окно микрофона (окна mic_split) против образца владельца (если его нет —
   против голоса самого крупного кластера окон этой же записи); доля секунд
   для быстрого пути, кластеры окон;
4. лаги уверенных пар mic↔sys (сосед / эхо) и покрытие — для mic_dedupe;
5. время эмбеддинга окна (мс) на выбранном устройстве.

Запускать только на записях, владелец которых согласен (в записях — голоса
коллег). Отчёт — одни числа: записи обезличены («запись 1»), ни имён, ни
текста реплик, ни путей.

    python scripts/speakers_calib.py <папка записи> [<папка записи> …] \\
        --voices <папка голосов> --out <отчёт.md> [--device auto|cpu|cuda]

Рядом с отчётом .md пишется .json с теми же числами."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from meet import library, mic_dedupe, mic_split, owner_voice, segvoices, speaker_split, voices  # noqa: E402
from meet.asr import Segment, Word  # noqa: E402

RATE = 16000
CHECKPOINTS = (4, 8, 16, 32)
BUCKETS = ((1, 2), (2, 4), (4, 8), (8, None))
MIN_SECONDS = 1.0
MAX_SECONDS = 30.0


# --- числа ----------------------------------------------------------------------


def stats(values) -> dict:
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], dtype=np.float64)
    if not v.size:
        return {"n": 0}
    q = np.percentile(v, [10, 50, 90])
    return {"n": int(v.size), "min": round(float(v.min()), 3), "p10": round(float(q[0]), 3),
            "p50": round(float(q[1]), 3), "p90": round(float(q[2]), 3), "max": round(float(v.max()), 3),
            "mean": round(float(v.mean()), 3)}


def _unit(x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _cos(a, b) -> float | None:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    if a.shape != b.shape or not np.linalg.norm(a) or not np.linalg.norm(b):
        return None
    return float(_unit(a) @ _unit(b))


class Timed:
    """Эмбеддер с замером времени каждого вызова."""

    def __init__(self, embed) -> None:
        self.embed = embed
        self.ms: list[float] = []
        self.by_len: dict[str, list[float]] = {}

    def __call__(self, audio: np.ndarray):
        t0 = time.perf_counter()
        vec = self.embed(audio)
        ms = (time.perf_counter() - t0) * 1000.0
        self.ms.append(ms)
        sec = len(audio) / RATE
        key = "<2" if sec < 2 else "2-3" if sec < 3 else "3-5" if sec < 5 else "5+"
        self.by_len.setdefault(key, []).append(ms)
        if vec is None:
            return None
        vec = np.asarray(vec, dtype=np.float64)
        return vec if vec.size and np.isfinite(vec).all() and np.linalg.norm(vec) else None


def _clip(audio: np.ndarray, start: float, end: float) -> np.ndarray | None:
    if end - start > MAX_SECONDS:
        mid = (start + end) / 2
        start, end = mid - MAX_SECONDS / 2, mid + MAX_SECONDS / 2
    clip = audio[max(0, int(start * RATE)):max(0, int(end * RATE))].astype(np.float32) / 32768.0
    return clip if clip.size >= int(MIN_SECONDS * RATE) else None


# --- одна запись ----------------------------------------------------------------


def _segments(folder: Path) -> list[dict] | None:
    data = library.read_transcript_full(folder)
    if not data or not isinstance(data.get("segments"), list):
        return None
    data = {**data, "segments": [dict(s) for s in data["segments"] if isinstance(s, dict)]}
    segvoices.mark_tracks(folder, data)  # дорожки старой записи — в памяти
    return [s for s in data["segments"] if s.get("kind") != "break"]


def _sidecar(folder: Path) -> dict[str, np.ndarray]:
    """Подпись спикера sys (display и сырая метка) → центроид кластера."""
    found = sorted(folder.glob("*_speakers.json"))
    if not found:
        return {}
    try:
        raw = json.loads(found[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for e in raw.get("speakers") or []:
        if not isinstance(e, dict) or e.get("track") == "mic" or not e.get("embedding"):
            continue
        vec = np.asarray(e["embedding"], dtype=np.float64)
        for key in (e.get("display"), e.get("label")):
            if isinstance(key, str):
                out.setdefault(key, vec)
    return out


def _base_without(voices_dir: Path | None, folder: Path) -> dict[str, list[np.ndarray]]:
    """База голосов без образцов из этой же встречи (иначе сходство завышено)."""
    if voices_dir is None or not voices_dir.is_dir():
        return {}
    out: dict[str, list[np.ndarray]] = {}
    for f in sorted(voices_dir.glob("*.json")):
        try:
            samples = json.loads(f.read_text(encoding="utf-8")).get("samples") or []
        except (OSError, ValueError, AttributeError):
            continue
        keep = [np.asarray(s["embedding"], dtype=np.float64) for s in samples
                if isinstance(s, dict) and s.get("embedding") and s.get("recording") != folder.name
                and folder.name not in str(s.get("source") or "")]
        if keep:
            out[f.stem] = keep
    return out


def _to_segment(s: dict) -> Segment:
    words = [Word(float(w["start"]), float(w["end"]), str(w.get("text") or ""))
             for w in s.get("words") or [] if isinstance(w, dict) and "start" in w and "end" in w]
    return Segment(float(s["start"]), float(s["end"]), str(s.get("text") or ""), s.get("speaker"), words=words,
                   track=s.get("track"))


def _sys_vs_centroids(segs, audio, centroids, embed, limit) -> tuple[list, list, dict, list]:
    own, other, buckets, voiced = [], [], {}, []
    rng = np.random.default_rng(0)
    pool = [s for s in segs if s.get("track") != "mic" and centroids.get(s.get("speaker")) is not None]
    if len(pool) > limit:
        pool = [pool[i] for i in sorted(rng.choice(len(pool), limit, replace=False))]
    for s in pool:
        clip = _clip(audio, float(s["start"]), float(s["end"]))
        vec = embed(clip) if clip is not None else None
        if vec is None:
            continue
        mine = centroids[s["speaker"]]
        o = _cos(vec, mine)
        rest = [_cos(vec, c) for k, c in centroids.items() if c is not mine]
        rest = [r for r in rest if r is not None]
        best_other = max(rest) if rest else None
        own.append(o)
        other.append(best_other)
        dur = float(s["end"]) - float(s["start"])
        for lo, hi in BUCKETS:
            if dur >= lo and (hi is None or dur < hi):
                key = f"{lo}-{hi}" if hi else f"{lo}+"
                buckets.setdefault(key, {"own": [], "other": []})
                buckets[key]["own"].append(o)
                buckets[key]["other"].append(best_other)
        voiced.append((s, vec))
    return own, other, buckets, voiced


def _live_vs_base(voiced, base) -> dict:
    """Накопленный центроид человека из базы против базы на отметках речи."""
    out = {str(c): {"own": [], "best_other": [], "margin": []} for c in CHECKPOINTS}
    by_person: dict[str, list] = {}
    for s, vec in sorted(voiced, key=lambda x: float(x[0]["start"])):
        if s.get("speaker") in base:
            by_person.setdefault(s["speaker"], []).append((float(s["end"]) - float(s["start"]), vec))
    for person, items in by_person.items():
        total, acc = 0.0, None
        marks = list(CHECKPOINTS)
        for dur, vec in items:
            acc = vec * dur if acc is None else acc + vec * dur
            total += dur
            while marks and total >= marks[0]:
                c = marks.pop(0)
                center = _unit(acc)
                mine = max(_cos(center, x) for x in base[person])
                others = [max(_cos(center, x) for x in xs) for name, xs in base.items() if name != person]
                best = max(others) if others else None
                out[str(c)]["own"].append(mine)
                out[str(c)]["best_other"].append(best)
                out[str(c)]["margin"].append(None if best is None else mine - best)
    return out


def _mic_windows(segs, audio, owner, embed) -> tuple[list, dict, list]:
    """Окна микрофона: сходство с образцом владельца (или с крупнейшим
    кластером окон этой записи), доля секунд для быстрого пути, кластеры."""
    mic = [_to_segment(s) for s in segs if s.get("track") == "mic"]
    wins = mic_split.windows(mic)
    vecs, secs = [], []
    for w in wins:
        if w.short:
            continue
        clip = _clip(audio, w.start, w.end)
        vec = embed(clip) if clip is not None else None
        if vec is not None:
            vecs.append(_unit(vec))
            secs.append(w.seconds)
    if not vecs:
        return [], {"fast_share": None, "clusters": []}, []
    x, w = np.stack(vecs), np.asarray(secs)
    labels = speaker_split.ahc_threshold(x, w, mic_split.AHC_STOP)
    if owner:
        cos = [owner_voice.score(v, owner) for v in vecs]
    else:
        # Образца нет: «владелец» — крупнейший кластер, окно — против него без себя.
        main = labels == 0
        total = (x[main] * w[main, None]).sum(0)
        cos = [_cos(total - (v * s if m else 0), v) for v, s, m in zip(x, w, main)]
    fast = float(w[np.asarray([c is not None and c >= mic_split.T_WIN_FAST for c in cos])].sum() / w.sum())
    clusters = []
    for g in range(int(labels.max()) + 1):
        sel = labels == g
        center = _unit((x[sel] * w[sel, None]).sum(0))
        ref = owner_voice.score(center, owner) if owner else _cos(center, (x[labels == 0] * w[labels == 0, None]).sum(0))
        clusters.append({"share": round(float(w[sel].sum() / w.sum()), 3), "seconds": round(float(w[sel].sum()), 1),
                         "owner_cos": None if ref is None else round(ref, 3)})
    return cos, {"fast_share": round(fast, 3), "clusters": clusters}, secs


def _dedupe_lags(segs) -> tuple[list, list, list]:
    """Лаги и покрытие уверенных пар mic↔sys (как их видит mic_dedupe)."""
    def toks(track):
        out = []
        for si, s in enumerate(segs):
            if (s.get("track") == "mic") != (track == "mic"):
                continue
            for wi, w in enumerate(mic_split._words_of(_to_segment(s))[0]):
                out.append(mic_dedupe.Tok((track, si, wi), (track, si), float(w.start), float(w.end), w.text))
        return out

    mic, sys_toks = toks("mic"), sorted(toks("sys"), key=lambda t: t.start)
    starts = [t.start for t in sys_toks]
    neighbour, echo, coverage = [], [], []
    for seg in mic_dedupe._by_segment(mic):
        for p in mic_dedupe._pairs(seg, sys_toks, starts):
            if p.matched < mic_dedupe.CONFIDENT_WORDS:
                continue
            coverage.append(p.coverage)
            if p.coverage < mic_dedupe.CONFIDENT_COVERAGE:
                continue
            if p.lag > mic_dedupe.NEIGHBOUR_MIN_LAG:
                neighbour.append(p.lag)
            elif p.lag <= mic_dedupe.ECHO_MAX_LAG:
                echo.append(p.lag)
    return neighbour, echo, coverage


# --- отчёт ----------------------------------------------------------------------


def constants() -> dict:
    return {"T_OWN": mic_split.T_OWN, "T_OTHER": mic_split.T_OTHER, "T_WIN_FAST": mic_split.T_WIN_FAST,
            "FAST_SHARE": mic_split.FAST_SHARE, "AHC_STOP": mic_split.AHC_STOP, "SYS_LINK": mic_split.SYS_LINK,
            "NEAR_DB": mic_split.NEAR_DB, "VOICE_THRESHOLD": voices.THRESHOLD, "VOICE_MARGIN": voices.MARGIN,
            "UNSURE_MIN": speaker_split.UNSURE_MIN, "NEIGHBOUR_MIN_LAG": mic_dedupe.NEIGHBOUR_MIN_LAG,
            "ECHO_MAX_LAG": mic_dedupe.ECHO_MAX_LAG, "LAG_TOLERANCE": mic_dedupe.LAG_TOLERANCE,
            "MIN_COVERAGE": mic_dedupe.MIN_COVERAGE, "MIN_ENV_CORR": mic_dedupe.MIN_ENV_CORR}


def _row(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"| {name} | 0 | | | | | |"
    return f"| {name} | {s['n']} | {s['min']} | {s['p10']} | {s['p50']} | {s['p90']} | {s['max']} |"


HEAD = "| | n | min | p10 | p50 | p90 | max |\n|---|---|---|---|---|---|---|"


def markdown(r: dict) -> str:
    lines = [f"# Калибровка голосов (T0) — {r['date']}", "",
             f"Записей: {r['recordings']} (пропущено: {r['skipped']}), устройство эмбеддера: {r['device']}.",
             "Записи обезличены; в отчёте только числа.", "",
             "## 1. Реплика sys против центроидов встречи", "", HEAD,
             _row("свой кластер", r["segments_vs_centroids"]["own"]),
             _row("лучший чужой", r["segments_vs_centroids"]["other"])]
    for key, s in r["segments_vs_centroids"]["by_duration"].items():
        lines += [_row(f"свой, {key} с", s["own"]), _row(f"чужой, {key} с", s["other"])]
    lines += ["", "## 2. Накопленный центроид против базы голосов", "", HEAD]
    for c, s in r["live_vs_base"]["checkpoints"].items():
        lines += [_row(f"{c} с: свой", s["own"]), _row(f"{c} с: лучший другой", s["best_other"]),
                  _row(f"{c} с: отрыв", s["margin"])]
    m = r["mic_windows_vs_owner"]
    ref = "образец владельца" if m["reference"] == "owner_sample" else "крупнейший кластер окон записи"
    lines += ["", f"## 3. Окна микрофона против владельца ({ref})", "", HEAD, _row("окно", m["cos"]),
              _row("доля секунд для быстрого пути", m["fast_share"]), "",
              "| запись | минут | реплик sys | окон mic | кластеры окон (доля / сходство) |", "|---|---|---|---|---|"]
    for rec in r["per_recording"]:
        cl = ", ".join(f"{c['share']}/{c['owner_cos']}" for c in rec["mic_clusters"])
        lines.append(f"| {rec['id']} | {rec['minutes']} | {rec['sys_segments']} | {rec['mic_windows']} | {cl} |")
    d = r["dedupe_lags"]
    lines += ["", "## 4. Дубли mic↔sys: лаги уверенных пар (с)", "", HEAD, _row("сосед", d["neighbour"]),
              _row("эхо", d["echo"]), _row("покрытие (от 4 слов)", d["coverage"]), "",
              "## 5. Время эмбеддинга окна (мс)", "", HEAD, _row("все окна", r["embed_ms"])]
    for key, s in r["embed_ms_by_length"].items():
        lines.append(_row(f"окно {key} с", s))
    lines += ["", "## Константы сейчас", "", "| константа | значение |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in r["constants"].items()]
    return "\n".join(lines) + "\n"


def _cpu_embedder():
    import torch
    from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding

    from meet import credentials
    from meet.diarize import DIARIZATION_MODEL

    model = PretrainedSpeakerEmbedding({"checkpoint": DIARIZATION_MODEL, "subfolder": "embedding"},
                                       device=torch.device("cpu"), token=credentials.get_hf_token())

    def embed(audio):
        wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None, None, :]
        return np.asarray(model(wav)[0], dtype=np.float32)

    return embed


def main(argv=None, *, embed=None, load=None) -> dict:
    ap = argparse.ArgumentParser(description="Калибровка порогов голосов (T0): только числа, без имён и текста.")
    ap.add_argument("folders", nargs="+", type=Path, help="папки записей (с согласия их владельца)")
    ap.add_argument("--voices", type=Path, default=None, help="папка базы голосов (и _owner/owner.json)")
    ap.add_argument("--out", type=Path, required=True, help="отчёт .md (рядом — .json)")
    ap.add_argument("--device", choices=("auto", "cpu"), default="auto",
                    help="auto — как у приложения (видеокарта, если есть); cpu — замер на процессоре")
    ap.add_argument("--max-segments", type=int, default=300, help="реплик sys на запись, не больше")
    args = ap.parse_args(argv)

    device = "injected" if embed is not None else args.device
    if embed is None:
        embed = _cpu_embedder() if args.device == "cpu" else segvoices.load_embedder()
    timed = Timed(embed)
    load = load or (lambda path: segvoices.decode(path, RATE))
    owner = owner_voice.load(args.voices) if args.voices else []

    own, other, buckets = [], [], {}
    live = {str(c): {"own": [], "best_other": [], "margin": []} for c in CHECKPOINTS}
    win_cos, fast, per_rec = [], [], []
    neighbour, echo, coverage = [], [], []
    skipped = 0
    for folder in args.folders:
        segs = _segments(folder)
        sys_src, mic_src = library.find_track(folder, "sys"), library.find_track(folder, "mic")
        if segs is None or sys_src is None or mic_src is None:
            skipped += 1
            continue
        n = len(per_rec) + 1
        print(f"запись {n}: голоса реплик…", file=sys.stderr, flush=True)
        sys_audio, mic_audio = load(sys_src), load(mic_src)
        centroids = _sidecar(folder)
        o, t, b, voiced = _sys_vs_centroids(segs, sys_audio, centroids, timed, args.max_segments)
        own += o
        other += t
        for key, v in b.items():
            buckets.setdefault(key, {"own": [], "other": []})
            buckets[key]["own"] += v["own"]
            buckets[key]["other"] += v["other"]
        for c, v in _live_vs_base(voiced, _base_without(args.voices, folder)).items():
            for k in v:
                live[c][k] += v[k]
        print(f"запись {n}: окна микрофона…", file=sys.stderr, flush=True)
        cos, info, secs = _mic_windows(segs, mic_audio, owner, timed)
        win_cos += cos
        if info["fast_share"] is not None:
            fast.append(info["fast_share"])
        nb, ec, cv = _dedupe_lags(segs)
        neighbour += nb
        echo += ec
        coverage += cv
        per_rec.append({"id": f"запись {n}", "minutes": round(len(mic_audio) / RATE / 60, 1),
                        "sys_segments": len(o), "mic_windows": len(secs), "mic_clusters": info["clusters"]})

    report = {
        "version": 1, "date": date.today().isoformat(), "device": device,
        "recordings": len(per_rec), "skipped": skipped,
        "segments_vs_centroids": {"own": stats(own), "other": stats(other),
                                  "by_duration": {k: {"own": stats(v["own"]), "other": stats(v["other"])}
                                                  for k, v in buckets.items()}},
        "live_vs_base": {"checkpoints": {c: {k: stats(v) for k, v in s.items()} for c, s in live.items()}},
        "mic_windows_vs_owner": {"reference": "owner_sample" if owner else "self", "cos": stats(win_cos),
                                 "fast_share": stats(fast)},
        "dedupe_lags": {"neighbour": stats(neighbour), "echo": stats(echo), "coverage": stats(coverage)},
        "embed_ms": stats(timed.ms),
        "embed_ms_by_length": {k: stats(v) for k, v in sorted(timed.by_len.items())},
        "per_recording": per_rec,
        "constants": constants(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown(report), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отчёт: {args.out.name} (+ .json), записей {len(per_rec)}", file=sys.stderr)
    return report


if __name__ == "__main__":
    main()
