"""Калибровка поиска голоса владельца по прошлым встречам (meet.owner_derive):
что решили бы остановка кластеризации (CLUSTER_STOP) и порог согласия встреч
(GROUP_COS) на настоящих записях. Константы скрипт не меняет.

    python scripts/owner_derive_calib.py --calibrate [--recordings ПАПКА] \\
        [--owner ИМЯ …] [--max 10] [--out отчёт.json]

Режим `--calibrate`:
1. Папка записей и имена владельца — из config.json приложения, прочитанного
   как текст (без `settings.load`: та переносит старый токен из файла в
   диспетчер учётных данных, то есть пишет), или `--recordings`/`--owner`.
2. Сразу после этого — своя пустая папка данных и HF_HUB_OFFLINE=1: ни
   настройки, ни токен, ни папка данных приложения больше не читаются и не
   пишутся; модель голосов — из кэша Hugging Face.
3. Кандидаты — те же, что у поиска (`owner_derive.candidates`): последние
   записи звонка. Папки записей только читаются: их дорожки, транскрипт,
   слова, кэш голосов и сайдкары спикеров копируются во временную папку,
   дальше читаются только копии, в конце копии удаляются (не вышло — путь
   временной папки в stderr и код 2). База голосов и образцы владельца не
   читаются: проверки «уже есть образец» и «в базе» здесь не делаются.
4. `owner_derive.find` на копиях с нынешними константами, затем перебор
   остановок и порогов на тех же голосах участков (посчитаны один раз).

Печатает только числа (встречи обезличены: «встреча 3»); сбой или Ctrl+C —
в stderr только тип ошибки, копии всё равно удаляются: размеры кластеров
по встречам, распределение попарного косинуса голосов встреч и решение при
каждой паре CLUSTER_STOP × GROUP_COS. Подписи владельца — из настроек
приложения (читаются до подмены папки данных) с переименованиями каждой
встречи, или `--owner` — одни на все встречи."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from meet import library, owner_derive  # noqa: E402

STOPS = (0.30, 0.35, 0.45, 0.55)
GROUP_COSES = (0.65, 0.70, 0.75, 0.80)
# Что копируется из папки записи, кроме дорожек mic и sys.
FILES = ("transcript.json", "words.json", "meta.json", "events.jsonl", "segment_voices.json",
         "segment_tracks.json")
TOP_CLUSTERS = 6


def _enroll_calib():
    """Общее с калибровкой образца: удаление копии с повтором и громкой ошибкой."""
    spec = importlib.util.spec_from_file_location("owner_enroll_calib",
                                                  Path(__file__).with_name("owner_enroll_calib.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def remove_copy(tmp: Path) -> bool:
    return _enroll_calib().remove_copy(tmp)


class Memo:
    """Эмбеддер с памятью по содержимому куска: перебор после `find` не
    считает голоса заново."""

    def __init__(self, embed) -> None:
        self.embed = embed
        self.cache: dict[str, object] = {}

    def __call__(self, clip):
        key = hashlib.sha1(np.ascontiguousarray(clip).tobytes()).hexdigest()
        if key not in self.cache:
            self.cache[key] = self.embed(clip)
        return self.cache[key]


def copy_meeting(folder: Path, into: Path) -> Path:
    """Копия нужного для поиска из папки записи; папка записи только читается.
    Сайдкары спикеров — тоже: по ним подписи владельца встречи (OWNER,
    правило «только кандидат»), как у настоящего поиска."""
    dst = into / folder.name
    dst.mkdir(parents=True)
    for stem in ("mic", "sys"):
        src = library.find_track(folder, stem)
        if src is not None:
            shutil.copyfile(src, dst / src.name)
    names = [n for n in FILES if (folder / n).is_file()] + sorted(p.name for p in folder.glob("*_speakers.json"))
    for name in names:
        shutil.copyfile(folder / name, dst / name)
    return dst


def _pct(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    arr = np.asarray(values)
    out = {"n": len(values), "min": round(float(arr.min()), 3)}
    out.update({f"p{q}": round(float(np.percentile(arr, q)), 3) for q in (10, 25, 50, 90)})
    out["max"] = round(float(arr.max()), 3)
    return out


def sweep(folders: list[Path], *, embed, decode, labels: set[str] | None, vad=None) -> dict:
    """Голоса участков по встречам — один раз; затем остановки и пороги."""
    per, failed = [], []
    for n, f in enumerate(folders, 1):
        try:
            per.append((f.name, owner_derive.meeting_runs(f, embed=embed, decode=decode, owner_labels=labels,
                                                          vad=vad)))
        except Exception as e:  # встреча не разобралась — без неё; текст ошибки может назвать запись
            failed.append({"meeting": n, "error": type(e).__name__})
    meetings = []
    for n, (name, runs) in enumerate(per, 1):
        now = owner_derive.summarize(name, runs)
        meetings.append({
            "meeting": n, "runs": len(runs), "speech_s": round(sum(r.seconds for r in runs), 1),
            "dominant_s": now.seconds, "share": round(now.share, 3), "usable": now.usable,
            "clusters_s": {f"{stop:.2f}": [round(sum(r.seconds for r in g), 1)
                                          for g in owner_derive.clusters(runs, stop)][:TOP_CLUSTERS]
                           for stop in STOPS}})
    by_stop = {}
    for stop in STOPS:
        found = [v for v in (owner_derive.summarize(name, runs, stop) for name, runs in per) if v.usable]
        cos = [float(a.centroid @ b.centroid) for a, b in itertools.combinations(found, 2)]
        decisions = {}
        for group_cos in GROUP_COSES:
            status, group, need = owner_derive.decide(found, group_cos)
            decisions[f"{group_cos:.2f}"] = {"status": status, "group": len(group), "need": need}
        by_stop[f"{stop:.2f}"] = {"usable": len(found), "pair_cos": _pct(cos),
                                  "pairs": sorted(round(c, 3) for c in cos), "decide": decisions}
    return {"meetings": meetings, "failed": failed, "by_stop": by_stop,
            "current": {"stop": owner_derive.CLUSTER_STOP, "group_cos": owner_derive.GROUP_COS}}


def run(root: Path, folders: list[Path], labels: set[str] | None, args, *, embed=None, decode=None,
        vad=None) -> int:
    """Поиск на копиях (`root` — их папка записей) и перебор. Печатает числа."""
    if embed is None:
        from meet import owner_enroll

        embed = owner_enroll.load_embedder()
    memo = Memo(embed)
    voices = Path(root).parent / "voices"  # пустая: ни базы, ни образцов
    voices.mkdir(exist_ok=True)
    outcome = owner_derive.find(root, voices, embed=memo, decode=decode, vad=vad, owner_labels=labels,
                                threshold=2.0, limit=args.max, log=lambda text: None)
    report = {"find": {"status": outcome.status, "checked": outcome.checked, "used": outcome.used,
                       "found": outcome.found}}
    report.update(sweep(folders, embed=memo, decode=decode or _decode(), labels=labels, vad=vad))
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"find": report["find"], "current": report["current"]}))
    for row in report["meetings"] + report["failed"]:
        print(json.dumps(row))
    for stop, entry in report["by_stop"].items():
        print(json.dumps({"stop": float(stop), **entry}))
    return 0


def _decode():
    from meet import segvoices

    return segvoices.decode


def app_setup() -> tuple[Path, set[str]]:
    """Папка записей и имена владельца из config.json приложения — чтение
    файла как текста. Не `settings.load()`: та переносит старый токен HF из
    файла в диспетчер учётных данных (пишет и туда, и в файл). Папка записей
    по умолчанию — как у установленного приложения (data_dir/recordings), а не
    папка репозитория, из которого запущен скрипт."""
    from meet import paths, settings

    base = paths.data_dir()
    try:
        raw = json.loads((base / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    section = raw.get("recording") if isinstance(raw, dict) else None
    rec = settings.Recording.from_raw(section if isinstance(section, dict) else {})
    return rec.out_dir or base / "recordings", {"Вы", rec.speaker_name, *rec.former_speaker_names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="owner_derive_calib")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--recordings", type=Path)
    parser.add_argument("--owner", action="append", default=[])
    parser.add_argument("--max", type=int, default=owner_derive.MAX_MEETINGS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not args.calibrate:
        parser.error("нужен режим --calibrate")
    tmp = Path(tempfile.mkdtemp(prefix="owner-derive-calib-"))
    code = 1
    try:
        try:
            code = _calibrate(args, tmp)
        except KeyboardInterrupt:
            print("прервано", file=sys.stderr)
            code = 130
        except Exception as e:  # текст ошибки может назвать запись — только тип
            print(f"ошибка: {type(e).__name__}", file=sys.stderr)
            code = 1
    finally:
        if not remove_copy(tmp):
            code = 2
    return code


def _calibrate(args, tmp: Path) -> int:
    if args.recordings:
        source, names = args.recordings, {"Вы"}
    else:
        source, names = app_setup()
    # `--owner` — эти подписи во всех встречах; иначе подписи каждой встречи
    # (owner_derive.meeting_labels) от имён владельца из настроек приложения.
    labels = set(args.owner) if args.owner else None
    # Своя пустая папка данных: дальше ни настроек, ни токена приложения;
    # модель — из кэша Hugging Face, и сеть — нет, даже если в окружении иначе.
    os.environ["MEET_DATA_DIR"] = str(tmp / "data")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from meet import credentials, segvoices

    credentials.get_hf_token = lambda: None  # токен из диспетчера не нужен и не читается
    # Имена владельца прочитаны до подмены папки данных — дальше их не перечитать.
    segvoices.owners = lambda: set(names)
    folders = owner_derive.candidates(Path(source), args.max)  # только чтение
    root = tmp / "recordings"
    copies = [copy_meeting(f, root) for f in folders]
    return run(root, copies, labels, args)


if __name__ == "__main__":
    sys.exit(main())
