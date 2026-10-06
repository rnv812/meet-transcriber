"""Живые голоса (meet.live_voices): онлайн-кластеры, политика имён, роль
микрофона. Эмбеддер фальшивый: голос сегмента зашит в его звук (значение
первого сэмпла → вектор)."""

import numpy as np

from meet import live_voices as lv
from meet.owner_voice import OwnerSample

SR = 16000
IVAN = np.array([1.0, 0.0, 0.0, 0.0])
PETR = np.array([0.0, 1.0, 0.0, 0.0])
OWNER = np.array([0.0, 0.0, 1.0, 0.0])
ROOM = np.array([0.0, 0.0, 0.0, 1.0])
# Голос по коду сэмпла: 0.01 → IVAN и т. д.
VOICES = {1: IVAN, 2: PETR, 3: OWNER, 4: ROOM}


def clip(code: int, seconds: float = 2.0) -> np.ndarray:
    return np.full(int(seconds * SR), code / 100.0, dtype=np.float32)


def fake_embed(calls=None):
    def embed(audio):
        if calls is not None:
            calls.append(len(audio))
        return VOICES[int(round(float(audio[0]) * 100))]
    return embed


def owner_samples():
    return [OwnerSample(id="a", embedding=OWNER * 1.7, source="enroll", date="2026-10-05", seconds=25.0)]


def voices(base=None, owner=None, **kw):
    # База — как у community-1: векторы не единичной длины.
    base = {"Демьян": [IVAN * 2.5], "Пётр": [PETR * 0.9]} if base is None else base
    kw.setdefault("name_threshold", 0.70)
    kw.setdefault("log", lambda line: None)
    kw.setdefault("session", "s")
    return lv.LiveVoices(fake_embed(kw.pop("calls", None)), base, owner,
                         defaults={"sys": "Собеседник", "mic": "Вы"}, **kw)


def feed(v, track, code, count, start=0.0, seconds=2.0, gap=0.2):
    out = []
    t = start
    for _ in range(count):
        out.append(v.assign(track, clip(code, seconds), t, t + seconds))
        t += seconds + gap
    return out, t


# --- TrackVoices ------------------------------------------------------------------


def test_same_voice_one_cluster_other_voice_new():
    tv = lv.TrackVoices("sys")
    a = tv.observe(IVAN, 0.0, 2.0)
    b = tv.observe(IVAN, 2.2, 4.2)
    c = tv.observe(PETR, 4.4, 6.4)
    assert a == b == "sys:0"
    assert c == "sys:1"
    assert tv.clusters[0].seconds == 4.0


def test_short_segment_inherits_previous_within_pause():
    tv = lv.TrackVoices("sys")
    tv.observe(IVAN, 0.0, 2.0)
    assert tv.observe(None, 3.0, 3.5) == "sys:0"  # пауза 1 с ≤ 1,5
    assert tv.observe(None, 10.0, 10.5) is None  # далеко — голоса нет


def test_new_cluster_only_from_two_seconds():
    tv = lv.TrackVoices("sys")
    tv.observe(IVAN, 0.0, 2.0)
    # Не похож, но короче 2 с: кластер предыдущего сегмента при паузе ≤ 1 с.
    assert tv.observe(PETR, 2.5, 4.0) == "sys:0"
    assert tv.observe(PETR, 20.0, 21.5) is None
    assert len(tv.live()) == 1


def test_previous_found_by_time_when_catchup_interleaves():
    tv = lv.TrackVoices("sys")
    tv.observe(IVAN, 100.0, 102.0)  # живое окно
    tv.observe(PETR, 5.0, 7.0)  # догонялка начала встречи
    assert tv.observe(None, 102.5, 103.0) == "sys:0"
    assert tv.observe(None, 7.5, 8.0) == "sys:1"


def test_close_clusters_merge_when_both_long():
    tv = lv.TrackVoices("sys")
    a = tv._new(IVAN, 16.0)
    b = tv._new(lv._unit(np.array([0.75, 0.66, 0.0, 0.0])), 20.0)  # cos ≈ 0.75
    c = tv._new(PETR, 16.0)
    assert tv.merge_close() == 1
    assert tv.resolve(a).n == b and tv.resolve(b).n == b  # больший остаётся
    assert tv.resolve(c).n == c
    assert tv.resolve(b).seconds == 36.0


def test_short_clusters_do_not_merge():
    tv = lv.TrackVoices("sys")
    tv._new(IVAN, 5.0)
    tv._new(lv._unit(np.array([0.75, 0.66, 0.0, 0.0])), 20.0)
    assert tv.merge_close() == 0


def test_cluster_cap_never_mixes_a_foreign_voice_into_a_cluster():
    """Потолок кластеров: чужой голос не вливается в ближайший (его центроид и
    имя — чужие) — самый старый мелкий безымянный кластер замораживается."""
    tv = lv.TrackVoices("sys", max_clusters=2)
    tv.observe(IVAN, 0.0, 2.0)
    tv.clusters[0].name = "Демьян"
    tv.observe(PETR, 3.0, 5.0)
    before = tv.clusters[0].total.copy()
    assert tv.observe(OWNER, 6.0, 8.0) == "sys:2"
    assert tv.clusters[1].retired and len(tv.live()) == 2
    assert np.allclose(tv.clusters[0].total, before)  # центроид Демьяна не тронут
    # Заморозить нечего (все названы или крупные) — голос без кластера, не «Демьян».
    tv.clusters[2].name = "Пётр"
    assert tv.observe(ROOM, 20.0, 22.0) is None
    assert np.allclose(tv.clusters[0].total, before)


def test_twelve_clusters_and_a_thirteenth_voice_shows_no_name():
    base = {"Демьян": [IVAN]}
    v = voices(base=base)
    feed(v, "sys", 1, 6)  # Демьян назван
    for i in range(11):  # ещё 11 мелких голосов
        VOICES[20 + i] = lv._unit(np.eye(16)[4 + i])
    VOICES[40] = lv._unit(np.eye(16)[15])
    try:
        b16 = {"Демьян": [np.eye(16)[0]]}
        v = lv.LiveVoices(fake_embed(), b16, None, name_threshold=0.7, session="s", log=lambda line: None)
        VOICES[1] = np.eye(16)[0]
        feed(v, "sys", 1, 6, start=0.0)
        t = 100.0
        for i in range(11):
            v.assign("sys", clip(20 + i), t, t + 2.0)
            t += 10.0
        got = v.assign("sys", clip(40), t, t + 2.0)
    finally:
        VOICES[1] = IVAN
        for i in range(11):
            VOICES.pop(20 + i)
        VOICES.pop(40)
    assert got.speaker == "Собеседник"
    assert len(v._tracks["sys"].live()) == lv.MAX_CLUSTERS


def test_merge_never_crosses_mic_roles():
    """Ревью I3: большой кластер «человек рядом» не поглощает кластер владельца."""
    tv = lv.TrackVoices("mic")
    room = lv._Cluster(0, lv._unit(np.array([0.0, 0.0, 0.40, 0.9165])) * 40, 40, role=lv.ROOM)
    own = lv._Cluster(1, lv._unit(np.array([0.0, 0.0, 0.78, 0.6258])) * 16, 16, role=lv.OWNER)
    tv.clusters, tv._next = {0: room, 1: own}, 2
    assert float(room.center @ own.center) >= 0.65
    assert tv.merge_close() == 0
    # Слияние разных ролей другим путём — итог владелец: «рядом» надо заслужить заново.
    kept = tv.merge(room, own)
    assert kept.role == lv.OWNER and kept.room_streak == 0


def test_merge_hands_a_name_only_if_the_merged_voice_earns_it():
    """Ревью M2: безымянный кластер получает имя слиянием, только если общий
    центроид сам проходит проверку имени."""
    refused = lv.TrackVoices("sys", accept_name=lambda center, name: False)
    accepted = lv.TrackVoices("sys", accept_name=lambda center, name: True)
    for tv in (refused, accepted):
        tv._new(IVAN, 16.0)
        tv.clusters[0].name = "Демьян"
        tv._new(lv._unit(np.array([0.75, 0.66, 0.0, 0.0])), 20.0)
    assert refused.merge_close() == 0
    assert accepted.merge_close() == 1 and accepted.resolve(0).name == "Демьян"


# --- имена ------------------------------------------------------------------------


def test_name_after_eight_seconds_and_two_checks_then_retro_rename():
    v = voices()
    got, _ = feed(v, "sys", 1, 6)  # 2 с × 6 = 12 с речи
    # До 8 с речи — «Собеседник»; проверки на 8 и 12 с — две подряд → имя.
    assert [a.speaker for a in got] == ["Собеседник"] * 5 + ["Демьян"]
    assert {a.voice for a in got} == {"s/sys:0"}
    assert v.drain() == [("s/sys:0", "Демьян")]  # первые пять строк — задним числом
    assert v.drain() == []
    assert v.speakers() == {"s/sys:0": "Демьян"}


def test_below_threshold_never_named():
    # cos 0.66 с образцом: ниже T_live 0.70 (лучший чужой у неизвестных — до 0.66).
    near = lv._unit(np.array([0.66, 0.0, 0.0, 0.75]))
    VOICES[9] = near
    try:
        v = voices()
        got, _ = feed(v, "sys", 9, 12)
    finally:
        del VOICES[9]
    assert all(a.speaker == "Собеседник" for a in got)
    assert v.drain() == []


def test_threshold_comes_from_caller():
    near = lv._unit(np.array([0.66, 0.0, 0.0, 0.75]))
    VOICES[9] = near
    try:
        v = voices(name_threshold=0.60)
        got, _ = feed(v, "sys", 9, 6)
    finally:
        del VOICES[9]
    assert got[-1].speaker == "Демьян"


def test_small_margin_never_named():
    v = voices(base={"Демьян": [IVAN], "Двойник": [lv._unit(np.array([1.0, 0.05, 0.0, 0.0]))]})
    got, _ = feed(v, "sys", 1, 10)
    assert all(a.speaker == "Собеседник" for a in got)


def test_two_people_get_their_own_names():
    v = voices()
    a, t = feed(v, "sys", 1, 6)
    b, _ = feed(v, "sys", 2, 7, start=t + 1.0)  # смена говорящего — с паузой
    assert a[-1] == ("s/sys:0", "Демьян", lv.OTHER)
    assert b[-1].voice == "s/sys:1" and b[-1].speaker == "Пётр"


def test_keys_carry_the_session():
    """Ревью I4: новый ассистент в той же записи — другие ключи голосов."""
    one, two = voices(session=None), voices(session=None)
    a, _ = feed(one, "sys", 1, 1)
    b, _ = feed(two, "sys", 1, 1)
    assert a[0].voice.endswith("/sys:0") and b[0].voice.endswith("/sys:0")
    assert a[0].voice != b[0].voice
    assert lv.track_of(a[0].voice) == "sys"


def test_name_taken_by_other_cluster_merges_them():
    # У Демьяна два образца из разных встреч; в живом режиме его голос сперва
    # похож на один, потом на другой — кластеры разные, но оба уверены в Демьяне.
    ivan2 = np.array([0.0, 0.0, 0.0, 1.0])
    v = voices(base={"Демьян": [IVAN, ivan2], "Пётр": [PETR]})
    feed(v, "sys", 1, 6)
    got, _ = feed(v, "sys", 4, 6, start=20.0)
    assert got[-1].voice == "s/sys:1"
    tv = v._tracks["sys"]
    assert tv.resolve(1).n == tv.resolve(0).n  # слиты
    assert v.speaker("s/sys:1") == "Демьян"
    assert ("s/sys:1", "Демьян") in v.drain()


def test_pinned_name_dropped_only_after_doubling_and_big_drop():
    v = voices()
    feed(v, "sys", 1, 6)
    v.drain()
    tv = v._tracks["sys"]
    c = tv.clusters[0]
    named = c.named_s
    # Голос кластера «уехал» (cos с Демьяном 0.5), но речи ещё не вдвое больше — держим.
    c.total = lv._unit(np.array([0.5, 0.0, 0.0, 0.866])) * c.seconds
    v._check_name(tv, c)
    assert c.name == "Демьян"
    c.seconds = 2 * named
    v._check_name(tv, c)
    assert c.name is None
    assert v.drain() == [("s/sys:0", "Собеседник")]


def test_pinned_name_kept_when_score_holds():
    v = voices()
    feed(v, "sys", 1, 6)
    tv = v._tracks["sys"]
    c = tv.clusters[0]
    c.seconds = 2 * c.named_s
    v._check_name(tv, c)
    assert c.name == "Демьян" and c.named_s == c.seconds


def test_no_base_means_no_embeddings_on_sys():
    calls = []
    v = voices(base={}, calls=calls)
    a = v.assign("sys", clip(1), 0.0, 2.0)
    assert a == (None, "Собеседник", lv.OTHER) and calls == []


def test_short_clip_not_embedded():
    calls = []
    v = voices(calls=calls)
    a = v.assign("sys", clip(1, 0.5), 0.0, 0.5)
    assert a.voice is None and calls == []


def test_embedder_error_keeps_default_speaker():
    def broken(audio):
        raise RuntimeError("cuda died")

    lines = []
    v = lv.LiveVoices(broken, {"Демьян": [IVAN]}, None, name_threshold=0.7, log=lines.append)
    for i in range(3):
        a = v.assign("sys", clip(1), i * 3.0, i * 3.0 + 2.0)
        assert a.voice is None and a.speaker == "Собеседник"
    assert len(lines) == 1  # один и тот же сбой — одна строка


def test_stats_count_embeddings():
    ticks = iter(x * 0.05 for x in range(100))
    v = voices(clock=lambda: next(ticks))
    feed(v, "sys", 1, 3)
    assert v.stats["embeds"] == 3
    assert abs(v.stats["embed_s"] - 0.15) < 1e-9
    assert "эмбеддингов 3" in v.stats_line()


# --- микрофон ---------------------------------------------------------------------


def test_mic_without_owner_sample_is_plain_owner():
    calls = []
    v = voices(owner=None, calls=calls)
    a = v.assign("mic", clip(3), 0.0, 2.0)
    assert a == (None, "Вы", lv.OWNER) and calls == []


def test_mic_disabled_by_setting():
    v = voices(owner=owner_samples(), mic=False)
    assert v.assign("mic", clip(3), 0.0, 2.0).voice is None


def test_mic_owner_lines_stay_owner():
    v = voices(owner=owner_samples())
    got, _ = feed(v, "mic", 3, 8)
    assert all(a.speaker == "Вы" and a.role == lv.OWNER for a in got)
    assert {a.voice for a in got} == {"s/mic:owner"}  # якорь владельца
    assert v.drain() == []


def test_mic_room_cluster_renamed_retroactively():
    v = voices(owner=owner_samples())
    own, t = feed(v, "mic", 3, 6)
    room, _ = feed(v, "mic", 4, 8, start=t)
    # Первые сегменты чужого голоса — «Вы» с ролью unsure (их задержат дубли);
    # проверки роли на 10 и 14 с речи — ниже T_OTHER дважды → человек рядом.
    assert room[0].speaker == "Вы" and room[0].role == lv.UNSURE
    assert room[5].speaker == "Вы"  # 12 с — одна проверка, мало
    assert room[-1].speaker == lv.ROOM_SPEAKER and room[-1].role == lv.ROOM
    assert v.drain() == [("s/mic:0", lv.ROOM_SPEAKER)]
    assert all(a.speaker == "Вы" for a in own)


def test_mic_room_person_from_base_gets_name():
    v = voices(owner=owner_samples())
    _, t = feed(v, "mic", 3, 6)
    got, _ = feed(v, "mic", 1, 10, start=t)  # голос Демьяна в микрофоне рядом с владельцем
    assert got[-1].speaker == "Демьян" and got[-1].role == lv.ROOM
    assert v.drain() == [("s/mic:0", "Демьян")]


def test_mic_without_owner_voice_stays_owner():
    """Ревью C1 (§2.4): в микрофоне только чужой голос — владелец не найден,
    микрофон весь владельца, как без образца."""
    lines = []
    v = voices(owner=owner_samples(), log=lines.append)
    got, _ = feed(v, "mic", 1, 12)
    assert all(a.speaker == "Вы" for a in got)
    assert got[-1].role == lv.OWNER
    assert v.drain() == []
    assert sum("не найден" in line for line in lines) == 1


def test_mic_sample_from_another_mic_keeps_owner():
    """Ревью C1: образец с другого микрофона (cos 0.55) — голос владельца не
    становится «Собеседник рядом»."""
    VOICES[9] = lv._unit(np.array([0.0, 0.0, 0.55, 0.835]))
    try:
        v = voices(owner=owner_samples())
        got, _ = feed(v, "mic", 9, 15)
    finally:
        del VOICES[9]
    assert all(a.speaker == "Вы" and a.role == lv.OWNER for a in got)
    assert v.drain() == []


def test_owner_with_odd_segments_never_yields_room():
    """Ревью C1: редкие нетипичные сегменты владельца (смех, эхо) — мелкий
    кластер, а не «человек рядом»."""
    VOICES[9] = lv._unit(np.array([0.0, 0.0, 0.40, 0.917]))  # cos 0.40 с образцом
    try:
        v = voices(owner=owner_samples())
        got, t = [], 0.0
        for i in range(24):
            code, seconds = (9, 2.5) if i % 6 == 5 else (3, 2.0)
            got.append(v.assign("mic", clip(code, seconds), t, t + seconds))
            t += seconds + 0.2
    finally:
        del VOICES[9]
    assert all(a.speaker == "Вы" for a in got)
    assert v.drain() == []


def test_mic_undecided_cluster_owner_like_segment_is_owner():
    v = voices(owner=owner_samples())
    a = v.assign("mic", clip(3), 0.0, 2.0)
    assert a.role == lv.OWNER  # не задерживается: похож на образец


# --- экономия процессора (ревью I5) -------------------------------------------------


def test_short_segment_below_one_and_a_half_seconds_not_embedded():
    calls = []
    v = voices(calls=calls)
    assert v.assign("sys", clip(1, 1.4), 0.0, 1.4).voice is None
    assert calls == []


def test_long_segment_embeds_only_its_middle():
    calls = []
    v = voices(calls=calls)
    v.assign("sys", clip(1, 6.0), 0.0, 6.0)
    assert calls == [3 * SR]
    assert v._tracks["sys"].clusters[0].seconds == 6.0  # вес — весь сегмент


def test_confident_owner_embeds_every_third_segment():
    calls = []
    v = voices(owner=owner_samples(), calls=calls)
    feed(v, "mic", 3, 15)  # 30 с владельца — каждый сегмент
    assert len(calls) == 15
    got, _ = feed(v, "mic", 3, 9, start=33.0)
    assert len(calls) == 15 + 3
    assert {a.voice for a in got} == {"s/mic:owner"}
    assert v.stats["skipped"] == 6


def test_foreign_voice_on_mic_restores_every_segment():
    calls = []
    v = voices(owner=owner_samples(), calls=calls)
    feed(v, "mic", 3, 15)
    v.assign("mic", clip(3), 33.0, 35.0)  # пропущен
    n = len(calls)
    v.assign("mic", clip(3), 35.2, 37.2)  # пропущен
    v.assign("mic", clip(4), 37.4, 39.4)  # третий — посчитан, чужой голос
    v.assign("mic", clip(4), 39.6, 41.6)  # дальше — каждый
    assert len(calls) == n + 2


def test_pinned_speaker_back_to_back_embeds_every_second_segment():
    calls = []
    v = voices(calls=calls)
    feed(v, "sys", 1, 6)  # Демьян назван на 6-м
    n = len(calls)
    got, t = feed(v, "sys", 1, 4, start=13.2)  # без пауз (0,2 с)
    assert len(calls) == n + 2
    assert all(a.speaker == "Демьян" for a in got)
    # С паузой больше PINNED_GAP_S — снова каждый.
    feed(v, "sys", 1, 2, start=t + 1.0, gap=1.0)
    assert len(calls) == n + 4


def test_found_owner_stays_found_when_the_room_talks_more():
    """Доля владельца падает ниже 30 % — найденный владелец не «теряется»
    (иначе «Собеседник рядом» мигал бы с «Вы»), пока она от 15 %."""
    lines = []
    v = voices(owner=owner_samples(), log=lines.append)
    _, t = feed(v, "mic", 3, 6)  # 12 с владельца
    room, _ = feed(v, "mic", 4, 20, start=t)  # 40 с человека рядом: доля владельца 23 %
    assert room[-1].speaker == lv.ROOM_SPEAKER
    assert not any("не найден" in line for line in lines)
    assert v.drain() == [("s/mic:0", lv.ROOM_SPEAKER)]
