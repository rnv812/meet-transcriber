"""Дубли соседа и эхо колонок (meet.mic_dedupe): одна реплика, попавшая и в
микрофон, и в звук собеседников, остаётся один раз — а слова владельца не
теряются никогда.

Слова и огибающие громкости синтетические, фразы выдуманы."""

import numpy as np
import pytest

from meet import mic_dedupe
from meet.mic_dedupe import Envelope, Lags, Tok

F = mic_dedupe.FRAME_S
TOTAL = 120.0


def _words(text, start, *, seg, role="owner", step=0.4):
    out = []
    for i, w in enumerate(text.split()):
        t = start + i * step
        out.append(Tok(key=(seg, i), seg=seg, start=round(t, 3), end=round(t + step * 0.8, 3),
                       text=" " + w, role=role))
    return out


def _db(events, floor=-70.0):
    """Кадры громкости: на каждом событии (начало, длительность, зерно,
    уровень) — «слоги» случайной огибающей. Одно зерно — та же речь."""
    out = np.full(int(TOTAL / F), floor, dtype=np.float32)
    for start, dur, seed, level in events:
        rng = np.random.default_rng(seed)
        a, k = int(round(start / F)), int(round(dur / F))
        out[a:a + k] = level + rng.uniform(-10.0, 0.0, k)
    return out


def _env(mic_events, sys_events, speech=None):
    return Envelope(_db(mic_events), _db(sys_events), speech=speech)


NEIGHBOUR = "давайте перенесём встречу на завтра"


def _reasons(drops):
    return [(d.track, d.reason, len(d.words)) for d in drops]


def test_normalize_lower_yo_punctuation():
    assert mic_dedupe.normalize(" Ёлки, Палки!") == "елки палки"
    assert mic_dedupe.normalize("«Ну — да…»") == "ну да"
    assert mic_dedupe.normalize("...") == ""


def test_neighbour_copy_in_room_window_is_dropped_from_mic():
    """Сосед говорит в свой ноутбук: в моём микрофоне он раньше, чем в звуке
    собеседников (лаг > 0)."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="room")
    sys = _words(NEIGHBOUR, 10.4, seg="s1")
    drops, lags = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    d = drops[0]
    assert d.lag == pytest.approx(0.4, abs=0.01) and d.coverage == pytest.approx(1.0)
    assert [w.key for w in d.words] == [w.key for w in mic]
    assert lags.reference("neighbour") == pytest.approx(0.4, abs=0.01)
    raw = d.to_raw()
    assert set(raw) == {"track", "start", "end", "text", "words", "pair", "coverage", "lag", "env_corr", "reason"}
    assert raw["text"] == "давайте перенесём встречу на завтра" and raw["words"] == 5
    assert raw["pair"]["track"] == "sys" and raw["pair"]["start"] == pytest.approx(10.4)


def test_speaker_echo_in_unsure_window_is_dropped_from_mic():
    """Колонки: голос собеседника из sys попадает в микрофон чуть позже."""
    sys = _words("по бюджету вопросов больше нет", 20.0, seg="s1")
    mic = _words("по бюджету вопросов больше нет", 20.03, seg="m1", role="unsure")
    drops, lags = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("mic", "echo", 5)]
    assert drops[0].lag == pytest.approx(-0.03, abs=0.01)
    assert lags.reference("echo") == pytest.approx(-0.03, abs=0.01)


def test_owner_leak_is_dropped_from_sys_never_from_mic():
    """Два ноутбука рядом: мой голос через ноутбук соседа приходит в мой sys."""
    mic = _words("я пришлю отчёт до пятницы", 30.0, seg="m1", role="owner")
    sys = _words("я пришлю отчёт до пятницы", 30.5, seg="s1")
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("sys", "owner_leak", 5)]
    assert [w.key for w in drops[0].words] == [w.key for w in sys]
    assert drops[0].to_raw()["pair"]["track"] == "mic"


def test_owner_window_with_echo_lag_keeps_both():
    """Окно владельца, а лаг как у эха: кто чья копия — неясно, ничего не трогаем."""
    mic = _words("я пришлю отчёт до пятницы", 30.0, seg="m1", role="owner")
    sys = _words("я пришлю отчёт до пятницы", 29.98, seg="s1")
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True)
    assert drops == []


def test_asr_variants_still_match():
    """Копия в микрофоне тише — распознана с ошибками; ё, регистр, знаки не мешают."""
    mic = _words("давайте перенесем встречу на завтро", 10.0, seg="m1", role="room")
    sys = _words("Давайте перенесём встречу на завтра.", 10.4, seg="s1")
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert 0.6 <= drops[0].coverage < 1.0


def test_partial_duplicate_inside_long_owner_phrase_drops_only_room_words():
    """Сосед вклинился в мою длинную фразу: убираются только его слова."""
    owner_a = _words("я думаю что нам", 40.0, seg="m1", role="owner")
    room = [Tok(key=("m1", 4 + i), seg="m1", start=w.start, end=w.end, text=w.text, role="room")
            for i, w in enumerate(_words(NEIGHBOUR, 41.6, seg="m1"))]
    owner_b = [Tok(key=("m1", 9 + i), seg="m1", start=w.start, end=w.end, text=w.text, role="owner")
               for i, w in enumerate(_words("и всё", 43.6, seg="m1"))]
    mic = owner_a + room + owner_b
    sys = _words(NEIGHBOUR, 42.0, seg="s1")
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert [w.key for w in drops[0].words] == [w.key for w in room]


def test_mic_heard_only_part_of_long_sys_phrase():
    sys = _words("коллеги давайте перенесём встречу на завтра потому что сервер лежит", 50.0, seg="s1")
    mic = _words("перенесём встречу на завтра", 50.4, seg="m1", role="room")
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True)
    assert _reasons(drops) == [("mic", "neighbour", 4)]
    assert drops[0].lag == pytest.approx(0.4, abs=0.01)


def test_lag_inconsistent_with_global_lag_is_kept():
    """Уверенные пары задают лаг соседа ~0,4 с; совпадение с лагом 1,8 с —
    не копия (через 1,8 с собеседник повторил те же слова)."""
    mic = (_words(NEIGHBOUR, 10.0, seg="m1", role="room")
           + _words("отчёт по складу готов полностью", 30.0, seg="m2", role="room")
           + _words("сервер опять лежит", 60.0, seg="m3", role="room"))
    sys = (_words(NEIGHBOUR, 10.4, seg="s1")
           + _words("отчёт по складу готов полностью", 30.4, seg="s2")
           + _words("сервер опять лежит", 61.8, seg="s3"))
    drops, lags = mic_dedupe.find(mic, sys, owner_known=True)
    assert [d.words[0].seg for d in drops] == ["m1", "m2"]
    assert lags.reference("neighbour") == pytest.approx(0.4, abs=0.01)


def test_without_confident_pairs_lag_window_is_wide_but_bounded():
    phrase = "сервер опять лежит с утра"
    mic = _words(phrase, 60.0, seg="m1", role="room")
    assert len(mic_dedupe.find(mic, _words(phrase, 61.5, seg="s1"), owner_known=True)[0]) == 1
    # Кандидаты sys — до 2,5 с после реплики, но лаг больше 2 с — уже не копия.
    assert mic_dedupe.find(mic, _words(phrase, 62.3, seg="s1"), owner_known=True)[0] == []
    # Совсем далеко — и не кандидат.
    assert mic_dedupe.find(mic, _words(phrase, 70.0, seg="s1"), owner_known=True)[0] == []


def test_three_words_need_established_lag():
    """Три совпавших слова без уверенных пар (лаг ещё не известен) — не дубль:
    «всем спасибо пока» в конце встречи говорят все."""
    mic = _words("всем спасибо пока", 10.0, seg="m1", role="room")
    sys = _words("всем спасибо пока", 10.8, seg="s1")
    assert mic_dedupe.find(mic, sys, owner_known=True)[0] == []
    lead_mic = _words(NEIGHBOUR, 2.0, seg="m0", role="room")
    lead_sys = _words(NEIGHBOUR, 2.8, seg="s0")
    drops, _ = mic_dedupe.find(lead_mic + mic, lead_sys + sys, owner_known=True)
    assert [d.words[0].seg for d in drops] == ["m0", "m1"]


def _short_case(*, sys_shift, env_same=True, with_env=True):
    """Короткое «ага» соседа (отдельный сегмент) + длинная уверенная пара,
    задающая лаг соседа 0,4 с."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="room") + _words("Ага.", 70.0, seg="m2", role="room")
    sys = _words(NEIGHBOUR, 10.4, seg="s1") + _words("ага", 70.0 + sys_shift, seg="s2")
    env = None
    if with_env:
        # Та же речь — та же огибающая со сдвигом; другая — «ага» посреди
        # чужой сплошной речи.
        other = (69.0, 3.0, 9, -12.0)
        env = _env([(10.0, 2.0, 1, -30.0), (70.0, 0.32, 7, -30.0)],
                   [(10.4, 2.0, 1, -12.0), (70.0 + sys_shift, 0.32, 7, -12.0) if env_same else other])
    return mic_dedupe.find(mic, sys, owner_known=True, env=env)[0]


def test_short_aga_with_exact_text_lag_and_envelope_is_dropped():
    drops = _short_case(sys_shift=0.45)
    assert [(d.words[0].seg, d.reason) for d in drops] == [("m1", "neighbour"), ("m2", "neighbour")]
    assert drops[1].env_corr >= mic_dedupe.MIN_ENV_CORR


def test_short_aga_needs_envelope():
    assert [d.words[0].seg for d in _short_case(sys_shift=0.45, with_env=False)] == ["m1"]
    assert [d.words[0].seg for d in _short_case(sys_shift=0.45, env_same=False)] == ["m1"]


def test_short_aga_needs_tight_lag():
    assert [d.words[0].seg for d in _short_case(sys_shift=0.9)] == ["m1"]


def test_short_aga_without_global_lag_is_kept():
    mic = _words("Ага.", 70.0, seg="m2", role="room")
    sys = _words("ага", 70.4, seg="s2")
    env = _env([(70.0, 0.32, 7, -30.0)], [(70.4, 0.32, 7, -12.0)])
    assert mic_dedupe.find(mic, sys, owner_known=True, env=env)[0] == []


def test_single_word_inside_long_segment_is_never_dropped():
    """Одно совпавшее слово посреди длинной реплики — не прогон, не трогаем."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="room") + _words("ну вот ага и всё тут", 70.0, seg="m2", role="room")
    sys = _words(NEIGHBOUR, 10.4, seg="s1") + _words("ага", 70.8 + 0.4, seg="s2")
    env = _env([(10.0, 2.0, 1, -30.0), (70.8, 0.32, 7, -30.0)], [(10.4, 2.0, 1, -12.0), (71.2, 0.32, 7, -12.0)])
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True, env=env)
    assert [d.words[0].seg for d in drops] == ["m1"]


def test_common_words_of_two_people_are_not_duplicates():
    """Владелец и собеседник независимо говорят одно и то же короткое:
    «да да конечно» / «да конечно», «ну да» / «ну да»."""
    mic = _words("да да конечно", 10.0, seg="m1", role="owner") + _words("ну да", 30.0, seg="m2", role="owner")
    sys = _words("да конечно", 11.0, seg="s1") + _words("ну да", 30.8, seg="s2")
    env = _env([(10.0, 1.2, 1, -10.0), (30.0, 0.8, 2, -10.0)], [(11.0, 0.8, 3, -12.0), (30.8, 0.8, 4, -12.0)])
    assert mic_dedupe.find(mic, sys, owner_known=True, env=env)[0] == []
    assert mic_dedupe.find(mic, sys, owner_known=True)[0] == []
    room = [Tok(w.key, w.seg, w.start, w.end, w.text, "room") for w in mic]
    assert mic_dedupe.find(room, sys, owner_known=True, env=env)[0] == []


def test_short_common_words_at_neighbour_lag_need_same_envelope():
    """Лаг соседа известен (0,4 с), и собеседник сказал «ну да» ровно через
    0,4 с после владельца — но посреди своей сплошной речи: не копия."""
    mic = _words(NEIGHBOUR, 2.0, seg="m0", role="room") + _words("ну да", 30.0, seg="m2", role="owner")
    sys = _words(NEIGHBOUR, 2.4, seg="s0") + _words("ну да", 30.4, seg="s2")
    env = _env([(2.0, 2.0, 1, -30.0), (30.0, 0.72, 2, -10.0)], [(2.4, 2.0, 1, -12.0), (28.0, 5.0, 4, -12.0)])
    drops, _ = mic_dedupe.find(mic, sys, owner_known=True, env=env)
    assert [d.words[0].seg for d in drops] == ["m0"]


def test_no_owner_sample_drops_only_quiet_mic_copy():
    """Без образца владельца: копию в микрофоне убираем, только если она
    заметно тише речи микрофона; копию в sys не трогаем никогда."""
    owner = _words("я пришлю отчёт до пятницы", 5.0, seg="m0")
    copy = _words(NEIGHBOUR, 10.0, seg="m1")
    sys = _words(NEIGHBOUR, 10.4, seg="s1")
    speech = [(w.start, w.end) for w in owner + copy]
    quiet = _env([(5.0, 2.0, 5, -10.0), (10.0, 2.0, 1, -35.0)], [(10.4, 2.0, 1, -12.0)], speech)
    loud = _env([(5.0, 2.0, 5, -10.0), (10.0, 2.0, 1, -10.0)], [(10.4, 2.0, 1, -12.0)], speech)
    drops, _ = mic_dedupe.find(owner + copy, sys, owner_known=False, env=quiet)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert mic_dedupe.find(owner + copy, sys, owner_known=False, env=loud)[0] == []
    assert mic_dedupe.find(owner + copy, sys, owner_known=False)[0] == []
    # Владелец (по словам и звуку — громкий) с утечкой в sys: без образца — не трогаем.
    leak = _words("я пришлю отчёт до пятницы", 5.5, seg="s2")
    assert mic_dedupe.find(owner, leak, owner_known=False, env=loud)[0] == []


def test_lags_reference_and_consistency():
    lags = Lags()
    assert lags.reference("neighbour") is None
    # Лаг не известен: длинным — широкое окно, коротким (до трёх слов) — нельзя.
    assert lags.consistent(1.9, 4) and lags.consistent(-0.29, 4) and not lags.consistent(2.1, 4)
    assert not lags.consistent(0.4, 3) and not lags.consistent(0.4, 1)
    for lag in (0.35, 0.4, 0.45):
        lags.add(lag)
    lags.add(-0.02)
    lags.add(2.5)  # вне правдоподобного окна — в лаг не идёт
    assert lags.reference("neighbour") == pytest.approx(0.4)
    assert lags.reference("echo") == pytest.approx(-0.02)
    assert lags.consistent(0.85, 3) and not lags.consistent(0.95, 4)
    assert lags.consistent(0.6, 2) is True and lags.consistent(0.75, 1) is False
    assert lags.consistent(0.03, 1) and lags.consistent(0.4, 4)
    raw = lags.to_raw()
    assert raw == {"neighbour": 0.4, "echo": -0.02, "pairs": {"neighbour": 3, "echo": 1}}


def test_live_match_one_line_learns_lag():
    """Живой режим: реплика микрофона против кольца недавних слов sys; уверенные
    пары копят лаг для коротких реплик."""
    lags = Lags()
    sys = _words(NEIGHBOUR, 10.4, seg="s1") + _words("ага", 20.45, seg="s2")
    first = mic_dedupe.match(_words(NEIGHBOUR, 10.0, seg="m1", role="room"), sys, lags=lags)
    assert _reasons(first) == [("mic", "neighbour", 5)]
    assert lags.reference("neighbour") == pytest.approx(0.4, abs=0.01)
    env = _env([(20.0, 0.32, 7, -30.0)], [(20.45, 0.32, 7, -12.0)])
    second = mic_dedupe.match(_words("ага", 20.0, seg="m2", role="room"), sys, lags=lags, env=env)
    assert _reasons(second) == [("mic", "neighbour", 1)]
    # Строка владельца — копию из sys убираем, свою — никогда.
    mine = mic_dedupe.match(_words("я пришлю отчёт до пятницы", 30.0, seg="m3"),
                            _words("я пришлю отчёт до пятницы", 30.4, seg="s3"), lags=lags)
    assert _reasons(mine) == [("sys", "owner_leak", 5)]


def test_empty_inputs():
    assert mic_dedupe.find([], [], owner_known=True)[0] == []
    assert mic_dedupe.find(_words("ага", 1.0, seg="m"), [], owner_known=True)[0] == []
    assert mic_dedupe.find([], _words("ага", 1.0, seg="s"), owner_known=True)[0] == []
