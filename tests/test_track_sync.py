"""Оценка сдвига дорожек старой записи по служебным данным записи.

Обе дорожки при «Стоп» доливаются тишиной до одного момента, поэтому разница
длительностей `sys` и `mic` — это запоздание старта микрофона. Журнал событий
проверяет оценку: штатное закрытие и рамка от «запись начата» до строки
`mic.opus: …`. Длительности здесь подставляются функцией — ffprobe не нужен.
"""

import json

import pytest

from meet import library, track_sync

T0 = 1_700_000_000.0


def _log(at: float, text: str) -> dict:
    return {"kind": "log", "at": at, "text": text}


def _journal(window: float, *, stop: bool = True, extra: tuple = ()) -> list[dict]:
    """Журнал обычной записи: старт, дорожки, (штатный) стоп."""
    lines = [
        _log(T0, "запись начата 2026-05-01, pid 100"),
        _log(T0 + window / 2, "sys.opus: Динамики (48000 Hz, 2 ch)"),
        *extra,
        _log(T0 + window, "mic.opus: Микрофон (48000 Hz, 1 ch)"),
        {"kind": "record.started", "at": T0 + window, "folder": "x", "pid": 100, "tracks": []},
    ]
    if stop:
        lines += [_log(T0 + 3600, "запись остановлена штатно"),
                  {"kind": "record.stopped", "at": T0 + 3600, "folder": "x", "duration_s": 3600.4}]
    return lines


def _folder(tmp_path, journal: list[dict] | None, name="2026-05-01_10-00"):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"sys")
    (folder / "mic.opus").write_bytes(b"mic")
    if journal is not None:
        (folder / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in journal) + "\n", encoding="utf-8")
    return folder


class _Probe:
    """Подставные длительности дорожек; считает вызовы."""

    def __init__(self, sys_s, mic_s):
        self.durations = {"sys": sys_s, "mic": mic_s}
        self.calls = []

    def __call__(self, path):
        self.calls.append(path.name)
        return self.durations[path.stem]


def test_difference_of_durations_is_the_mic_delay():
    got = track_sync.estimate(_journal(0.5), 3600.40, 3600.10)
    assert got["mic_s"] == pytest.approx(0.30)
    assert got["method"] == track_sync.METHOD_DURATIONS
    assert got["bounds"] == [0.0, pytest.approx(0.5)]
    assert "reason" not in got


def test_mac_handshake_delay_with_early_silence_is_accepted():
    # Рукопожатие помощника: микрофон открылся через 2,4 с; ранняя доливка
    # тишины у `sys` сдвигает только первые секунды и отказом не считается.
    silence = {"kind": "record.silence", "at": T0 + 1.0, "track": "sys.opus", "gap_s": 1.0}
    got = track_sync.estimate(_journal(2.9, extra=(silence,)), 3602.4, 3600.0)
    assert got["mic_s"] == pytest.approx(2.4)


def test_no_clean_stop_is_refused():
    got = track_sync.estimate(_journal(0.5, stop=False), 3600.40, 3600.10)
    assert got["reason"] == track_sync.UNCLEAN_STOP
    assert "mic_s" not in got


def test_estimate_outside_the_window_is_refused():
    got = track_sync.estimate(_journal(0.6), 3601.2, 3600.0)
    assert got["reason"] == track_sync.OUT_OF_BOUNDS
    assert "mic_s" not in got
    assert got["raw_s"] == pytest.approx(1.2)
    assert got["bounds"] == [0.0, pytest.approx(0.6)]


def test_small_negative_difference_is_zero():
    got = track_sync.estimate(_journal(0.5), 3600.00, 3600.05)
    assert got["mic_s"] == 0.0


def test_clearly_negative_difference_is_refused():
    got = track_sync.estimate(_journal(0.5), 3600.0, 3601.0)
    assert got["reason"] == track_sync.OUT_OF_BOUNDS


def test_trimmed_tail_is_refused():
    # Обрезка хвоста режет все дорожки до одной длины — разница пропадает.
    journal = _journal(0.5) + [{"kind": "record.trimmed", "at": T0 + 4000, "duration_s": 3000.0}]
    assert track_sync.estimate(journal, 3000.0, 3000.0)["reason"] == track_sync.TRIMMED


def test_journal_without_start_lines_is_refused():
    # Объединённая запись пишет только record.started/stopped — рамки нет.
    journal = [{"kind": "record.started", "at": T0}, {"kind": "record.stopped", "at": T0 + 10}]
    assert track_sync.estimate(journal, 10.3, 10.0)["reason"] == track_sync.NO_WINDOW


def test_last_session_in_the_journal_decides():
    # Два сеанса в одном журнале: дорожки — от последнего, первый оборван.
    first = _journal(5.0, stop=False)
    second = [dict(e, at=e["at"] + 7200) for e in _journal(0.5)]
    got = track_sync.estimate(first + second, 3600.4, 3600.1)
    assert got["mic_s"] == pytest.approx(0.3)
    assert got["bounds"][1] == pytest.approx(0.5)


def test_missing_duration_gives_no_estimate():
    assert track_sync.estimate(_journal(0.5), None, 3600.0)["reason"] == track_sync.NO_DURATION


def test_ensure_writes_the_estimate_to_meta(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    library.write_meta(folder, {"title": "Встреча"})
    got = track_sync.ensure(folder, probe=_Probe(3600.40, 3600.10))
    meta = library.read_meta(folder)
    assert meta["title"] == "Встреча"
    assert meta[track_sync.META_KEY] == got
    assert got["mic_s"] == pytest.approx(0.3)
    assert got["method"] == "durations"
    assert isinstance(got["estimated_at"], str)


def test_ensure_without_journal_stores_only_the_reason(tmp_path):
    folder = _folder(tmp_path, None)
    probe = _Probe(3600.4, 3600.1)
    track_sync.ensure(folder, probe=probe)
    stored = library.read_meta(folder)[track_sync.META_KEY]
    assert stored["reason"] == track_sync.NO_JOURNAL
    assert set(stored) == {"reason", "estimated_at"}
    assert probe.calls == []  # без журнала дорожки даже не открываем


def test_ensure_refusal_for_unclean_stop_is_stored(tmp_path):
    folder = _folder(tmp_path, _journal(0.5, stop=False))
    probe = _Probe(3600.4, 3600.1)
    track_sync.ensure(folder, probe=probe)
    assert library.read_meta(folder)[track_sync.META_KEY]["reason"] == track_sync.UNCLEAN_STOP
    assert probe.calls == []


def test_recording_aligned_at_recording_is_not_estimated(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    library.write_meta(folder, {track_sync.META_KEY: track_sync.recorded_mark()})
    probe = _Probe(3600.4, 3600.1)
    got = track_sync.ensure(folder, probe=probe)
    assert probe.calls == []
    assert got == track_sync.recorded_mark()
    assert got["method"] == track_sync.METHOD_RECORDING and got["mic_s"] == 0.0


def test_ensure_runs_once(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    probe = _Probe(3600.4, 3600.1)
    first = track_sync.ensure(folder, probe=probe)
    second = track_sync.ensure(folder, probe=probe)
    assert first == second
    assert sorted(probe.calls) == ["mic.opus", "sys.opus"]


def test_unknown_duration_is_not_stored_and_retried_later(tmp_path):
    # ffprobe не ответил (нет его, файл занят) — это не свойство записи:
    # отказ не запоминается, следующий раз оценка повторится.
    folder = _folder(tmp_path, _journal(0.5))
    got = track_sync.ensure(folder, probe=_Probe(None, 3600.1))
    assert got["reason"] == track_sync.NO_DURATION
    assert track_sync.META_KEY not in library.read_meta(folder)
    track_sync.ensure(folder, probe=_Probe(3600.4, 3600.1))
    assert library.read_meta(folder)[track_sync.META_KEY]["mic_s"] == pytest.approx(0.3)


def test_single_track_recording_is_not_estimated(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    (folder / "mic.opus").unlink()
    assert track_sync.needs_estimate(folder) is False
    assert track_sync.ensure(folder, probe=_Probe(1.0, 1.0)) is None
    assert track_sync.META_KEY not in library.read_meta(folder)


def test_needs_estimate_only_without_track_offset(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    assert track_sync.needs_estimate(folder) is True
    track_sync.ensure(folder, probe=_Probe(3600.4, 3600.1))
    assert track_sync.needs_estimate(folder) is False


def test_estimate_folder_does_not_write(tmp_path):
    folder = _folder(tmp_path, _journal(0.5))
    got = track_sync.estimate_folder(folder, probe=_Probe(3600.4, 3600.1))
    assert got["mic_s"] == pytest.approx(0.3)
    assert not (folder / library.META_JSON).exists()


# --- что применяет перерасшифровка -------------------------------------------


@pytest.mark.parametrize("stored, applied", [
    ({"mic_s": 0.4, "method": "durations", "bounds": [0.0, 0.5]}, 0.4),
    ({"mic_s": -0.3, "method": "durations"}, -0.3),
    (track_sync.recorded_mark(), 0.0),
    ({"reason": "no_journal"}, 0.0),
    ({"method": "durations", "reason": "out_of_bounds", "raw_s": 2.0}, 0.0),
    ({"reason": "no_duration"}, 0.0),
    ({"mic_s": "0.4", "method": "durations"}, 0.0),  # битое значение — как отказ
    ({"mic_s": True, "method": "durations"}, 0.0),
    (None, 0.0),  # не двухдорожечная
])
def test_offset_to_apply_takes_only_an_accepted_estimate(stored, applied):
    assert track_sync.offset_to_apply(stored) == pytest.approx(applied)


@pytest.mark.parametrize("transcript, applied", [
    ({"segments": [], "track_offset_applied_s": 0.4}, 0.4),
    ({"segments": [], "track_offset_applied_s": -0.3}, -0.3),
    ({"segments": []}, 0.0),  # расшифровка до поправки
    ({"segments": [], "track_offset_applied_s": "x"}, 0.0),
    (None, 0.0),  # транскрипта нет
])
def test_applied_offset_of_a_transcript_reads_old_ones_as_zero(transcript, applied):
    assert track_sync.applied_s(transcript) == pytest.approx(applied)
