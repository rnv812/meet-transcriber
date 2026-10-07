"""Живые голоса (meet.live_voices): онлайн-кластеры, политика имён, роль
микрофона. Эмбеддер фальшивый: голос сегмента зашит в его звук (значение
первого сэмпла → вектор)."""

import numpy as np
import pytest

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


def test_no_base_still_clusters_sys_without_names():
    """Без базы называть некого, но голоса собеседников делятся: ключ есть,
    подпись — «Собеседник»."""
    calls = []
    v = voices(base={}, calls=calls)
    a = v.assign("sys", clip(1), 0.0, 2.0)
    assert a == ("s/sys:0", "Собеседник", lv.OTHER) and calls == [2 * SR]


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
    assert sum(("пока не найден" in line or "больше не найден" in line) for line in lines) == 1


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


def test_short_segment_below_one_and_a_half_seconds_starts_no_cluster():
    """Реплика 1–1,5 с собеседников считается (её решат по голосам, когда их
    станет два), но кластера не заводит: без соседа — без голоса."""
    calls = []
    v = voices(calls=calls)
    assert v.assign("sys", clip(1, 1.4), 0.0, 1.4).voice is None
    assert calls == [int(1.4 * SR)] and not v._tracks["sys"].live()


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
    assert not any(("пока не найден" in line or "больше не найден" in line) for line in lines)
    assert v.drain() == [("s/mic:0", lv.ROOM_SPEAKER)]


# --- реалистичный разброс эмбеддингов (ревью, раунд 2) --------------------------------
# Окна голоса не одинаковы: cos(окно, голос) ≈ 0.8, сходство окна владельца с
# образцом разбросано (sd ≈ 0.12; T0: медиана 0.76, p5 0.51). При шумном образце
# часть окон владельца ниже T_WIN_FAST и складывается в свой кластер — он не
# должен стать «Собеседник рядом». c0 — cos голоса владельца с образцом.

SPREAD_D = 25
SPREAD_SIG = 0.75


def _u(x):
    return x / np.linalg.norm(x)


def _toward(rng, o, c):
    u = rng.standard_normal(SPREAD_D)
    u = _u(u - (u @ o) * o)
    return _u(c * o + np.sqrt(1 - c ** 2) * u)


def spread_run(c0, plan, seed=0, sample_of="owner", short_share=0.0, drift=None):
    """`plan` — [(кто, секунды)]: owner, room или b (третий человек, cos 0.35
    с владельцем). Образец — голоса `sample_of` с качеством c0. `short_share`
    — доля коротких реплик 1–1,5 с (их эмбеддинг шумнее). `drift` — (cd, p):
    у владельца второй «режим» голоса (cos cd с обычным), им сказана доля p
    его окон (сел голос, смех, другая интонация)."""
    rng = np.random.default_rng(seed)
    o = _u(rng.standard_normal(SPREAD_D))
    r = rng.standard_normal(SPREAD_D)
    r = _u(r - (r @ o) * o * 0.6)  # человек рядом: cos с владельцем ≈ 0.3–0.4
    voice = {"owner": o, "room": r, "b": _toward(rng, o, 0.35)}
    sample = _toward(rng, voice[sample_of], c0)
    second = _toward(rng, o, drift[0]) if drift else None
    nxt = {}
    v = lv.LiveVoices(lambda clip: nxt["v"], {},
                      [OwnerSample(id="a", embedding=sample, source="enroll", date="d", seconds=25.0)],
                      name_threshold=0.70, session="t", log=lambda line: None)
    t, lines = 0.0, []
    for who, total in plan:
        said = 0.0
        while said < total:
            short = bool(short_share) and rng.random() < short_share
            d = float(rng.uniform(1.0, 1.5)) if short else float(rng.uniform(1.5, 4.0))
            sig = SPREAD_SIG * (1.6 if short else 1.0)
            mode = second if drift and who == "owner" and rng.random() < drift[1] else voice[who]
            nxt["v"] = _u(mode + sig * _u(rng.standard_normal(SPREAD_D)))
            a = v.assign("mic", np.zeros(int(d * SR), dtype=np.float32), t, t + d)
            lines.append((who, a.voice, d))
            t += d + 0.4
            said += d
    out = {}
    for who, key, d in lines:
        label = v.speaker(key) if key else "Вы"
        out[(who, label)] = out.get((who, label), 0.0) + d
    return out, v


def test_noisy_samples_never_turn_the_owner_into_a_room_speaker():
    for c0 in (0.55, 0.65, 0.70, 0.86):
        for seed in range(3):
            out, _ = spread_run(c0, [("owner", 900)], seed)
            shown_as_room = sum(x for (who, label), x in out.items() if label != "Вы")
            assert shown_as_room == 0, (c0, seed, out)


def test_real_room_person_still_detected_with_spread():
    for c0 in (0.70, 0.86):
        for seed in range(3):
            out, _ = spread_run(c0, [("owner", 30), ("room", 20)] * 10, seed)
            owner_as_room = sum(x for (who, label), x in out.items() if who == "owner" and label != "Вы")
            room = sum(x for (who, label), x in out.items() if who == "room")
            room_as_room = out.get(("room", lv.ROOM_SPEAKER), 0.0)
            assert owner_as_room == 0, (c0, seed, out)
            assert room_as_room >= 0.8 * room, (c0, seed, out)


def test_bad_sample_keeps_the_whole_mic_as_owner_like_offline():
    out, _ = spread_run(0.60, [("owner", 30), ("room", 20)] * 10, 0)
    assert all(label == "Вы" for (_, label) in out)  # офлайн: owner_not_found


def test_room_lines_do_not_flip_to_owner_after_the_owner_leaves():
    """Владелец найден по сильной улике — «найден» до конца сеанса: долгий
    кусок только с человеком рядом не переписывает его строки в «Вы»."""
    for seed in range(3):
        out, v = spread_run(0.86, [("owner", 60), ("room", 600)], seed)
        assert v._latched and v._present
        assert out.get(("room", lv.ROOM_SPEAKER), 0.0) >= 0.9 * 600


def test_room_cluster_near_the_owner_anchor_reverts():
    v = voices(owner=owner_samples())
    feed(v, "mic", 3, 6)
    tv = v._tracks["mic"]
    c = tv.clusters[tv._new(ROOM, 12.0)]
    c.role, v._present = lv.ROOM, True
    assert v.speaker("s/mic:0") == lv.ROOM_SPEAKER
    c.total = lv._unit(np.array([0.0, 0.0, 0.7, 0.714])) * 12.0  # cos с якорем 0.70
    assert v.speaker("s/mic:0") == "Вы"
    v._check_role(c)
    assert c.role == lv.OWNER and c.room_streak == 0


def test_passer_by_matching_a_wrong_sample_does_not_latch():
    """Ревью, раунд 3: образец чужой (сосед по ноутбуку, ошибочное «Это я»);
    его хозяин заглянул на 40 с посреди встречи. Защёлка — только при доле
    группы якоря от 30 % из не меньше 60 с речи, так что прохожий не
    закрепляется, и речь владельца не становится «Собеседник рядом»."""
    for seed in range(3):
        out, v = spread_run(0.86, [("owner", 300), ("b", 40), ("owner", 1500)], seed, sample_of="b")
        assert not v._latched, seed
        assert out.get(("owner", lv.ROOM_SPEAKER), 0.0) == 0, (seed, out)


def test_latch_needs_share_and_volume():
    v = voices(owner=owner_samples())
    feed(v, "mic", 3, 15)  # 30 с владельца, но речи микрофона меньше 60 с
    assert not v._latched and v._present
    feed(v, "mic", 3, 48, start=40.0)  # каждый 3-й считается (экономия) — ещё ~30 с
    assert v._mic_voiced >= 60.0 and v._latched


def test_presence_changes_are_logged_with_the_labels():
    """Ревью, раунд 3 (N2): журнал совпадает с подписями — «найден», когда
    люди рядом начинают подписываться отдельно, «больше не найден», когда
    перестают."""
    lines = []
    v = voices(owner=owner_samples(), log=lines.append)
    _, t = feed(v, "mic", 3, 6)
    assert sum("микрофоне найден" in line for line in lines) == 1
    feed(v, "mic", 4, 40, start=t)  # владелец ушёл, долю потерял (без защёлки)
    assert sum("больше не найден" in line for line in lines) == 1


def test_session_prefix_is_32_bits():
    v = voices(session=None)
    assert len(v.session) == 8


# --- два голоса микрофона: встреча за одним ноутбуком -------------------------------
# Владелец и сосед говорят в один микрофон, реплики встык. Когда оба голоса
# известны (якорь владельца и человек рядом), сегмент решается сравнением с их
# живыми центроидами, а не порогом образца.


def _both_known(v):
    """Владелец 12 с, потом человек рядом 16 с: оба голоса известны."""
    _, t = feed(v, "mic", 3, 6)
    room, t = feed(v, "mic", 4, 8, start=t)
    assert room[-1].role == lv.ROOM and v._both(v._tracks["mic"])
    return t


def test_window_like_the_sample_stays_owner_even_when_closer_to_the_room_voice():
    """Образец защищает владельца: окно, похожее на образец (от T_WIN_FAST и
    тем более от T_OWN), — «Вы», даже если центроид соседа к нему ближе. Иначе
    второй «режим» голоса владельца, ошибочно ставший «рядом», забирал бы и его
    окна, похожие на образец."""
    for code, vec in ((9, 0.5 * OWNER + 0.866 * ROOM), (10, 0.8 * OWNER + 0.6 * ROOM)):
        VOICES[code] = lv._unit(vec)
    try:
        v = voices(owner=owner_samples())
        t = _both_known(v)
        near_sample = v.assign("mic", clip(9), t, t + 2.0)  # с образцом 0.5, с соседом 0.87
        like_sample = v.assign("mic", clip(10), t + 2.2, t + 4.2)  # с образцом 0.8 (от T_OWN)
    finally:
        del VOICES[9], VOICES[10]
    assert near_sample.speaker == "Вы" and near_sample.role == lv.OWNER
    assert like_sample.speaker == "Вы" and like_sample.voice == "s/mic:owner"


def test_segment_unlike_the_sample_and_closer_to_the_room_voice_is_room():
    """Окно соседа ниже T_WIN_FAST, но до LIVE_ASSIGN с его кластером не
    дотянуло: раньше — новый кластер («Вы»), теперь — сосед по двум голосам."""
    VOICES[9] = lv._unit(0.2 * OWNER + 0.5 * ROOM + 0.843 * IVAN)
    try:
        v = voices(owner=owner_samples())
        t = _both_known(v)
        got = v.assign("mic", clip(9), t, t + 2.0)
    finally:
        del VOICES[9]
    assert got.role == lv.ROOM and got.speaker == lv.ROOM_SPEAKER
    tv = v._tracks["mic"]
    assert tv.resolve(tv.number(got.voice)).seconds == 16.0  # центроид соседа далёким окном не вырос


def test_room_too_close_to_the_owner_anchor_turns_two_voices_off():
    """«Человек рядом», чей центроид близок к якорю владельца (cos от
    LIVE_ASSIGN), — скорее второй режим голоса самого владельца: сегменты
    решает прежний путь."""
    v = voices(owner=owner_samples())
    _both_known(v)
    tv = v._tracks["mic"]
    room = next(c for c in tv.live() if c.role == lv.ROOM)
    room.total = lv._unit(0.6 * OWNER + 0.8 * ROOM) * room.seconds  # cos с якорем 0.6
    assert v._is_room(room) and not v._both(tv)


def test_owner_segment_below_the_sample_threshold_stays_owner_when_both_known():
    VOICES[9] = lv._unit(0.40 * OWNER + 0.2 * ROOM + 0.894 * IVAN)  # с образцом 0.40
    try:
        v = voices(owner=owner_samples())
        t = _both_known(v)
        got = v.assign("mic", clip(9), t, t + 2.0)
    finally:
        del VOICES[9]
    assert got.speaker == "Вы" and got.role == lv.OWNER
    assert v._tracks["mic"].owner().seconds == 12.0  # не похож на образец — якорь не растёт


def test_short_reply_between_owner_phrases_is_decided_by_voice():
    calls = []
    v = voices(owner=owner_samples(), calls=calls)
    t = _both_known(v)
    feed(v, "mic", 3, 1, start=t)  # владелец…
    n = len(calls)
    # …и сразу «да» соседа на 1,2 с: раньше — без голоса, подпись предыдущего («Вы»).
    got = v.assign("mic", clip(4, 1.2), t + 2.2, t + 3.4)
    assert len(calls) == n + 1 and calls[-1] == int(1.2 * SR)
    assert got.role == lv.ROOM and got.speaker == lv.ROOM_SPEAKER
    tv = v._tracks["mic"]
    room = tv.resolve(tv.number(got.voice))
    before = room.seconds
    v.assign("mic", clip(4, 1.2), t + 3.5, t + 4.7)
    assert room.seconds == before  # короткая реплика центроид не растит


def test_short_reply_is_not_embedded_until_both_voices_are_known():
    calls = []
    v = voices(owner=owner_samples(), calls=calls)
    _, t = feed(v, "mic", 3, 6)
    n = len(calls)
    got = v.assign("mic", clip(4, 1.2), t, t + 1.2)
    assert len(calls) == n  # сосед ещё не известен — как раньше, без голоса
    assert got.voice == "s/mic:owner"  # подпись предыдущего сегмента


def test_reply_shorter_than_a_second_is_never_embedded():
    calls = []
    v = voices(owner=owner_samples(), calls=calls)
    t = _both_known(v)
    n = len(calls)
    v.assign("mic", clip(4, 0.9), t, t + 0.9)
    assert len(calls) == n


def test_voice_far_from_both_known_voices_gets_its_own_cluster():
    """Третий голос (не похож ни на владельца, ни на соседа) — не в «Вы» и не
    в соседа, а свой кластер (может стать своим человеком рядом)."""
    v = voices(owner=owner_samples())
    t = _both_known(v)
    got = v.assign("mic", clip(1), t, t + 2.5)  # голос Демьяна
    tv = v._tracks["mic"]
    c = tv.resolve(tv.number(got.voice))
    assert c.n != lv.OWNER_N and c.role != lv.ROOM
    assert c.seconds == pytest.approx(2.5)


def test_small_fragment_closer_to_the_room_voice_is_relabelled_retroactively():
    """Кусок голоса соседа, сказанный до того, как сосед стал известен, —
    мелкий кластер без роли («Вы»). Когда сосед известен, кусок вливается в
    него, и его строки получают подпись задним числом. Кусок, который ближе к
    владельцу, остаётся «Вы»."""
    v = voices(owner=owner_samples())
    feed(v, "mic", 3, 6)
    tv = v._tracks["mic"]
    near_room = tv.clusters[tv._new(lv._unit(0.3 * OWNER + 0.6 * ROOM + 0.742 * IVAN), 2.0)]
    near_owner = tv.clusters[tv._new(lv._unit(0.6 * OWNER + 0.3 * ROOM + 0.742 * IVAN), 2.0)]
    # Ближе к соседу, но похож на него меньше LIVE_ASSIGN — не вливается.
    loose = tv.clusters[tv._new(lv._unit(0.2 * OWNER + 0.5 * ROOM + 0.843 * PETR), 2.0)]
    for c in (near_room, near_owner, loose):
        v._shown[tv.key(c.n)] = v.speaker(tv.key(c.n))
    assert v.speaker(tv.key(near_room.n)) == "Вы"
    room = tv.clusters[tv._new(ROOM, 12.0)]
    room.role, v._present = lv.ROOM, True
    v._review("mic", tv)
    assert near_room.into == room.n and near_owner.into is None and loose.into is None
    assert room.role == lv.ROOM  # роль соседа не сбросилась, как при merge
    assert (tv.key(near_room.n), lv.ROOM_SPEAKER) in v.drain()
    assert v.speaker(tv.key(near_owner.n)) == "Вы"


def test_fast_turns_with_short_replies_split_owner_and_room():
    """Разговор лицом к лицу: короткие реплики встык, треть — 1–1,5 с.
    Владелец «рядом» почти не бывает, сосед почти не бывает «Вы»."""
    for c0 in (0.70, 0.86):
        for seed in range(3):
            out, _ = spread_run(c0, [("owner", 8), ("room", 4)] * 60, seed, short_share=0.4)
            owner = sum(x for (who, _), x in out.items() if who == "owner")
            room = sum(x for (who, _), x in out.items() if who == "room")
            owner_as_room = sum(x for (who, label), x in out.items() if who == "owner" and label != "Вы")
            room_as_you = out.get(("room", "Вы"), 0.0)
            assert owner_as_room <= 0.03 * owner, (c0, seed, out)
            assert room_as_you <= 0.10 * room, (c0, seed, out)


def test_fast_turns_with_a_bad_sample_keep_the_whole_mic_as_owner():
    out, _ = spread_run(0.55, [("owner", 8), ("room", 4)] * 60, 0, short_share=0.4)
    assert all(label == "Вы" for (_, label) in out)


# --- второй «режим» голоса владельца (ревью v037) -----------------------------------
# Голос владельца бывает не одним: сел, смех, другая интонация — окна с cos
# 0.5–0.65 к обычному голосу. Сильный дрейф ломает и прежнюю логику (такой режим
# становится «рядом»); правила двух голосов не должны делать хуже. База
# сравнения — те же правила выключенными (TWO_VOICES_MIN_S = ∞: _rooms пуст, и
# микрофон решается ровно как до них).
DRIFT_LEVELS = (0.5, 0.55, 0.6, 0.65, 0.8)
# Шум 4 прогонов по ~600 с речи: доля секунд гуляет на доли процента.
DRIFT_SLACK = 0.01


def _drift_share(plan, c0, cd, **kw):
    owner_as_room, room_as_you = [], []
    for seed in range(4):
        out, _ = spread_run(c0, plan, seed, drift=(cd, 0.3), **kw)
        owner = sum(x for (who, _), x in out.items() if who == "owner")
        room = sum(x for (who, _), x in out.items() if who == "room")
        owner_as_room.append(sum(x for (who, label), x in out.items() if who == "owner" and label != "Вы") / owner)
        room_as_you.append(out.get(("room", "Вы"), 0.0) / room if room else 0.0)
    return float(np.mean(owner_as_room)), float(np.mean(room_as_you))


def _drift_compare(monkeypatch, plan, **kw):
    rows = []
    for c0 in (0.70, 0.86):
        for cd in DRIFT_LEVELS:
            new = _drift_share(plan, c0, cd, **kw)
            with monkeypatch.context() as m:
                m.setattr(lv, "TWO_VOICES_MIN_S", float("inf"))
                base = _drift_share(plan, c0, cd, **kw)
            rows.append((c0, cd, new, base))
    return rows


def test_owner_drift_alone_is_no_worse_than_without_two_voice_rules(monkeypatch):
    for c0, cd, new, base in _drift_compare(monkeypatch, [("owner", 600)]):
        assert new[0] <= base[0] + DRIFT_SLACK, (c0, cd, new, base)
        if cd >= 0.65:
            assert new[0] <= base[0], (c0, cd, new, base)


def test_owner_drift_with_a_neighbour_is_better_than_without_two_voice_rules(monkeypatch):
    for c0, cd, new, base in _drift_compare(monkeypatch, [("owner", 8), ("room", 4)] * 40, short_share=0.4):
        assert new[0] <= base[0] + DRIFT_SLACK, (c0, cd, new, base)
        assert new[1] <= base[1], (c0, cd, new, base)


# --- голоса собеседников (дорожка sys, v037) ------------------------------------------
# Раньше на звонке все голоса — один «Собеседник»: без базы sys не кластеризовался,
# а безымянные кластеры подписывались одинаково. Теперь подтверждённые голоса
# собеседников нумеруются («Собеседник 1», «Собеседник 2»), пока голос один —
# «Собеседник», как раньше.


def test_two_unknown_voices_get_numbers_and_earlier_lines_follow():
    v = voices(base={})
    a, t = feed(v, "sys", 1, 8)  # Демьян 16 с: первый голос — от 15 с речи
    b, _ = feed(v, "sys", 2, 10, start=t + 1.0)  # Пётр: две проверки подряд (16 и 20 с)
    assert {x.speaker for x in a} == {"Собеседник"}  # голос один — как раньше
    assert [x.speaker for x in b] == ["Собеседник"] * 9 + ["Собеседник 2"]
    assert dict(v.drain()) == {a[0].voice: "Собеседник 1", b[0].voice: "Собеседник 2"}
    assert v.drain() == []


def _two_known(v):
    """Демьян 16 с, потом Пётр 20 с: оба голоса собеседников подтверждены."""
    a, t = feed(v, "sys", 1, 8)
    b, t = feed(v, "sys", 2, 10, start=t + 1.0)
    assert len(v._tracks["sys"].voices()) == 2
    v.drain()
    return a[0].voice, b[0].voice, t


def test_short_reply_between_two_known_voices_is_decided_by_voice():
    calls = []
    v = voices(base={}, calls=calls)
    ivan, petr, t = _two_known(v)
    feed(v, "sys", 1, 1, start=t)  # Демьян…
    n = len(calls)
    reply = v.assign("sys", clip(2, 1.2), t + 2.2, t + 3.4)  # …и «да, слушаю» Петра встык
    assert len(calls) == n + 1 and reply.voice == petr and reply.speaker == "Собеседник 2"
    tv = v._tracks["sys"]
    assert tv.resolve(tv.number(petr)).seconds == pytest.approx(20.0)  # короткая реплика центроид не растит


def test_short_reply_before_the_second_voice_is_relabelled_when_it_is_confirmed():
    """Короткая реплика Петра до того, как его голос подтвердился, берёт голос
    соседа (Демьяна), но под своим ключом: подтвердился Пётр — она его."""
    v = voices(base={})
    a, t = feed(v, "sys", 1, 8)
    reply = v.assign("sys", clip(2, 1.2), t, t + 1.2)
    assert reply.speaker == "Собеседник" and reply.voice != a[0].voice
    tv = v._tracks["sys"]
    assert tv.resolve(tv.number(reply.voice)).n == tv.number(a[0].voice)  # пока — голос Демьяна
    b, _ = feed(v, "sys", 2, 10, start=t + 3.0)
    renames = dict(v.drain())
    assert renames[reply.voice] == "Собеседник 2" and renames[a[0].voice] == "Собеседник 1"
    assert v.speaker(reply.voice) == v.speaker(b[0].voice)


def test_fragment_near_the_second_voice_is_labelled_by_it_but_can_become_a_third():
    """Обрывок (свой кластер 2 с) с Петром cos 0.45 — ниже LIVE_ASSIGN: после
    подтверждения Петра строка обрывка задним числом — «Собеседник 2», но в
    Петра он не вливается (центроид Петра чужим не мутнеет). Заговорил третий
    человек, на которого обрывок похож, — обрывок его."""
    VOICES[9] = lv._unit(0.45 * PETR + 0.893 * OWNER)
    try:
        v = voices(base={})
        _, t = feed(v, "sys", 1, 8)
        frag = v.assign("sys", clip(9), t + 1.0, t + 3.0)
        _, t = feed(v, "sys", 2, 10, start=t + 4.0)
        tv = v._tracks["sys"]
        assert tv.clusters[tv.number(frag.voice)].into is None
        assert (frag.voice, "Собеседник 2") in v.drain()
        feed(v, "sys", 3, 10, start=t + 1.0)  # третий голос
    finally:
        del VOICES[9]
    assert len(tv.voices()) == 3
    assert v.speaker(frag.voice) == "Собеседник 3"
    assert (frag.voice, "Собеседник 3") in v.drain()


def test_long_segment_only_partly_like_a_known_voice_starts_its_own_cluster():
    """При двух голосах длинный сегмент, похожий на Петра лишь на 0.45, — не в
    Петра (может быть третьим человеком), но подписан как Пётр."""
    VOICES[9] = lv._unit(0.45 * PETR + 0.893 * OWNER)
    try:
        v = voices(base={})
        ivan, petr, t = _two_known(v)
        got = v.assign("sys", clip(9, 2.5), t, t + 2.5)
    finally:
        del VOICES[9]
    tv = v._tracks["sys"]
    assert got.voice not in (ivan, petr) and got.speaker == "Собеседник 2"
    assert tv.resolve(tv.number(petr)).seconds == pytest.approx(20.0)


def test_voice_base_names_sys_voices_and_numbers_only_the_unnamed():
    """Голос из базы — по имени; один безымянный рядом с ним — просто
    «Собеседник»; от двух безымянных — номера (порядок подтверждения), и
    голос с именем номер не занимает (ревью M2: не «Собеседник 2/3»)."""
    v = voices(base={"Демьян": [IVAN * 2.5]})
    a, t = feed(v, "sys", 1, 8)
    b, t = feed(v, "sys", 2, 10, start=t + 1.0)
    assert a[-1].speaker == "Демьян" and b[-1].speaker == "Собеседник"
    c, _ = feed(v, "sys", 4, 10, start=t + 1.0)
    assert c[-1].speaker == "Собеседник 2"
    assert v.speaker(a[0].voice) == "Демьян" and v.speaker(b[0].voice) == "Собеседник 1"


def test_one_voice_never_shows_a_number():
    v = voices(base={})
    got, _ = feed(v, "sys", 1, 40)
    assert {x.speaker for x in got} == {"Собеседник"} and v.drain() == []
    assert len(v._tracks["sys"].voices()) == 1


def test_mic_track_keeps_its_own_rules():
    """Нумерация — только у собеседников: микрофон без образца — «Вы»."""
    v = voices(base={})
    feed(v, "sys", 1, 6)
    got, _ = feed(v, "mic", 2, 8)
    assert {x.speaker for x in got} == {"Вы"} and all(x.voice is None for x in got)


# --- смена говорящего внутри сегмента -------------------------------------------------


def two_voice_clip(first: int, second: int, a: float, b: float) -> np.ndarray:
    return np.concatenate([clip(first, a), clip(second, b)])


def test_segment_with_two_voices_is_cut_at_the_pause_where_the_voice_changes():
    calls = []
    v = voices(base={}, calls=calls)
    ivan, petr, t = _two_known(v)
    n = len(calls)
    # 2 с Демьяна, 2 с Петра; пауза 0,3 с между словами на стыке, 0,05 — внутри.
    cuts = [(t + 1.0, 0.05), (t + 2.0, 0.3), (t + 3.0, 0.05)]
    parts = v.assign_turns("sys", two_voice_clip(1, 2, 2.0, 2.0), t, t + 4.0, cuts)
    assert [(round(a - t, 3), round(b - t, 3), x.speaker) for a, b, x in parts] == [
        (0.0, 2.0, "Собеседник 1"), (2.0, 4.0, "Собеседник 2")]
    assert calls[n:] == [int(lv.SPLIT_WIN_S * SR)] * 2  # два окна у паузы, середина не считается
    assert v.stats["turns"] == 1


def test_one_voice_segment_is_not_cut_and_costs_no_extra_audio():
    calls = []
    v = voices(base={}, calls=calls)
    ivan, petr, t = _two_known(v)
    n = len(calls)
    parts = v.assign_turns("sys", clip(1, 4.0), t, t + 4.0, [(t + 2.0, 0.3)])
    assert [(x.voice, x.speaker) for _, _, x in parts] == [(ivan, "Собеседник 1")]
    # Два окна по 1,5 с вместо середины 3 с: звука на эмбеддинги — столько же.
    assert calls[n:] == [int(lv.SPLIT_WIN_S * SR)] * 2


def test_weak_difference_between_the_halves_does_not_cut():
    """Окна у паузы различаются, но без отрыва SPLIT_MARGIN — сегмент целиком."""
    VOICES[9] = lv._unit(0.60 * IVAN + 0.55 * PETR)  # с Демьяном 0.74, с Петром 0.68
    try:
        v = voices(base={})
        _, _, t = _two_known(v)
        parts = v.assign_turns("sys", two_voice_clip(1, 9, 2.0, 2.0), t, t + 4.0, [(t + 2.0, 0.3)])
    finally:
        del VOICES[9]
    assert len(parts) == 1 and v.stats["turns"] == 0


def test_no_cut_before_two_voices_are_known():
    calls = []
    v = voices(base={}, calls=calls)
    _, t = feed(v, "sys", 1, 6)
    n = len(calls)
    parts = v.assign_turns("sys", two_voice_clip(1, 2, 2.0, 2.0), t, t + 4.0, [(t + 2.0, 0.3)])
    assert len(parts) == 1 and calls[n:] == [int(lv.EMBED_MAX_S * SR)]  # обычный путь: середина


# --- синтетика с разбросом окон (собеседники) ------------------------------------------
# Окно голоса: cos с его голосом ≈ 0.7, окна одного человека между собой ≈ 0.5
# (v037, mic-live-diar: окно с окном ~0.5); короткое (1–1,5 с) — шумнее. Голоса
# разных людей — cos 0.35 (до 0.45). Эмбеддер читает голос из звука клипа.

SYS_D = 32


def _toward_d(rng, o, c):
    u = rng.standard_normal(SYS_D)
    u = _u(u - (u @ o) * o)
    return _u(c * o + np.sqrt(1 - c ** 2) * u)


def sys_run(plan, seed=0, n=2, cross=0.35, short_share=0.0, drift=None, base=None):
    """`plan` — [(кто, секунды[, доля коротких])], кто — 0…n-1. `drift` — (cd,
    p): у голоса 0 второй «режим» (cos cd с обычным) на доле p окон. `base` —
    {имя: кто}. → ([(кто, подпись сейчас, подпись при выдаче, секунды)], v)."""
    rng = np.random.default_rng(seed)
    voice = [_u(rng.standard_normal(SYS_D))]
    while len(voice) < n:
        voice.append(_toward_d(rng, voice[0], cross))
    if drift:
        voice.append(_toward_d(rng, voice[0], drift[0]))  # код n — второй режим голоса 0

    def embed(audio):
        codes = np.rint(audio * 100).astype(int) - 1
        codes = codes[codes >= 0]
        share = np.bincount(codes, minlength=len(voice)) / max(1, len(codes))
        mix = _u(sum(w * vec for w, vec in zip(share, voice)))
        sig = 1.6 if len(audio) < 1.5 * SR else 1.0
        return _u(mix + sig * _u(rng.standard_normal(SYS_D)))

    names = {name: [voice[who] * 1.7] for name, who in (base or {}).items()}
    v = lv.LiveVoices(embed, names, [], name_threshold=0.70, session="t", log=lambda line: None)
    t, lines = 0.0, []
    for step in plan:
        who, total = step[0], step[1]
        share = step[2] if len(step) > 2 else short_share
        said = 0.0
        while said < total:
            short = rng.random() < share
            d = float(rng.uniform(1.0, 1.5)) if short else float(rng.uniform(1.5, 4.0))
            code = n if drift and who == 0 and rng.random() < drift[1] else who
            got = v.assign("sys", np.full(int(d * SR), (code + 1) / 100, dtype=np.float32), t, t + d)
            lines.append((who, got.voice, got.speaker, d))
            v.drain()
            t += d + 0.4
            said += d
    return [(who, v.speaker(key) if key else "Собеседник", shown, d) for who, key, shown, d in lines], v


def label_accuracy(rows, final=True):
    """Доля секунд с верной подписью при лучшем соответствии «подпись ↔
    человек» один к одному (как DER без пауз и нахлёста)."""
    import itertools

    col = 1 if final else 2
    whos = sorted({r[0] for r in rows})
    labels = sorted({r[col] for r in rows})
    m = {}
    for r in rows:
        m[(r[0], r[col])] = m.get((r[0], r[col]), 0.0) + r[3]
    pad = labels + [None] * max(0, len(whos) - len(labels))
    best = max(sum(m.get((w, lab), 0.0) for w, lab in zip(whos, labs))
               for labs in itertools.permutations(pad, len(whos)))
    return best / sum(r[3] for r in rows)


def test_two_remote_voices_in_fast_turns_are_told_apart():
    for seed in range(4):
        rows, _ = sys_run([(0, 6), (1, 4)] * 40, seed, short_share=0.4)
        assert label_accuracy(rows) >= 0.95, seed
        assert label_accuracy(rows, final=False) >= 0.75, seed  # до подтверждения — «Собеседник»


def test_two_close_remote_voices_are_still_told_apart():
    for seed in range(3):
        rows, _ = sys_run([(0, 6), (1, 4)] * 40, seed, short_share=0.4, cross=0.45)
        assert label_accuracy(rows) >= 0.92, seed


def test_one_remote_voice_with_drift_is_never_split():
    """Звонок один на один: другая интонация, шум линии — не второй человек.
    Ни одной строки с номером ни при выдаче, ни задним числом. Второй «режим»
    голоса — на 30 % окон, cos 0.75–0.8 с обычным (T0: один человек — p10
    0.70 на 15 с; при 0.7 и ниже он неотличим от второго похожего человека —
    см. отчёт v037 sys-live-diar)."""
    for cd in (0.75, 0.8, 1.0):
        for seed in range(4):
            rows, v = sys_run([(0, 900)], seed, n=1, drift=(cd, 0.3), short_share=0.3)
            assert {r[1] for r in rows} == {r[2] for r in rows} == {"Собеседник"}, (cd, seed)


def test_three_remote_voices_get_three_labels():
    for seed in range(4):
        rows, v = sys_run([(0, 6), (1, 5), (2, 5), (1, 3)] * 25, seed, n=3, short_share=0.3)
        assert len(v._tracks["sys"].voices()) == 3, seed
        assert label_accuracy(rows) >= 0.92, seed


def test_short_replies_of_the_second_voice_are_labelled_by_voice():
    """Второй голос отвечает в основном коротко (1–1,5 с): его секунды —
    его подпись, а не голос предыдущего говорящего."""
    for seed in range(4):
        rows, _ = sys_run([(0, 8, 0.2), (1, 1.2, 1.0), (0, 5, 0.2), (1, 3, 0.0)] * 30, seed)
        assert label_accuracy(rows) >= 0.95, seed
        theirs = {}
        for r in rows:
            if r[0] == 1:
                theirs[r[1]] = theirs.get(r[1], 0.0) + r[3]
        mine = max(theirs, key=theirs.get)
        short = [r for r in rows if r[0] == 1 and r[3] < 1.5]
        right = sum(r[3] for r in short if r[1] == mine) / sum(r[3] for r in short)
        assert right >= 0.8, (seed, right)


def test_spread_voice_from_the_base_is_named():
    for seed in range(3):
        rows, _ = sys_run([(0, 6), (1, 4)] * 40, seed, short_share=0.3, base={"Демьян": 0})
        ivan = sum(r[3] for r in rows if r[0] == 0 and r[1] == "Демьян")
        assert ivan >= 0.95 * sum(r[3] for r in rows if r[0] == 0), seed
        petr_as_ivan = sum(r[3] for r in rows if r[0] == 1 and r[1] == "Демьян")
        assert petr_as_ivan <= 0.05 * sum(r[3] for r in rows if r[0] == 1), seed


# --- fix round 1 (ревью v037 sys-live-diar) -------------------------------------------


def _split_voice(v):
    """Ложное разделение: голос 1 (Демьян) и голос 2 — «режим» Демьяна, чей
    центроид от отбора окон ушёл от него (cos 0.45 < SYS_DISTINCT)."""
    VOICES[9] = lv._unit(0.45 * IVAN + 0.893 * OWNER)
    try:
        a, t = feed(v, "sys", 1, 8)
        b, t = feed(v, "sys", 9, 10, start=t + 1.0)
    finally:
        del VOICES[9]
    tv = v._tracks["sys"]
    assert len(tv.voices()) == 2
    return a, b, t


def test_split_voice_merges_back_and_lines_revert_through_drain():
    v = voices(base={})
    a, b, t = _split_voice(v)
    assert b[-1].speaker == "Собеседник 2"
    v.drain()
    tv = v._tracks["sys"]
    one, two = tv.voices()
    two.total = lv._unit(0.8 * IVAN + 0.6 * OWNER) * two.seconds  # центроиды сошлись: cos 0.8
    feed(v, "sys", 1, 1, start=t + 1.0)
    assert len(tv.voices()) == 1
    renames = dict(v.drain())
    assert renames[a[0].voice] == "Собеседник" and renames[b[0].voice] == "Собеседник"
    notes = v.drain_notes()
    assert notes and "одним человеком" in notes[-1]


def test_split_voice_heals_on_segment_evidence_before_centroids_meet():
    """I1: голоса, чьи сегменты почти так же похожи на другой голос, как на
    свой (отношение от SYS_SAME_RATIO), сливаются, даже когда их центроиды
    разошлись от отбора окон (cos 0.62 < SYS_MERGE)."""
    v = voices(base={})
    a, b, t = _split_voice(v)
    tv = v._tracks["sys"]
    assert float(tv.voices()[0].center @ tv.voices()[1].center) < lv.SYS_MERGE
    VOICES[9] = lv._unit(0.62 * IVAN + 0.785 * OWNER)
    try:
        feed(v, "sys", 9, lv.SYS_EVIDENCE_N + 1, start=t + 1.0)
    finally:
        del VOICES[9]
    assert len(tv.voices()) == 1
    assert v.speaker(b[0].voice) == "Собеседник"


def test_two_real_voices_are_not_merged_by_the_evidence_rule():
    v = voices(base={})
    _, _, t = _two_known(v)
    feed(v, "sys", 2, 20, start=t)
    feed(v, "sys", 1, 20, start=t + 50.0)
    assert len(v._tracks["sys"].voices()) == 2


def test_numbers_are_reused_after_a_false_split_heals():
    """M2: после слияния ошибочно разделённого голоса настоящий второй
    человек — «Собеседник 2», а не «Собеседник 3»."""
    v = voices(base={})
    a, b, t = _split_voice(v)
    tv = v._tracks["sys"]
    tv.voices()[1].total = lv._unit(0.8 * IVAN + 0.6 * OWNER) * tv.voices()[1].seconds
    _, t = feed(v, "sys", 1, 1, start=t + 1.0)
    assert len(tv.voices()) == 1
    c, _ = feed(v, "sys", 2, 10, start=t + 1.0)
    assert c[-1].speaker == "Собеседник 2"
    assert v.speaker(a[0].voice) == "Собеседник 1"


def test_short_reply_without_a_neighbour_takes_the_nearest_numbered_voice():
    """M3: короткая реплика после долгой паузы (соседа нет) при двух голосах —
    не «Собеседник» без номера и без ключа, а ближайший голос под своим ключом."""
    VOICES[9] = lv._unit(0.50 * IVAN + 0.52 * PETR + 0.69 * OWNER)  # без отрыва: голосами не решить
    try:
        v = voices(base={})
        ivan, petr, t = _two_known(v)
        got = v.assign("sys", clip(9, 1.2), t + 30.0, t + 31.2)
    finally:
        del VOICES[9]
    assert got.voice not in (None, ivan, petr) and got.speaker == "Собеседник 2"
    tv = v._tracks["sys"]
    assert tv.get(tv.number(got.voice)).shadow


def test_evicted_shades_drop_their_embedding_but_keep_their_voice():
    v = voices(base={})
    a, t = feed(v, "sys", 1, 8)
    keys = []
    for i in range(lv.SHADOWS_KEPT + 5):
        keys.append(v.assign("sys", clip(1, 1.2), t, t + 1.2).voice)
        t += 1.4
    tv = v._tracks["sys"]
    first = tv.get(tv.number(keys[0]))
    assert first.total.size == 0 and tv.resolve(first.n).n == tv.number(a[0].voice)
    assert tv.get(tv.number(keys[-1])).total.size > 0
    assert v.speaker(keys[0]) == "Собеседник"


def test_drain_notes_tell_the_agent_when_labels_change():
    """I2: агенту — короткая заметка, когда нумерация включилась, выключилась
    или голос получил имя из базы."""
    v = voices(base={})
    feed(v, "sys", 1, 8)
    v.drain()
    assert v.drain_notes() == []  # голос один — нумерации нет
    _, t = feed(v, "sys", 2, 10, start=40.0)
    v.drain()
    notes = v.drain_notes()
    assert len(notes) == 1 and notes[0].startswith("Meet уточнил говорящих:")
    assert "Собеседник 1, Собеседник 2" in notes[0]
    assert v.drain_notes() == []
    named = voices(base={"Демьян": [IVAN * 2.5]})
    feed(named, "sys", 1, 6)
    named.drain()
    assert named.drain_notes() == ["Meet уточнил говорящих: «Собеседник» — это Демьян."]


@pytest.mark.parametrize("cd", [0.62, 0.65, 0.68, 0.70])
def test_one_drifting_voice_for_an_hour_heals_and_is_numbered_briefly(cd):
    """I1: один голос с сильным вторым «режимом» (cos 0.62–0.70 на 30 % окон)
    1 ч. Ни один прогон не остаётся с номерами; строк с номером при выдаче —
    мало (до правок раунда 1 на тех же прогонах: 956/320/227/143)."""
    numbered = 0
    for seed in (0, 4, 5):
        rows, _ = sys_run([(0, 3600)], seed, n=1, drift=(cd, 0.3), short_share=0.3)
        assert {r[1] for r in rows} == {"Собеседник"}, (cd, seed)
        numbered += sum(1 for r in rows if r[2] != "Собеседник")
    assert numbered <= {0.62: 420, 0.65: 110, 0.68: 110, 0.70: 70}[cd], numbered
