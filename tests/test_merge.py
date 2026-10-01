"""Объединение встреч: план ffmpeg, выравнивание дорожек тишиной, части в
meta.json и отметки перерывов. Всё, кроме одного интеграционного теста на
крошечных сгенерированных файлах, — без настоящего ffmpeg."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from meet import library, merge
from meet.asr import Segment


def _recording(root: Path, name: str, tracks=("sys", "mic"), started=None, title=None,
               duration=None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for role in tracks:
        (folder / f"{role}.opus").write_bytes(b"x")
    events = []
    if started is not None:
        events.append({"kind": "record.started", "at": started})
    if duration is not None:
        events.append({"kind": "record.stopped", "duration_s": duration})
    if events:
        (folder / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    if title:
        library.write_meta(folder, {"title": title})
    return folder


def _at(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


# --- начало и длительность части ------------------------------------------------


def test_part_start_prefers_the_exact_start_from_events(tmp_path):
    folder = _recording(tmp_path, "2026-09-30_10-00", started=_at("2026-09-30T10:00:07"))
    assert merge.part_start(folder) == datetime.fromisoformat("2026-09-30T10:00:07")


def test_part_start_falls_back_to_the_folder_name(tmp_path):
    folder = _recording(tmp_path, "2026-09-30_10-00")
    assert merge.part_start(folder) == datetime.fromisoformat("2026-09-30T10:00:00")


def test_part_duration_is_the_longest_track(tmp_path):
    folder = _recording(tmp_path, "2026-09-30_10-00")
    lengths = {"sys.opus": 600.0, "mic.opus": 612.5}
    part = merge.describe_part(folder, probe=lambda p: lengths[p.name])
    assert part.duration_s == 612.5
    assert set(part.tracks) == {"sys", "mic"}


def test_part_duration_falls_back_to_events_when_probe_fails(tmp_path):
    folder = _recording(tmp_path, "2026-09-30_10-00", duration=300.0)
    assert merge.describe_part(folder, probe=lambda p: None).duration_s == 300.0


def test_part_without_any_duration_is_an_error(tmp_path):
    folder = _recording(tmp_path, "2026-09-30_10-00")
    with pytest.raises(merge.MergeError, match="длительность"):
        merge.describe_part(folder, probe=lambda p: None)


def test_ffmpeg_duration_is_parsed_from_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(merge.shutil, "which", lambda name: name)

    def run(cmd, **kw):
        assert cmd[0] == "ffprobe"
        return subprocess.CompletedProcess(cmd, 0, stdout="61.234000\n", stderr="")

    assert merge.probe_duration(tmp_path / "a.opus", run=run) == pytest.approx(61.234)


def test_ffmpeg_duration_falls_back_to_ffmpeg_banner(tmp_path, monkeypatch):
    monkeypatch.setattr(merge.shutil, "which", lambda name: None if name == "ffprobe" else name)

    def run(cmd, **kw):
        assert cmd[0] == "ffmpeg"
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="  Duration: 01:02:03.50, start: 0.000000, bitrate: 24 kb/s\n")

    assert merge.probe_duration(tmp_path / "a.opus", run=run) == pytest.approx(3723.5)


# --- дорожки --------------------------------------------------------------------


def _part(id_, start, duration, tracks, title=None):
    return merge.Part(id=id_, folder=Path(id_), start=datetime.fromisoformat(start),
                      duration_s=duration, tracks={r: Path(f"{id_}/{r}.opus") for r in tracks},
                      title=title)


def test_roles_of_calls_are_sys_and_mic_and_import_goes_to_sys():
    parts = [_part("a", "2026-09-30T10:00:00", 60, ("sys", "mic")),
             _part("b", "2026-09-30T11:00:00", 60, ("source",))]
    assert merge.output_roles(parts) == ("sys", "mic")
    # импорт — запись чужого звонка: его звук идёт туда же, где собеседники
    assert merge.role_track(parts[1], "sys") == Path("b/source.opus")
    assert merge.role_track(parts[1], "mic") is None


def test_imports_only_stay_a_single_source_track():
    parts = [_part("a", "2026-09-30T10:00:00", 60, ("source",)),
             _part("b", "2026-09-30T11:00:00", 60, ("source",))]
    assert merge.output_roles(parts) == ("source",)


def test_concat_plan_pads_each_piece_to_its_part_and_fills_gaps_with_silence():
    cmd = merge.concat_command([(Path("a/mic.opus"), 61.5), (None, 30.0), (Path("c/mic.opus"), 10.0)],
                               Path("out/mic.opus.part"))
    assert cmd[0] == "ffmpeg"
    # отсутствующая дорожка — не вход, а генератор тишины в графе
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"] == [str(Path("a/mic.opus")), str(Path("c/mic.opus"))]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:a:0]" in graph and "[1:a:0]" in graph
    assert "anullsrc=r=16000:cl=mono" in graph
    assert "atrim=end=61.500" in graph and "atrim=end=30.000" in graph and "atrim=end=10.000" in graph
    assert graph.count("apad") == 2  # короткая дорожка части дополняется тишиной до её длины
    assert "concat=n=3:v=0:a=1[out]" in graph
    assert cmd[cmd.index("-map") + 1] == "[out]"
    assert cmd[-3:] == ["-f", "ogg", str(Path("out/mic.opus.part"))]
    assert "libopus" in cmd


def test_plan_has_one_piece_per_part_in_order():
    parts = [_part("a", "2026-09-30T10:00:00", 60, ("sys", "mic")),
             _part("b", "2026-09-30T11:00:00", 30, ("sys",))]
    assert merge.pieces(parts, "mic") == [(Path("a/mic.opus"), 60), (None, 30)]


# --- части в meta.json и перерывы -----------------------------------------------------


def test_parts_meta_has_offsets_original_starts_and_gaps():
    parts = [_part("a", "2026-09-30T10:00:00", 600, ("sys",)),
             _part("b", "2026-09-30T10:25:00", 300, ("sys",)),
             _part("c", "2026-09-30T10:30:00", 120, ("sys",))]
    assert merge.parts_meta(parts) == [
        {"id": "a", "start_offset_s": 0.0, "original_start": "2026-09-30T10:00:00",
         "duration_s": 600.0, "gap_s": 0.0},
        {"id": "b", "start_offset_s": 600.0, "original_start": "2026-09-30T10:25:00",
         "duration_s": 300.0, "gap_s": 900.0},
        {"id": "c", "start_offset_s": 900.0, "original_start": "2026-09-30T10:30:00",
         "duration_s": 120.0, "gap_s": 0.0},
    ]


@pytest.mark.parametrize("gap, text", [
    (0, "— перерыв меньше минуты —"),
    (59, "— перерыв меньше минуты —"),
    (90, "— перерыв 2 мин —"),
    (900, "— перерыв 15 мин —"),
    (3600, "— перерыв 1 ч —"),
    (5400, "— перерыв 1 ч 30 мин —"),
    (86400 * 2 + 3600, "— перерыв 2 дня —"),
    (86400 * 5, "— перерыв 5 дней —"),
    (86400 * 21, "— перерыв 21 день —"),
])
def test_break_text(gap, text):
    assert merge.break_text(gap) == text


def test_breaks_are_inserted_at_part_boundaries():
    parts = [{"start_offset_s": 0.0, "gap_s": 0.0}, {"start_offset_s": 100.0, "gap_s": 600.0},
             {"start_offset_s": 200.0, "gap_s": 30.0}]
    segments = [Segment(5, 20, "раз", "Вы"), Segment(95, 101, "на стыке", "Вы"),
                Segment(100, 110, "два", "Спикер 1"), Segment(150, 160, "три", "Вы")]
    out = merge.with_breaks(segments, parts)
    assert [(s.kind, s.start, s.text) for s in out] == [
        (None, 5, "раз"), (None, 95, "на стыке"),
        ("break", 100.0, "— перерыв 10 мин —"),
        (None, 100, "два"), (None, 150, "три"),
        ("break", 200.0, "— перерыв меньше минуты —"),
    ]
    brk = out[2]
    assert brk.speaker is None and brk.end == brk.start


def test_no_parts_no_breaks():
    segments = [Segment(0, 1, "а", "Вы")]
    assert merge.with_breaks(segments, None) == segments
    assert merge.with_breaks(segments, [{"start_offset_s": 0.0}]) == segments


# --- новая папка ------------------------------------------------------------------


def test_create_orders_by_time_and_writes_meta(tmp_path):
    late = _recording(tmp_path, "2026-09-30_11-00", title="Вторая часть")
    early = _recording(tmp_path, "2026-09-30_10-00", title="Планёрка")
    folder = merge.create(tmp_path, [late, early], keep_originals=False)
    assert folder.name == "2026-09-30_10-00_merged"
    meta = library.read_meta(folder)
    assert meta["source"] == "merge"
    assert meta["title"] == "Планёрка"
    assert meta["merged_from"] == ["2026-09-30_10-00", "2026-09-30_11-00"]
    assert meta["merge"] == {"keep_originals": False, "state": "pending", "kb_exported": []}
    card = library.describe(folder)  # видна в библиотеке ещё до звука
    assert card is not None and card.source == "merge" and card.tracks == {}


def test_create_without_titles_names_by_date_and_does_not_clash(tmp_path):
    a = _recording(tmp_path, "2026-09-30_10-00")
    b = _recording(tmp_path, "2026-09-30_10-30")
    (tmp_path / "2026-09-30_10-00_merged").mkdir()
    folder = merge.create(tmp_path, [a, b], keep_originals=True)
    assert folder.name == "2026-09-30_10-00_merged-2"
    meta = library.read_meta(folder)
    assert meta["title"] == "Объединённая встреча 30.09.2026"
    assert meta["merge"]["keep_originals"] is True


def test_create_remembers_originals_exported_to_the_knowledge_base(tmp_path):
    a = _recording(tmp_path, "2026-09-30_10-00")
    b = _recording(tmp_path, "2026-09-30_10-30")
    library.write_meta(b, {"kb_export": {"path": "D:/База/2026-09-30 - Часть 2", "at": 1.0}})
    folder = merge.create(tmp_path, [a, b], keep_originals=False)
    assert library.read_meta(folder)["merge"]["kb_exported"] == ["D:/База/2026-09-30 - Часть 2"]


@pytest.mark.parametrize("names, error", [
    (["2026-09-30_10-00"], "минимум две"),
    (["2026-09-30_10-00", "2026-09-30_10-00"], "минимум две"),
])
def test_create_refuses_fewer_than_two(tmp_path, names, error):
    _recording(tmp_path, "2026-09-30_10-00")
    with pytest.raises(merge.MergeError, match=error):
        merge.create(tmp_path, [tmp_path / n for n in names], keep_originals=False)


def test_create_refuses_recordings_without_sound(tmp_path):
    a = _recording(tmp_path, "2026-09-30_10-00")
    b = _recording(tmp_path, "2026-09-30_11-00_import", tracks=())
    library.write_meta(b, {"source": "import"})
    with pytest.raises(merge.MergeError, match="нет звука"):
        merge.create(tmp_path, [a, b], keep_originals=False)


# --- сборка ---------------------------------------------------------------------------


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"merged")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_run_builds_tracks_parts_and_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(merge.shutil, "which", lambda name: name)
    a = _recording(tmp_path, "2026-09-30_10-00", started=_at("2026-09-30T10:00:00"))
    b = _recording(tmp_path, "2026-09-30_10-30", tracks=("sys",), started=_at("2026-09-30T10:30:00"))
    folder = merge.create(tmp_path, [a, b], keep_originals=False)
    lengths = {"2026-09-30_10-00": 600.0, "2026-09-30_10-30": 300.0}
    run = FakeRun()
    merge.run(folder, run=run, probe=lambda p: lengths[p.parent.name])
    assert [Path(c[-1]).name for c in run.calls] == ["sys.opus.part", "mic.opus.part"]
    assert (folder / "sys.opus").read_bytes() == b"merged" and (folder / "mic.opus").exists()
    assert not list(folder.glob("*.part"))
    meta = library.read_meta(folder)
    assert [p["gap_s"] for p in meta["parts"]] == [0.0, 1200.0]
    card = library.describe(folder)
    assert card.duration_s == 900.0
    assert card.started_at == "2026-09-30T10:00:00"
    assert set(card.tracks) == {"sys", "mic"}


def test_run_fails_when_an_original_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(merge.shutil, "which", lambda name: name)
    a = _recording(tmp_path, "2026-09-30_10-00")
    b = _recording(tmp_path, "2026-09-30_10-30")
    folder = merge.create(tmp_path, [a, b], keep_originals=False)
    shutil.rmtree(b)
    with pytest.raises(merge.MergeError, match="2026-09-30_10-30"):
        merge.run(folder, run=FakeRun(), probe=lambda p: 10.0)


def test_failed_ffmpeg_leaves_no_half_track(tmp_path, monkeypatch):
    monkeypatch.setattr(merge.shutil, "which", lambda name: name)
    a = _recording(tmp_path, "2026-09-30_10-00")
    b = _recording(tmp_path, "2026-09-30_10-30")
    folder = merge.create(tmp_path, [a, b], keep_originals=False)

    def broken(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"half")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Invalid data found")

    with pytest.raises(merge.MergeError, match="Invalid data"):
        merge.run(folder, run=broken, probe=lambda p: 10.0)
    assert not list(folder.glob("*.opus*"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg не установлен")
def test_real_ffmpeg_concatenates_and_pads(tmp_path):
    """Настоящий ffmpeg принимает граф: длина — сумма частей, недостающая
    дорожка второй части — тишина той же длины."""
    def tone(path: Path, seconds: float) -> None:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}", "-c:a", "libopus", str(path)],
                       check=True)

    a = tmp_path / "2026-09-30_10-00"
    b = tmp_path / "2026-09-30_10-30"
    a.mkdir()
    b.mkdir()
    tone(a / "sys.opus", 2.0)
    tone(a / "mic.opus", 1.5)  # короче своей части — дополняется
    tone(b / "sys.opus", 1.0)  # во второй части микрофона нет вовсе
    folder = merge.create(tmp_path, [a, b], keep_originals=True)
    merge.run(folder)
    for role in ("sys", "mic"):
        assert merge.probe_duration(folder / f"{role}.opus") == pytest.approx(3.0, abs=0.15)
