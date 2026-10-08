"""«Прокликивание» окна на тестовых данных (0.5, пункт 23).

Поднимает резидента из исходников с временной папкой данных (`MEET_DATA_DIR`:
свои настройки, три записи-образца, автозапись выключена, условия приняты),
Vite с прокси на него и обходит окно в системном Edge (`app/scripts/
clickthrough.mjs`): записи и вкладки карточки, «Голоса», все разделы настроек
с «Тонкой настройкой», поиск, окно ассистента и панель записи — в тёмной и
светлой теме. Снимки и `report.md` — в `--out` (по умолчанию
`.superpowers/clickthrough/<время>`). Код выхода 1 — были ошибки консоли,
падения страницы или ответы резидента 5xx.

    .venv/Scripts/python scripts/clickthrough.py [--out папка] [--keep]

Настоящую папку данных (`%LOCALAPPDATA%\\meet`) не трогает; в сеть не ходит.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
PORT = 5179


def _terms_version() -> str:
    text = (APP / "src" / "lib" / "terms.ts").read_text(encoding="utf-8")
    return re.search(r'TERMS_VERSION = "([^"]+)"', text).group(1)


def _audio(path: Path, seconds: int) -> None:
    """Короткий звук для плеера (ffmpeg из PATH); нет ffmpeg — заглушка, плеер покажет ошибку загрузки."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={seconds}", "-ac", "1", "-c:a", "libopus",
                        "-b:a", "24k", str(path)], check=True)
    else:
        path.write_bytes(b"")


def make_data(data: Path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from meet import library

    recordings = data / "recordings"
    (data / "voices").mkdir(parents=True)
    config = {
        "recording": {"out_dir": str(recordings), "voices_dir": str(data / "voices"), "speaker_name": "Андрей"},
        "auto_record": {"enabled": False},
        "ui": {"theme": "system", "terms_accepted": _terms_version(), "wizard_done": True},
    }
    (data / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    people = ["Андрей", "Анна", "Борис"]
    lines = ["Давайте начнём с бюджета на квартал.", "Предлагаю перенести релиз на неделю.",
             "Согласен, но нужно предупредить поддержку.", "Кто подготовит план миграции?",
             "Я возьму, к пятнице.", "Тогда решили: релиз через неделю, план — к пятнице."]
    for name, title, n in (("2026-10-07_10-00", "Планёрка по релизу", 6),
                           ("2026-10-08_15-30", None, 4), ("2026-10-09_09-00", None, 0)):
        folder = recordings / name
        folder.mkdir(parents=True)
        seconds = max(n, 1) * 10
        _audio(folder / "sys.opus", seconds)
        _audio(folder / "mic.opus", seconds)
        (folder / "events.jsonl").write_text(json.dumps({"kind": "record.stopped", "duration_s": seconds}) + "\n",
                                             encoding="utf-8")
        if n:
            segments = [{"start": i * 10.0, "end": i * 10.0 + 8, "speaker": people[i % 3], "text": lines[i]}
                        for i in range(n)]
            library.write_transcript(folder, {"version": 1, "title": title, "segments": segments})


def wait_for(check, seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.3)
    raise SystemExit(f"не дождался: {what}")


def report(out: Path) -> int:
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    lines = [f"# Прокликивание окна — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Снимков: {len(data['shots'])}, замечаний: {len(data['issues'])}, "
             f"штатных ответов «нет данных»: {len(data.get('expected', []))}.", ""]
    if data["issues"]:
        lines += ["## Замечания", "", "| Тема | Где | Что | Текст |", "|---|---|---|---|"]
        for i in data["issues"]:
            text = i["text"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {i['theme']} | {i['where']} | {i['kind']} | {text} |")
        lines.append("")
    lines += ["## Снимки", ""] + [f"- {s['theme']} — {s['name']}: `{s['file']}`" for s in data["shots"]]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(data["issues"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="не удалять временную папку данных")
    args = parser.parse_args()
    out = (args.out or ROOT / ".superpowers" / "clickthrough" / f"{datetime.now():%Y-%m-%d_%H-%M}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="meet-click-"))
    env = {**os.environ, "MEET_DATA_DIR": str(data), "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "src")}
    procs: list[subprocess.Popen] = []
    try:
        make_data(data)
        log = open(out / "resident.log", "w", encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, "-c", "import sys; from meet.tray import main; sys.argv=['meet-tray','--headless']; main()"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
        endpoint = wait_for(lambda: (data / "daemon.json").is_file()
                            and json.loads((data / "daemon.json").read_text(encoding="utf-8")), 60, "резидент")
        npx = shutil.which("npx") or "npx"
        vite_log = open(out / "vite.log", "w", encoding="utf-8")
        procs.append(subprocess.Popen([npx, "vite", "--port", str(PORT), "--strictPort", "--host", "127.0.0.1"],
                                      cwd=APP, env=env, stdout=vite_log, stderr=subprocess.STDOUT))

        def up() -> bool:
            try:
                return urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1).status == 200
            except OSError:
                return False

        wait_for(up, 60, "Vite")
        node = shutil.which("node") or "node"
        code = subprocess.run([node, "scripts/clickthrough.mjs", f"http://127.0.0.1:{PORT}", str(out)],
                              cwd=APP, env=env).returncode
        found = report(out) if (out / "report.json").is_file() else -1
        print(f"отчёт: {out / 'report.md'}")
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{endpoint['port']}/shutdown", method="POST",
                                         headers={"Authorization": f"Bearer {endpoint['token']}"})
            urllib.request.urlopen(req, timeout=70)
        except OSError:
            pass
        return 1 if code or found else 0
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
                else:
                    proc.terminate()
        if not args.keep:
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
