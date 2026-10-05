"""Микрофон по голосам (meet.mic_split): окна по словам, голос окна, кластеры,
владелец по образцу, люди в комнате, дубли соседа.

Звук синтетический: голос — тон своей частоты (владелец 200 Гц, люди в
комнате 450 и 800 Гц); эмбеддер-подделка раскладывает окно по этим частотам
(плюс небольшой шум окна), так что смешанное окно и даёт смешанный голос.
Фразы и имена выдуманы, моделей нет."""

import wave
import zlib

import numpy as np
import pytest

from meet import mic_split, owner_voice
from meet.asr import Segment, Word
from meet.diarize import Diarization

RATE = 16000
DIM = 8
FREQS = (200.0, 450.0, 800.0)
OWNER, ROOM1, ROOM2 = 0, 1, 2
LOUD, QUIET = 0.5, 0.06
TOTAL = 90.0


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def _e(i):
    return np.eye(DIM)[i]


def fake_embed(audio):
    """Окно → доли энергии на частотах голосов + шум окна (зерно — от звука)."""
    spec = np.abs(np.fft.rfft(audio))
    hz = np.fft.rfftfreq(len(audio), 1.0 / RATE)
    v = np.zeros(DIM)
    for i, f in enumerate(FREQS):
        v[i] = spec[(hz > f - 15) & (hz < f + 15)].sum()
    if not v.any():
        return None
    v = v / np.linalg.norm(v)
    rng = np.random.default_rng(int(abs(float(audio[: RATE // 10].sum())) * 1e6) % (2**32))
    v[3:] = rng.normal(0.0, 0.08, DIM - 3)
    return v.astype(np.float32)


class Track:
    """Звук дорожки: фразы-тоны по словам."""

    def __init__(self, total=TOTAL):
        self.audio = np.zeros(int(total * RATE), dtype=np.float64)

    def say(self, words, voice, amp, seed=0):
        rng = np.random.default_rng(seed)
        for w in words:
            a, b = int(w.start * RATE), int(w.end * RATE)
            t = np.arange(b - a) / RATE
            # Громкость слова — от самого слова: копия той же фразы на другой
            # дорожке звучит с той же огибающей.
            env = 0.6 + 0.4 * (zlib.crc32(w.text.encode("utf-8")) % 1000) / 1000
            for v in (voice if isinstance(voice, tuple) else (voice,)):
                self.audio[a:b] += amp * env * np.sin(2 * np.pi * FREQS[v] * t + rng.uniform(0, 6))
        self.audio[:] += 0.0005 * rng.normal(size=len(self.audio))  # фон

    def write(self, path):
        pcm = np.clip(self.audio, -1, 1)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes((pcm * 32767).astype(np.int16).tobytes())
        return path


def _words(text, start, step=0.4):
    return [Word(round(start + i * step, 3), round(start + i * step + 0.35, 3), " " + w)
            for i, w in enumerate(text.split())]


def _seg(words, **kw):
    return Segment(words[0].start, words[-1].end, "".join(w.text for w in words).strip(),
                   words=list(words), **kw)


OWNER_PHRASES = [
    (2.0, "я посмотрел отчёт по складу и там всё сходится до копейки"),
    (12.0, "давайте тогда двигаться дальше по плану на эту неделю"),
    (22.0, "мне кажется сроки реальные если никто не заболеет опять"),
    (32.0, "я напишу поставщику сегодня вечером и пришлю ответ всем"),
]
# Люди в комнате: по четыре фразы ~4 с — больше MIN_DECIDE_S речи на голос,
# и у копий через звонок хватает независимых пар для лага (mic_dedupe).
ROOM1_PHRASES = [
    (40.0, "а по бюджету у нас что получается на следующий квартал"),
    (44.5, "я бы ещё раз проверил цифры перед тем как отправлять"),
    (49.0, "и с бухгалтерией тоже надо бы это всё согласовать"),
    (53.5, "в прошлый раз они нам вернули счёт без всяких объяснений"),
]
ROOM2_PHRASES = [
    (60.0, "склад готов принять всю партию в четверг утром до обеда"),
    (64.5, "и машину надо заказать заранее иначе не успеем никак"),
    (69.0, "водитель обещал позвонить накануне вечером после шести"),
    (73.5, "а грузчиков на месте будет двое или даже трое человек"),
]
LEAK_LAG = 0.3  # мой голос через ноутбук соседа


def _meeting(tmp_path, *, room1=False, room2=False, extra_mic=(), sys_phrases=()):
    """Запись: микрофон (владелец громко, люди в комнате тихо) и sys."""
    mic, sys = Track(), Track()
    mic_segs = []
    for n, (start, text) in enumerate(OWNER_PHRASES):
        ws = _words(text, start)
        mic.say(ws, OWNER, LOUD, seed=n)
        mic_segs.append(_seg(ws))
    for flag, voice, phrases in ((room1, ROOM1, ROOM1_PHRASES), (room2, ROOM2, ROOM2_PHRASES)):
        if flag:
            for n, (start, text) in enumerate(phrases):
                ws = _words(text, start)
                mic.say(ws, voice, QUIET, seed=100 + voice * 10 + n)
                mic_segs.append(_seg(ws))
    for ws, voice, amp in extra_mic:
        mic.say(ws, voice, amp, seed=int(ws[0].start * 10))
        mic_segs.append(_seg(ws))
    sys_segs = []
    for ws, voice in sys_phrases:
        sys.say(ws, voice, LOUD, seed=500 + int(ws[0].start))
        sys_segs.append(_seg(ws, speaker="SPEAKER_00"))
    mic_segs.sort(key=lambda s: s.start)
    return mic_segs, sys_segs, mic.write(tmp_path / "mic16.wav"), sys.write(tmp_path / "sys16.wav")


def _owner(vec=None, device=None):
    return [owner_voice.OwnerSample(id="o1", embedding=np.asarray(_unit(_e(OWNER) if vec is None else vec),
                                                                   dtype=np.float32),
                                    source="enroll", date="2026-10-05", seconds=25.0, device=device)]


def _run(meeting, owner, *, base=None, diar=None, names=None, embed=fake_embed, **kw):
    mic_segs, sys_segs, mic_wav, sys_wav = meeting
    logs = []
    got = mic_split.run(mic_segs, sys_segs, mic_wav, sys_wav, diar, owner=owner, base=base or {},
                        threshold=0.75, owner_label="Вы", embed=embed, log=logs.append, names=names, **kw)
    return got, logs


def _speakers(segs):
    return [(round(s.start, 1), s.speaker, s.uncertain) for s in segs]


def _by_speaker(segs):
    out = {}
    for s in segs:
        out.setdefault(s.speaker, []).append(round(s.start, 1))
    return out


# --- окна -----------------------------------------------------------------------


def test_windows_cut_on_pause_and_length_short_tails_join():
    ws = _words("раз два три четыре пять шесть семь восемь", 0.0)  # 0.0 … 3.15
    lone = [Word(10.0, 10.4, " ага")]
    after = _words("ну да", 3.6)  # пауза 0,45 > 0,3 — новое окно, но короткое: к соседу
    segs = [_seg(ws), _seg(after), Segment(10.0, 10.4, "ага", words=lone)]
    wins = mic_split.windows(segs)
    spans = [(round(w.start, 2), round(w.end, 2), w.short) for w in wins]
    # 8 слов: окно до 3 с, хвост «восемь» (0,35 с) — к нему; «ну да» — к нему же (пауза < 0,5).
    assert spans == [(0.0, 4.35, False), (10.0, 10.4, True)]
    assert [k for k in wins[0].keys][:2] == [(0, 0), (0, 1)]


def test_window_without_word_timings_is_whole_segment():
    wins = mic_split.windows([Segment(5.0, 7.5, "без слов")])
    assert [(w.start, w.end, w.short, w.keys) for w in wins] == [(5.0, 7.5, False, [(0, 0), (0, 1)])]


# --- без образца — как сейчас ------------------------------------------------------


def test_without_owner_sample_mic_is_owner_and_embedder_is_not_loaded(tmp_path):
    calls = []

    def embed(audio):
        calls.append(len(audio))
        return fake_embed(audio)

    meeting = _meeting(tmp_path, room1=True)
    got, logs = _run(meeting, [], embed=embed)
    assert calls == []
    assert {s.speaker for s in got.mic} == {"Вы"} and all(s.track == "mic" for s in got.mic)
    assert not any(s.uncertain for s in got.mic)
    assert got.report == {"rule": 1, "status": "no_profile", "owner_profile": None, "room_speakers": 0,
                          "dropped": {"echo": 0, "neighbour": 0, "owner_leak": 0}}
    assert [s.text for s in got.mic] == [s.text for s in meeting[0]]
    assert got.sys == meeting[1]


def test_switched_off_is_like_today(tmp_path):
    got, _ = _run(_meeting(tmp_path, room1=True), _owner(), speakers=False,
                  embed=lambda a: pytest.fail("эмбеддер не нужен"))
    assert got.report["status"] == "off" and {s.speaker for s in got.mic} == {"Вы"}


def test_embedder_unavailable_means_no_split(tmp_path, monkeypatch):
    from meet import segvoices

    def broken():
        raise RuntimeError("нет токена")

    monkeypatch.setattr(segvoices, "load_embedder", broken)
    got, logs = _run(_meeting(tmp_path, room1=True), _owner(), embed=None)
    assert got.report["status"] == "skipped_no_token" and {s.speaker for s in got.mic} == {"Вы"}
    assert any("нет токена" in line for line in logs)


def test_no_token_known_in_advance_does_not_load_the_embedder(tmp_path, monkeypatch):
    """Диаризация уже сказала «нет токена HF»: эмбеддер (тот же гейтед-чекпойнт)
    не грузится вовсе — ни попытки, ни строки об ошибке в журнале."""
    from meet import segvoices

    monkeypatch.setattr(segvoices, "load_embedder", lambda: pytest.fail("эмбеддер не грузится без токена"))
    got, logs = _run(_meeting(tmp_path, room1=True), _owner(), embed=None, no_token=True)
    assert got.report["status"] == "skipped_no_token" and {s.speaker for s in got.mic} == {"Вы"}
    assert not any("не посчитать" in line for line in logs)


# --- быстрый путь ----------------------------------------------------------------


def test_fast_path_one_person_at_the_microphone(tmp_path):
    got, _ = _run(_meeting(tmp_path), _owner())
    assert got.report["status"] == "ok" and got.report["owner_profile"] == "enroll"
    assert got.report["room_speakers"] == 0
    assert {s.speaker for s in got.mic} == {"Вы"} and not any(s.uncertain for s in got.mic)
    assert got.voices["fast"] is True
    (owner_entry,) = got.sidecar
    assert owner_entry["label"] == "OWNER" and owner_entry["owner"] is True and owner_entry["track"] == "mic"
    assert owner_entry["display"] == "Вы" and len(owner_entry["embedding"]) == DIM
    assert [(c["id"], c["role"], c["label"]) for c in got.voices["clusters"]] == [("O0", "owner", "Вы")]


# --- владелец и люди в комнате ----------------------------------------------------


def test_owner_and_one_room_speaker(tmp_path):
    got, logs = _run(_meeting(tmp_path, room1=True), _owner())
    assert got.report["status"] == "ok" and got.report["room_speakers"] == 1
    by = _by_speaker(got.mic)
    assert by["Вы"] == [s for s, _ in OWNER_PHRASES]
    assert by["SPEAKER_M0"] == [s for s, _ in ROOM1_PHRASES]
    assert all(s.track == "mic" for s in got.mic)
    roles = {c["role"] for c in got.voices["clusters"]}
    assert roles == {"owner", "room"}
    room = next(c for c in got.voices["clusters"] if c["role"] == "room")
    assert room["label"] == "SPEAKER_M0" and room["link"]["kind"] == "new" and room["seconds"] >= 10
    assert room["owner_cos"] < mic_split.T_OTHER
    labels = {e["label"]: e for e in got.sidecar}
    assert labels["SPEAKER_M0"]["track"] == "mic" and "owner" not in labels["SPEAKER_M0"]
    assert labels["OWNER"]["owner"] is True
    assert not any("бюджету" in line for line in logs)  # в журнал — без текста встречи


def test_owner_and_two_room_speakers_named_from_sys_and_base(tmp_path):
    """Второй человек в комнате — тот же голос, что кластер sys (сосед со своим
    ноутбуком) — получает его подпись; первый узнан по базе голосов."""
    diar = Diarization(turns=[(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")],
                       embeddings={"SPEAKER_00": _e(5).astype(np.float32),
                                   "SPEAKER_01": _unit(_e(ROOM2) + 0.1 * _e(6)).astype(np.float32)})
    base = {"Пётр": [_e(ROOM1).astype(np.float32)], "Анна": [_e(4).astype(np.float32)]}
    got, _ = _run(_meeting(tmp_path, room1=True, room2=True), _owner(), base=base, diar=diar,
                  names={"SPEAKER_01": "Демьян"})
    by = _by_speaker(got.mic)
    assert by["Вы"] == [s for s, _ in OWNER_PHRASES]
    assert by["Пётр"] == [s for s, _ in ROOM1_PHRASES]
    assert by["Демьян"] == [s for s, _ in ROOM2_PHRASES]
    assert got.report["room_speakers"] == 2
    links = {c["label"]: c["link"] for c in got.voices["clusters"] if c["role"] == "room"}
    assert links["Пётр"]["kind"] == "base" and links["Демьян"] == {"kind": "sys", "to": "SPEAKER_01",
                                                                 "score": pytest.approx(0.99, abs=0.02)}
    displays = {e["display"] for e in got.sidecar if not e.get("owner")}
    assert displays == {"Пётр", "Демьян"}


def test_room_cluster_without_name_is_numbered(tmp_path):
    got, _ = _run(_meeting(tmp_path, room1=True, room2=True), _owner())
    by = _by_speaker(got.mic)
    assert set(by) == {"Вы", "SPEAKER_M0", "SPEAKER_M1"}


def test_mixed_segment_is_cut_by_voice(tmp_path):
    """Одна реплика ASR захватила владельца и человека рядом — режется по словам."""
    mine = _words("я думаю что нам стоит подождать с этим решением", 40.0)
    theirs = _words("а по бюджету у нас что получается на следующий квартал", 44.0)
    mic, sys = Track(), Track()
    for n, (start, text) in enumerate(OWNER_PHRASES):
        ws = _words(text, start)
        mic.say(ws, OWNER, LOUD, seed=n)
    mic.say(mine, OWNER, LOUD, seed=40)
    mic.say(theirs, ROOM1, QUIET, seed=41)
    for n, (start, text) in enumerate(ROOM1_PHRASES):
        mic.say(_words(text, start + 10), ROOM1, QUIET, seed=50 + n)
    segs = [_seg(_words(t, s)) for s, t in OWNER_PHRASES] + [_seg(mine + theirs)]
    segs += [_seg(_words(t, s + 10)) for s, t in ROOM1_PHRASES]
    meeting = (segs, [], mic.write(tmp_path / "mic16.wav"), sys.write(tmp_path / "sys16.wav"))
    got, _ = _run(meeting, _owner())
    cut = [s for s in got.mic if 40.0 <= s.start < 48.5]
    assert [(s.speaker, s.text.split()[0]) for s in cut] == [("Вы", "я"), ("SPEAKER_M0", "а")]
    assert all(s.words and s.track == "mic" for s in cut)
    assert sum(len(s.words) for s in cut) == len(mine + theirs)


def test_short_reply_inherits_nearby_window_far_one_stays_owner(tmp_path):
    """«Ага» короче секунды голоса не имеет: метка соседнего окна до 1,5 с,
    дальше — владелец."""
    near = [Word(58.0, 58.3, " ага")]  # 0,55 с после фразы человека в комнате (… 57.45)
    far = [Word(61.0, 61.3, " угу")]  # 3,5 с после неё
    got, _ = _run(_meeting(tmp_path, room1=True, extra_mic=[(near, ROOM1, QUIET), (far, ROOM1, QUIET)]),
                  _owner())
    speaker_at = {round(s.start, 1): s.speaker for s in got.mic}
    assert speaker_at[58.0] == "SPEAKER_M0"
    assert speaker_at[61.0] == "Вы"


def test_grey_zone_cluster_is_owner_but_uncertain(tmp_path):
    """Образец с другого микрофона похож на голос владельца лишь средне (между
    T_OTHER и T_OWN): владелец с пометкой «не уверен», а не чужое имя; люди
    в комнате — своими кластерами."""
    sample = _unit(0.70 * _e(OWNER) + 0.71 * _e(4))
    got, _ = _run(_meeting(tmp_path, room1=True), _owner(sample))
    by_start = {round(s.start, 1): (s.speaker, s.uncertain) for s in got.mic}
    for start, _ in OWNER_PHRASES:
        assert by_start[start] == ("Вы", True)
    for start, _ in ROOM1_PHRASES:
        assert by_start[start] == ("SPEAKER_M0", False)
    roles = {c["role"]: c for c in got.voices["clusters"]}
    assert set(roles) == {"unsure", "room"}
    assert mic_split.T_OTHER <= roles["unsure"]["owner_cos"] < mic_split.T_OWN


def test_loud_room_voice_is_not_taken_for_the_owner(tmp_path):
    """Громкость людей в комнате не отличает (T0: на 1–2 дБ тише владельца):
    так же громкий, но чужой голос — человек в комнате."""
    loud_room = [(_words(t, s), ROOM1, LOUD) for s, t in ROOM1_PHRASES]
    got, _ = _run(_meeting(tmp_path, extra_mic=loud_room), _owner())
    assert _by_speaker(got.mic) == {"Вы": [s for s, _ in OWNER_PHRASES],
                                    "SPEAKER_M0": [s for s, _ in ROOM1_PHRASES]}


def test_short_cluster_below_owner_threshold_is_unsure(tmp_path):
    """Кластер меньше MIN_DECIDE_S речи, не похожий на образец, — не «человек в
    комнате», а владелец с пометкой."""
    few = [(_words(ROOM1_PHRASES[0][1], ROOM1_PHRASES[0][0]), ROOM1, QUIET),
           (_words(ROOM1_PHRASES[1][1], ROOM1_PHRASES[1][0]), ROOM1, QUIET)]
    got, _ = _run(_meeting(tmp_path, extra_mic=few), _owner())
    by_start = {round(s.start, 1): (s.speaker, s.uncertain) for s in got.mic}
    assert by_start[ROOM1_PHRASES[0][0]] == ("Вы", True)
    assert got.report["room_speakers"] == 0


def test_embedding_runs_with_capped_torch_threads(tmp_path, monkeypatch):
    """Эмбеддинг окон — не больше EMBED_THREADS потоков torch, потом как было."""
    import sys
    import types

    state = {"n": 16, "seen": []}
    fake = types.SimpleNamespace(get_num_threads=lambda: state["n"],
                                 set_num_threads=lambda n: state.update(n=n))
    monkeypatch.setitem(sys.modules, "torch", fake)

    def embed(audio):
        state["seen"].append(state["n"])
        return fake_embed(audio)

    _run(_meeting(tmp_path, room1=True), _owner(), embed=embed)
    assert set(state["seen"]) == {mic_split.EMBED_THREADS} and state["n"] == 16


def test_live_threshold_is_capped():
    assert mic_split.live_threshold(0.82) == pytest.approx(0.70)
    assert mic_split.live_threshold(0.72) == pytest.approx(0.67)


def test_owner_not_found_falls_back_to_today(tmp_path):
    """Образец не похож ни на один крупный голос микрофона (другой человек,
    другой микрофон) — разделения нет, подсказка записать образец."""
    got, _ = _run(_meeting(tmp_path, room1=True), _owner(_e(ROOM2)))
    assert got.report["status"] == "owner_not_found"
    assert {s.speaker for s in got.mic} == {"Вы"} and not any(s.uncertain for s in got.mic)
    # Кандидат в голос владельца — крупнейший голос микрофона, с пометкой:
    # по нему «Это я + Запомнить мой голос» может поправить плохой образец.
    (entry,) = got.sidecar
    assert entry["label"] == "OWNER" and entry["owner"] is True and entry["candidate"] is True
    assert entry["display"] == "Вы" and entry["track"] == "mic"
    center = np.asarray(entry["embedding"])
    assert max(center @ _e(OWNER), center @ _e(ROOM1)) > 0.8


def test_no_token_and_no_sample_says_no_token(tmp_path):
    """Без токена HF образец всё равно не записать (та же модель): статус —
    «нет токена», а не «нет образца»."""
    got, _ = _run(_meeting(tmp_path, room1=True), [], embed=None, no_token=True)
    assert got.report["status"] == "skipped_no_token"


def test_owner_sample_of_this_device_is_preferred(tmp_path):
    samples = [owner_voice.OwnerSample(id="a", embedding=_e(ROOM2).astype(np.float32), source="enroll",
                                       date="2026-10-05", seconds=25.0, device="USB"),
               owner_voice.OwnerSample(id="b", embedding=_e(OWNER).astype(np.float32), source="meeting",
                                       date="2026-10-05", seconds=60.0, device="Onboard", recording="r1")]
    ok, _ = _run(_meeting(tmp_path, room1=True), samples, device="Onboard")
    assert ok.report["status"] == "ok" and ok.report["owner_profile"] == "meeting"
    bad, _ = _run(_meeting(tmp_path, room1=True), samples, device="USB")
    assert bad.report["status"] == "owner_not_found"


def test_empty_mic_segment_gets_owner_label(tmp_path):
    meeting = _meeting(tmp_path)
    segs = meeting[0] + [Segment(80.0, 80.5, "", speaker=None)]
    got, _ = _run((segs, *meeting[1:]), _owner())
    assert got.mic[-1].speaker == "Вы" and got.mic[-1].track == "mic"


def test_no_mic_speech(tmp_path):
    meeting = ([], [], *_meeting(tmp_path)[2:])
    got, _ = _run(meeting, _owner())
    assert got.mic == [] and got.report["status"] == "ok" and got.sidecar == []


# --- мало голоса, нет звука ----------------------------------------------------------


def test_broken_embedder_means_no_voice_and_no_owner_leak(tmp_path):
    """Эмбеддер вернул NaN: голоса нет — статус no_voice, микрофон владельца,
    и копии в sys «по голосу владельца» не удаляются."""
    sys_phrases = [(_words(t, s + LEAK_LAG), OWNER) for s, t in OWNER_PHRASES]
    got, _ = _run(_meeting(tmp_path, sys_phrases=sys_phrases), _owner(),
                  embed=lambda a: np.full(DIM, np.nan, dtype=np.float32))
    assert got.report["status"] == "no_voice" and got.report["dropped"]["owner_leak"] == 0
    assert len(got.sys) == len(OWNER_PHRASES) and {s.speaker for s in got.mic} == {"Вы"}


def test_too_little_voiced_speech_is_no_voice(tmp_path):
    """Одно окно 1,5 с — не основание ни для быстрого пути, ни для удалений."""
    ws = _words("ну да давайте", 5.0)
    mic, sys = Track(), Track()
    mic.say(ws, OWNER, LOUD)
    sys.say(_words("ну да давайте", 5.0 + LEAK_LAG), OWNER, LOUD)
    meeting = ([_seg(ws)], [_seg(_words("ну да давайте", 5.0 + LEAK_LAG), speaker="SPEAKER_00")],
               mic.write(tmp_path / "mic16.wav"), sys.write(tmp_path / "sys16.wav"))
    got, _ = _run(meeting, _owner())
    assert got.report["status"] == "no_voice" and len(got.sys) == 1 and got.sidecar == []


def test_missing_mic_audio_falls_back_to_today(tmp_path):
    """Звук микрофона не прочитать: расшифровка не падает, всё как раньше."""
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    segs, sys_segs, _, sys_wav = _meeting(tmp_path, room1=True, sys_phrases=sys_phrases)
    gone = tmp_path / "нет.wav"
    for owner, status in (([], "no_profile"), (_owner(), "skipped_error")):
        got, logs = mic_split.run(segs, sys_segs, gone, sys_wav, None, owner=owner, base={}, threshold=0.75,
                                  owner_label="Вы", embed=fake_embed, log=(out := []).append), out
        assert got.report["status"] == status and {s.speaker for s in got.mic} == {"Вы"}
        assert got.dropped == [] and got.sys == sys_segs
        assert any("звук не прочитать" in line for line in logs)


def test_nothing_to_do_does_not_read_audio(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mic_split, "_read", lambda p: calls.append(p))
    segs, sys_segs, mic_wav, sys_wav = _meeting(tmp_path, room1=True)
    got = mic_split.run(segs, sys_segs, mic_wav, sys_wav, None, owner=_owner(), base={}, threshold=0.75,
                        owner_label="Вы", embed=fake_embed, log=lambda m: None, speakers=False, dedupe=False)
    assert calls == [] and got.report["status"] == "off"
    got = mic_split.run(segs, [], mic_wav, sys_wav, None, owner=[], base={}, threshold=0.75,
                        owner_label="Вы", embed=fake_embed, log=lambda m: None)
    assert calls == [] and got.report["status"] == "no_profile"


def test_embedder_failure_with_token_is_a_generic_skip(tmp_path, monkeypatch):
    from meet import credentials, segvoices

    def broken():
        raise MemoryError("CUDA out of memory")

    monkeypatch.setattr(segvoices, "load_embedder", broken)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    got, _ = _run(_meeting(tmp_path, room1=True), _owner(), embed=None)
    assert got.report["status"] == "skipped_error" and {s.speaker for s in got.mic} == {"Вы"}


def test_owner_not_found_dedupes_only_quiet_copies(tmp_path):
    """Образец не нашёлся: дубли — как без образца (тихие копии с уликами)."""
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    sys_phrases += [(_words(t, s + LEAK_LAG), OWNER) for s, t in OWNER_PHRASES[:1]]
    got, _ = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), _owner(_e(ROOM2)))
    assert got.report["status"] == "owner_not_found"
    assert got.report["dropped"] == {"echo": 0, "neighbour": 4, "owner_leak": 0}
    assert _by_speaker(got.mic) == {"Вы": [s for s, _ in OWNER_PHRASES]}


def _small_neighbour(tmp_path, neighbour_idx, total_windows=107):
    """Встреча ревьюера: 107 окон по 5 слов, из них несколько — сосед со своим
    ноутбуком (тихо в микрофоне, копия в sys с лагом 0,2 с)."""
    total = total_windows * 3.0 + 5
    mic, sys = Track(total), Track(total)
    mic_segs, sys_segs = [], []
    for k in range(total_windows):
        start = 1.0 + k * 3.0
        who = "сосед" if k in neighbour_idx else "влад"
        ws = [Word(round(start + i * 0.4, 3), round(start + i * 0.4 + 0.35, 3), f" {who}{k}ж{i}") for i in range(5)]
        if k in neighbour_idx:
            mic.say(ws, ROOM1, QUIET, seed=k)
            copy = [Word(w.start + 0.2, w.end + 0.2, w.text) for w in ws]
            sys.say(copy, ROOM1, LOUD, seed=1000 + k)
            sys_segs.append(_seg(copy, speaker="SPEAKER_01"))
        else:
            mic.say(ws, OWNER, LOUD, seed=k)
        mic_segs.append(_seg(ws))
    return mic_segs, sys_segs, mic.write(tmp_path / "mic16.wav"), sys.write(tmp_path / "sys16.wav")


def test_small_neighbour_is_never_absorbed_by_the_fast_path(tmp_path):
    """Сосед говорит ~6% окон, и его окна приходятся между точками любой
    выборки: быстрый путь его не проглатывает — копии уходят из микрофона, а
    не из sys как «утечка владельца», и «Вы» он не становится."""
    sampled = set(np.linspace(0, 106, 60).round().astype(int).tolist())
    between = [k for k in range(107) if k not in sampled][::7][:7]
    got, _ = _run(_small_neighbour(tmp_path, set(between)), _owner())
    assert got.voices["fast"] is False
    assert got.report["dropped"]["owner_leak"] == 0 and got.report["dropped"]["neighbour"] == 7
    assert sum(s.speaker == "SPEAKER_01" for s in got.sys) == 7
    assert not any("сосед" in s.text and s.speaker == "Вы" for s in got.mic)


def test_fast_path_decides_on_every_window(tmp_path):
    """Один владелец: быстрый путь — по голосу всех окон."""
    total = 260.0
    mic = Track(total)
    segs = []
    for i in range(90):
        ws = _words("я согласен давайте так", 2.0 + i * 2.8)
        mic.say(ws, OWNER, LOUD, seed=i)
        segs.append(_seg(ws))
    meeting = (segs, [], mic.write(tmp_path / "mic16.wav"), Track(total).write(tmp_path / "sys16.wav"))
    calls = []

    def counting(audio):
        calls.append(1)
        return fake_embed(audio)

    got, _ = _run(meeting, _owner(), embed=counting)
    assert got.report["status"] == "ok" and got.voices["fast"] is True
    assert len(calls) == len(mic_split.windows(segs)) == 90


def test_embedder_failing_mid_run_falls_back_to_today(tmp_path):
    """Эмбеддер падает посреди окон (нехватка памяти видеокарты): расшифровка
    не ломается — skipped_error и микрофон как раньше."""
    calls = []

    def flaky(audio):
        calls.append(1)
        if len(calls) > 3:
            raise RuntimeError("CUDA out of memory")
        return fake_embed(audio)

    sys_phrases = [(_words(t, s + LEAK_LAG), OWNER) for s, t in OWNER_PHRASES]
    got, logs = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), _owner(), embed=flaky)
    assert got.report["status"] == "skipped_error" and {s.speaker for s in got.mic} == {"Вы"}
    assert got.report["dropped"]["owner_leak"] == 0 and len(got.sys) == len(OWNER_PHRASES)
    assert any("RuntimeError" in line for line in logs)


def test_break_segments_are_kept_and_never_windowed(tmp_path):
    meeting = _meeting(tmp_path)
    brk = Segment(40.0, 40.0, "", kind="break")
    segs = sorted(meeting[0] + [brk], key=lambda s: s.start)
    assert all(k[0] != segs.index(brk) for w in mic_split.windows(segs) for k in w.keys)
    sys_segs = [brk, _seg(_words("коллеги всем добрый день", 60.0), speaker="SPEAKER_00")]
    got, _ = _run((segs, sys_segs, *meeting[2:]), _owner())
    assert brk in got.mic and got.sys == sys_segs
    assert all(t.seg[1] != 0 for t in mic_split._toks(sys_segs, "sys"))


# --- дубли ----------------------------------------------------------------------


def test_room_speaker_heard_through_the_call_is_dropped_from_mic(tmp_path):
    """Человек в комнате говорит в свой ноутбук: его фразы приходят и в sys
    (на 0,4 с позже) — в микрофоне они лишние."""
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    meeting = _meeting(tmp_path, room1=True, sys_phrases=sys_phrases)
    got, _ = _run(meeting, _owner())
    assert _by_speaker(got.mic) == {"Вы": [s for s, _ in OWNER_PHRASES]}
    assert got.report["dropped"] == {"echo": 0, "neighbour": 4, "owner_leak": 0}
    assert len(got.sys) == 4 and got.sys == meeting[1]
    assert [d["reason"] for d in got.dropped] == ["neighbour"] * 4
    assert got.voices["dropped"] == got.dropped
    assert got.voices["lag_s"]["neighbour"] == pytest.approx(0.4, abs=0.01)
    # Все его слова ушли дублями: в расшифровке и сайдкаре его нет, а в
    # mic_voices.json кластер остаётся.
    assert got.report["room_speakers"] == 0
    assert [e["label"] for e in got.sidecar] == ["OWNER"]
    assert "room" in {c["role"] for c in got.voices["clusters"]}


def test_speaker_echo_is_dropped_from_mic(tmp_path):
    """Колонки вместо наушников: собеседник из sys тихо звучит в микрофоне
    чуть позже — эхо уходит из микрофона, в sys всё остаётся."""
    remote = [(_words(t, s), ROOM2) for s, t in ROOM2_PHRASES]
    echo = [(_words(t, s + 0.03), ROOM2, QUIET) for s, t in ROOM2_PHRASES]
    meeting = _meeting(tmp_path, extra_mic=echo, sys_phrases=remote)
    got, _ = _run(meeting, _owner())
    assert _by_speaker(got.mic) == {"Вы": [s for s, _ in OWNER_PHRASES]}
    assert got.report["dropped"] == {"echo": 4, "neighbour": 0, "owner_leak": 0}
    assert len(got.sys) == 4
    assert got.voices["lag_s"]["echo"] == pytest.approx(-0.03, abs=0.01)


def test_owner_voice_leaking_into_sys_is_dropped_from_sys(tmp_path):
    sys_phrases = [(_words(t, s + LEAK_LAG), OWNER) for s, t in OWNER_PHRASES]
    other = _words("коллеги всем добрый день начнём", 60.0)
    meeting = _meeting(tmp_path, sys_phrases=sys_phrases + [(other, ROOM2)])
    got, _ = _run(meeting, _owner())
    assert [s.text for s in got.sys] == ["коллеги всем добрый день начнём"]
    assert len(got.mic) == len(OWNER_PHRASES)
    assert got.report["dropped"]["owner_leak"] == 4
    assert got.dropped[0]["track"] == "sys"
    # Голос решён один раз по центроиду всех копий-кандидатов.
    leak = got.voices["leak"]
    assert leak["gate"] == "voice_match" and leak["candidates"] == 4 and leak["cos"] >= mic_split.T_OWN


def test_same_words_in_sys_by_another_voice_are_not_an_owner_leak(tmp_path):
    """Собеседник в sys повторил мои фразы с тем же лагом и ритмом, но голос
    копии — не мой (по образцу): его слова в sys остаются."""
    sys_phrases = [(_words(t, s + LEAK_LAG), ROOM2) for s, t in OWNER_PHRASES]
    meeting = _meeting(tmp_path, sys_phrases=sys_phrases)
    got, _ = _run(meeting, _owner())
    assert got.report["dropped"]["owner_leak"] == 0 and got.sys == meeting[1]
    # Кандидаты были (лаг и огибающая), но центроид их голоса — не владелец.
    leak = got.voices["leak"]
    assert leak["gate"] == "voice_mismatch" and leak["candidates"] == 4 and leak["cos"] < mic_split.T_OWN


def test_alignment_state_is_recorded(tmp_path):
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    got, _ = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), _owner(), aligned=True)
    assert got.voices["align"] is True and got.voices["lag_s"]["align"] is True
    got, _ = _run(_meeting(tmp_path, room1=True), _owner())
    assert got.voices["align"] is None


def test_partial_sys_drop_keeps_rest_of_segment(tmp_path):
    """Утечка — только часть длинной реплики sys: остальное остаётся."""
    leaks = [_words(t, s + LEAK_LAG) for s, t in OWNER_PHRASES]
    tail = _words("а ещё про отпуск хотел спросить", leaks[1][-1].end + 0.3)
    meeting = _meeting(tmp_path)
    sys_track = Track()
    for n, leak in enumerate(leaks):
        sys_track.say(leak, OWNER, LOUD, seed=n)
    sys_track.say(tail, ROOM2, LOUD, seed=9)
    sys_segs = [_seg(leaks[0], speaker="SPEAKER_00"), _seg(leaks[1] + tail, speaker="SPEAKER_00"),
                _seg(leaks[2], speaker="SPEAKER_00"), _seg(leaks[3], speaker="SPEAKER_00")]
    meeting = (meeting[0], sys_segs, meeting[2], sys_track.write(tmp_path / "sys16.wav"))
    got, _ = _run(meeting, _owner())
    (rest,) = got.sys
    assert rest.text == "а ещё про отпуск хотел спросить" and rest.start == tail[0].start
    assert rest.speaker == "SPEAKER_00" and [w.text for w in rest.words] == [w.text for w in tail]


def test_dedupe_switched_off(tmp_path):
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    got, _ = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), _owner(), dedupe=False)
    assert got.dropped == [] and "SPEAKER_M0" in _by_speaker(got.mic)


def test_without_sample_only_quiet_copies_are_dropped(tmp_path):
    """Без образца: тихая копия соседа уходит, громкий владелец — никогда."""
    sys_phrases = [(_words(t, s + 0.4), ROOM1) for s, t in ROOM1_PHRASES]
    sys_phrases += [(_words(OWNER_PHRASES[0][1], OWNER_PHRASES[0][0] + LEAK_LAG), OWNER)]
    got, _ = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), [])
    assert _by_speaker(got.mic) == {"Вы": [s for s, _ in OWNER_PHRASES]}
    assert got.report["dropped"] == {"echo": 0, "neighbour": 4, "owner_leak": 0}
    assert len(got.sys) == 5


def test_loaded_embedder_is_really_released(tmp_path, monkeypatch):
    """N4: эмбеддер, загруженный разделением, после него не держит никто —
    ни сам run, ни замыкание проверки утечек (иначе empty_cache не вернёт
    видеопамять)."""
    import gc
    import weakref

    from meet import segvoices

    class Embedder:
        def __call__(self, audio):
            return fake_embed(audio)

    refs = []

    def load():
        model = Embedder()
        refs.append(weakref.ref(model))
        return model

    alive_at_release = []

    def release():
        gc.collect()
        alive_at_release.append(refs[0]() is not None)

    monkeypatch.setattr(segvoices, "load_embedder", load)
    monkeypatch.setattr(mic_split, "_release", release)
    sys_phrases = [(_words(t, s + LEAK_LAG), OWNER) for s, t in OWNER_PHRASES]
    got, _ = _run(_meeting(tmp_path, room1=True, sys_phrases=sys_phrases), _owner(), embed=None)
    assert got.voices["leak"]["gate"] == "voice_match"
    # Когда отпускается видеопамять, модель уже никому не нужна.
    assert alive_at_release == [False]
