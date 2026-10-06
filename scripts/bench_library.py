"""Замер списка записей и поиска по библиотеке на выдуманных встречах.

Встречи создаются во временной папке (настоящие записи не трогаются), текст —
псевдослова с частотами как у живой речи (закон Ципфа) и вкраплениями
нужных запросам слов. Модели не нужны.

    python scripts/bench_library.py                    # 1000 записей, 500 расшифрованы, по часу
    python scripts/bench_library.py --meetings 200 --transcribed 100 --minutes 30
    python scripts/bench_library.py --keep D:/tmp/lib  # оставить библиотеку для повторов

Что меряется (мс, медиана повторов):
* `cards` — карточки всей библиотеки (основа `GET /recordings`): холодные и из кэша,
  и они же с переводом в JSON, как отдаёт резидент;
* поиск (`search.search_library`) по нескольким запросам: холодный (кэш пуст —
  чтение и разбор всех транскриптов), тёплый (тексты в кэше, проход по ним) и
  повтор того же запроса (`repeat_ms`: счётчики категорий и групп берут
  найденное из памяти запроса, тексты не проходят);
* `cpu_spin_ms` — пустой цикл до и после: на ноутбуке частота под нагрузкой
  падает в разы (48 мс в покое, 150 — сброшенная), цифры сравнимы только при
  близкой мерке.
Цели дизайна 0.3.5: тёплый поиск по 500 встречам ≤200 мс, холодный ≤2–3 с,
`/recordings` 1000 из кэша ≤150 мс.
"""

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meet import library, search  # noqa: E402

SYLLABLES = ("ка", "ра", "по", "ли", "ме", "до", "ну", "те", "ва", "со", "би", "ло", "за", "ки", "ст", "пр",
             "ов", "ан", "ен", "ин", "ре", "на", "то", "ми", "го", "ду", "ше", "жи", "ча", "фо")
SPEAKERS = ("Анна Петрова", "Борис", "Вы", "Глеб Смирнов", "Дарья", "SPEAKER_03")
# Слова запросов и как часто они попадаются в сегменте.
PLANTED = {"бюджет": 0.03, "бюджета": 0.02, "релиз": 0.02, "квартал": 0.02, "план работ": 0.01}
RARE = "редкоеслово"
START = datetime(2026, 10, 1, 10, 0)
QUERIES = ("бюджет", "релиз квартал", '"план работ"', "спикер:Анна бюджет", RARE, "что", "кара")


def _vocabulary(rng: random.Random, size: int = 3000) -> tuple[list[str], list[float]]:
    words: list[str] = ["что", "это", "да", "нет", "так", "вот", "мы", "они", "надо", "уже"]
    seen = set(words)
    while len(words) < size:
        word = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(1, 4)))
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words, [1.0 / (rank + 1) for rank in range(len(words))]


def _segments(rng: random.Random, vocab, weights, minutes: int, rare: bool) -> list[dict]:
    out, t = [], 0.0
    speakers = rng.sample(SPEAKERS, rng.randint(2, 4))
    while t < minutes * 60:
        n = rng.randint(8, 30)
        words = rng.choices(vocab, weights, k=n)
        for word, p in PLANTED.items():
            if rng.random() < p:
                words.insert(rng.randrange(len(words) + 1), word)
        if rare and not out:
            words.append(RARE)
        text = " ".join(words).capitalize() + "."
        dur = n * 0.4
        out.append({"start": round(t, 2), "end": round(t + dur, 2), "speaker": rng.choice(speakers),
                    "text": text, "uncertain": False})
        t += dur + rng.uniform(0.1, 3.0)
    return out


def make_library(root: Path, meetings: int, transcribed: int, minutes: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    vocab, weights = _vocabulary(rng)
    for i in range(meetings):
        # по две встречи в день, свежие — с меньшими номерами
        stamp = (START - timedelta(hours=12 * i)).strftime("%Y-%m-%d_%H-%M")
        folder = root / f"{stamp}_{i:04d}"
        folder.mkdir(parents=True)
        (folder / "sys.opus").write_bytes(b"x")
        (folder / "events.jsonl").write_text(json.dumps(
            {"kind": "record.stopped", "duration_s": minutes * 60.0}) + "\n", encoding="utf-8")
        if i % 7 == 0:
            library.write_meta(folder, {"group": "g-alpha", "title": f"Планёрка {i}"})
        if i < transcribed:
            segments = _segments(rng, vocab, weights, minutes, rare=i % 167 == 0)
            library.write_transcript(folder, {"version": 1, "title": None, "segments": segments})


def _timed(fn, repeat: int, best: dict | None = None, name: str = "") -> tuple[float, object]:
    """Медиана повторов (мс); лучший — в `best[name]`: на общей машине медиану
    сдвигают чужие процессы, лучший ближе к тому, что умеет сам код."""
    times, result = [], None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000)
    if best is not None:
        best[name] = round(min(times), 1)
    return statistics.median(times), result


def _spin() -> float:
    """Мерка самого процессора (мс на пустой цикл): ноутбук под нагрузкой или
    на батарее сбрасывает частоту в разы — без неё цифры разных прогонов не сравнить."""
    t0 = time.perf_counter()
    x = 0
    for i in range(2_000_000):
        x += i
    return round((time.perf_counter() - t0) * 1000, 1)


def _cold() -> None:
    search.clear_cache()
    with library._heads_lock:
        library._heads.clear()


def run(root: Path, repeat: int) -> dict:
    out: dict = {"cpu_spin_ms_before": _spin()}
    _cold()
    out["cards_cold_ms"], cards = _timed(lambda: search.cards(root), 1)
    out["cards"] = len(cards)
    out["cards_warm_ms"], _ = _timed(lambda: search.cards(root), repeat)
    out["recordings_warm_ms"], _ = _timed(
        lambda: json.dumps({"items": [dict(c) for c in search.cards(root)][:5000]}, ensure_ascii=False), repeat)
    _cold()
    search.cards(root)  # карточки — не про поиск: их список читает и так
    out["search_cold_ms"], found = _timed(lambda: search.search_library(root, QUERIES[0], limit=5000), 1)
    out["search"] = {}
    def scan(q):
        search._MEMO = None  # тёплый кэш текстов, но без памяти прошлого запроса
        return search.search_library(root, q, limit=5000)

    for q in QUERIES:
        best: dict = {}
        ms, found = _timed(lambda: scan(q), repeat, best, "best")
        again, _ = _timed(lambda: search.search_library(root, q, limit=5000), repeat)
        out["search"][q] = {"warm_ms": round(ms, 1), "best_ms": best["best"], "repeat_ms": round(again, 1),
                            "meetings": len(found), "places": sum(f["total"] for f in found)}
    out["search_warm_max_ms"] = max(v["warm_ms"] for v in out["search"].values())
    out["cpu_spin_ms_after"] = _spin()
    size = getattr(search._CACHE, "_size", None)
    if size is not None:
        out["cache_estimate_mb"] = round(size / 2**20, 1)
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--meetings", type=int, default=1000)
    ap.add_argument("--transcribed", type=int, default=500)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--keep", type=Path, help="папка библиотеки: создать, если пуста, и не удалять")
    args = ap.parse_args()
    root = args.keep or Path(tempfile.mkdtemp(prefix="meet-bench-lib-"))
    try:
        root.mkdir(parents=True, exist_ok=True)
        if not any(root.iterdir()):
            t0 = time.perf_counter()
            make_library(root, args.meetings, args.transcribed, args.minutes)
            print(f"библиотека: {args.meetings} записей, {args.transcribed} расшифрованы по {args.minutes} мин "
                  f"— {time.perf_counter() - t0:.1f} с, {root}", flush=True)
        print(json.dumps(run(root, args.repeat), ensure_ascii=False, indent=1))
    finally:
        if args.keep is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
