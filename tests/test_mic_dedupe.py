"""Дубли соседа и эхо колонок (meet.mic_dedupe): одна реплика, попавшая и в
микрофон, и в звук собеседников, остаётся один раз — а слова владельца без
настоящих улик (установленный лаг по независимым парам + та же огибающая)
не теряются никогда; повтор другим человеком — не копия.

Слова и огибающие громкости синтетические, фразы выдуманы."""

import numpy as np
import pytest

from meet import mic_dedupe
from meet.mic_dedupe import Envelope, Lags, Tok

F = mic_dedupe.FRAME_S
TOTAL = 160.0
LAG = 0.2  # лаг соседа в этих тестах
NEIGHBOUR = "давайте перенесём встречу на завтра"
# Независимые фразы соседа: по ним устанавливается L*.
LEAD_PHRASES = ["отчёт по складу готов полностью сегодня", "поставщик подтвердил новые цены вчера",
                "машину закажем заранее на утро", "бухгалтерия согласует счёт к пятнице"]


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
    out = np.full(int(TOTAL / F), floor, dtype=np.float64)
    for start, dur, seed, level in events:
        rng = np.random.default_rng(seed)
        a, k = int(round(start / F)), int(round(dur / F))
        out[a:a + k] = level + rng.uniform(-10.0, 0.0, k)
    return out


def _env(mic_events, sys_events, speech=None):
    return Envelope.from_db(_db(mic_events), _db(sys_events), speech, F)


def _lead(n=3, lag=LAG, role="room", t0=120.0, level=-30.0):
    """n независимых уверенных пар соседа (сегменты L0…): mic, sys и события
    огибающей (та же речь в обеих дорожках)."""
    mic, sys, me, se = [], [], [], []
    for i in range(n):
        start = t0 + i * 8.0
        text = LEAD_PHRASES[i]
        mic += _words(text, start, seg=f"L{i}", role=role)
        sys += _words(text, start + lag, seg=f"S{i}")
        dur = len(text.split()) * 0.4
        me.append((start, dur, 900 + i, level))
        se.append((start + lag, dur, 900 + i, -12.0))
    return mic, sys, me, se


def _owner_voice(spans):
    """Центроид голоса копий в sys — владелец (по образцу): в этих тестах — да."""
    return True, 0.9


def _find(mic, sys, *, owner_known=True, lead=3, lag=LAG, lead_role="room", mic_ev=None, sys_ev=None,
          speech=None, leak_voice=_owner_voice):
    """find() с n независимыми парами соседа; → удаления без пар-подпорок."""
    lm, ls, le_m, le_s = _lead(lead, lag, lead_role) if lead else ([], [], [], [])
    env = None
    if mic_ev is not None:
        env = _env(list(mic_ev) + le_m, list(sys_ev) + le_s, speech)
    drops, lags = mic_dedupe.find(mic + lm, sys + ls, owner_known=owner_known, env=env, leak_voice=leak_voice)
    return [d for d in drops if not str(d.words[0].seg).startswith(("L", "S"))], lags


def _reasons(drops):
    return [(d.track, d.reason, len(d.words)) for d in drops]


def test_normalize_lower_yo_punctuation():
    assert mic_dedupe.normalize(" Ёлки, Палки!") == "елки палки"
    assert mic_dedupe.normalize("«Ну — да…»") == "ну да"
    assert mic_dedupe.normalize("...") == ""


# --- L*: только независимые пары ---------------------------------------------------


def test_neighbour_copy_in_room_window_is_dropped_when_lag_is_established():
    """Сосед говорит в свой ноутбук: в моём микрофоне он раньше, чем в звуке
    собеседников; ещё три его фразы дают тот же лаг."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="room")
    sys = _words(NEIGHBOUR, 10.0 + LAG, seg="s1")
    drops, lags = _find(mic, sys)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    d = drops[0]
    assert d.lag == pytest.approx(LAG, abs=0.01) and d.coverage == pytest.approx(1.0)
    assert [w.key for w in d.words] == [w.key for w in mic]
    assert lags.reference("neighbour") == pytest.approx(LAG, abs=0.01)
    raw = d.to_raw()
    assert set(raw) == {"track", "start", "end", "text", "words", "pair", "coverage", "lag", "env_corr", "reason"}
    assert raw["text"] == "давайте перенесём встречу на завтра" and raw["words"] == 5
    assert raw["pair"]["track"] == "sys" and raw["pair"]["start"] == pytest.approx(10.0 + LAG)


def test_single_confident_pair_does_not_validate_itself():
    """Одна пара — сама себе не L*: без независимых пар ничего не удаляется."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="room")
    sys = _words(NEIGHBOUR, 10.0 + LAG, seg="s1")
    assert _find(mic, sys, lead=0)[0] == []
    # Две подпорки — тоже мало (нужно три независимые).
    assert _find(mic, sys, lead=2)[0] == []
    # Четыре пары — каждая подтверждена тремя другими.
    lm, ls, _, _ = _lead(3)
    drops, _ = mic_dedupe.find(mic + lm, sys + ls, owner_known=True)
    assert len(drops) == 4


def test_lone_closing_phrase_of_four_words_is_kept():
    """«всем спасибо всем пока» в конце встречи — и в микрофоне, и в sys через
    секунду; других пар нет — не дубль."""
    mic = _words("всем спасибо всем пока", 10.0, seg="m1", role="room")
    sys = _words("всем спасибо всем пока", 11.0, seg="s1")
    assert _find(mic, sys, lead=0)[0] == []


def test_outlier_pair_does_not_establish_lag_for_another():
    """Одна случайная длинная пара (лаг 1,7) и три слова с лагом 1,5 — без
    независимых пар лаг не установлен: ничего не удаляется."""
    mic = (_words("ну это мы уже обсуждали вчера", 10.0, seg="m1", role="room")
           + _words("сервер опять лежит", 30.0, seg="m2", role="room"))
    sys = _words("ну это мы уже обсуждали вчера", 11.7, seg="s1") + _words("сервер опять лежит", 31.5, seg="s2")
    assert _find(mic, sys, lead=0)[0] == []


def test_scattered_lags_do_not_establish_reference():
    lags = Lags()
    for lag in (0.15, 0.9, 1.6, 0.5):
        lags.add(lag)
    assert lags.reference("neighbour") is None


def test_lag_inconsistent_with_established_lag_is_kept():
    """L* соседа ~0,2 с; совпадение с лагом 0,9 с — не копия (собеседник
    повторил те же слова)."""
    mic = _words("сервер опять лежит с утра", 60.0, seg="m3", role="room")
    sys = _words("сервер опять лежит с утра", 60.9, seg="s3")
    assert _find(mic, sys)[0] == []


def test_three_words_with_established_lag_are_dropped():
    mic = _words("сервер опять лежит", 60.0, seg="m3", role="room")
    sys = _words("сервер опять лежит", 60.0 + LAG, seg="s3")
    assert _reasons(_find(mic, sys)[0]) == [("mic", "neighbour", 3)]


def test_lags_reference_and_consistency():
    lags = Lags()
    assert lags.reference("neighbour") is None
    assert not lags.consistent(0.2, 4) and not lags.consistent(0.2, 1)
    for lag in (0.18, 0.2, 0.22):
        lags.add(lag)
    assert lags.add(-0.02) and not lags.add(2.5)  # вне правдоподобного — в L* не идёт
    assert lags.reference("neighbour") == pytest.approx(0.2)
    assert lags.reference("echo") is None  # одна пара эха — не L*
    # Без собственного лага (проверяемая пара) остаются две — L* нет.
    assert lags.reference("neighbour", exclude=0.2) is None
    lags.add(0.21)
    assert lags.reference("neighbour", exclude=0.2) == pytest.approx(0.21)
    assert lags.consistent(0.25, 3) and not lags.consistent(0.9, 4)
    assert lags.consistent(0.3, 2) and not lags.consistent(0.6, 1)
    raw = lags.to_raw()
    assert raw == {"neighbour": 0.205, "echo": None, "pairs": {"neighbour": 4, "echo": 1}}


# --- направления ---------------------------------------------------------------------


def test_speaker_echo_in_room_window_is_dropped_from_mic():
    """Колонки: голос собеседника из sys попадает в микрофон чуть позже."""
    text = "по бюджету вопросов больше нет"
    sys = _words(text, 20.0, seg="s1")
    mic = _words(text, 20.03, seg="m1", role="room")
    drops, lags = _find(mic, sys, lag=-0.03)
    assert _reasons(drops) == [("mic", "echo", 5)]
    assert drops[0].lag == pytest.approx(-0.03, abs=0.01)
    assert lags.reference("echo") == pytest.approx(-0.03, abs=0.01)


LEAK_PHRASES = ["я пришлю отчёт до пятницы", "мы созвонимся с поставщиком завтра",
                "надо проверить счёт ещё раз", "давайте закончим на этом сегодня",
                "я напишу всем после обеда"]


def _leak_case(*, same_env=True, with_env=True, lag=0.25, sound_lag=None, leak_voice=_owner_voice, n=5,
               report=None):
    """Окно владельца, n фраз с копией в sys (по 2 с, пять — 10 с звука):
    `lag` — по словам ASR, `sound_lag` — по звуку (по умолчанию тот же)."""
    lead_lag = lag if lag > mic_dedupe.NEIGHBOUR_MIN_LAG else LAG
    sound_lag = lag if sound_lag is None else sound_lag
    mic, sys, me, se = [], [], [], []
    for i, text in enumerate(LEAK_PHRASES[:n]):
        start = 30.0 + i * 8.0
        mic += _words(text, start, seg=f"m{i}", role="owner")
        sys += _words(text, start + lag, seg=f"s{i}")
        me.append((start, 2.0, 50 + i, -10.0))
        se.append((start + sound_lag, 2.0, 50 + i if same_env else 70 + i, -12.0))
    lm, ls, le_m, le_s = _lead(3, lead_lag)
    env = _env(me + le_m, se + le_s) if with_env else None
    drops, lags = mic_dedupe.find(mic + lm, sys + ls, owner_known=True, env=env, leak_voice=leak_voice,
                                  leak_report=report)
    return [d for d in drops if not str(d.words[0].seg).startswith(("L", "S"))], lags


LEAKED = [("sys", "owner_leak", 5)] * 5


def test_owner_leak_with_established_lag_and_same_envelope_is_dropped_from_sys():
    """Два ноутбука рядом: мой голос через ноутбук соседа приходит в мой sys."""
    report = {}
    drops, _ = _leak_case(report=report)
    assert _reasons(drops) == LEAKED
    assert drops[0].to_raw()["pair"]["track"] == "mic" and drops[0].env_corr > 0.9
    assert report == {"candidates": 5, "seconds": pytest.approx(9.6, abs=0.05), "cos": 0.9, "gate": "voice_match"}


def test_owner_leak_needs_envelope_evidence():
    """Собеседник повторил мои слова с тем же лагом, но это другой голос
    (другая огибающая) или огибающей нет — его слова остаются."""
    assert _leak_case(same_env=False)[0] == []
    assert _leak_case(with_env=False)[0] == []


def test_owner_leak_needs_lag_of_at_least_a_tenth():
    """Эхо колонок ещё не откалибровано: пара в окне владельца с лагом 0,07 с
    (эхо с дрожанием начала слов) — не утечка, слова собеседника остаются."""
    drops, _ = _leak_case(lag=0.07)
    assert drops == []
    assert _reasons(_leak_case(lag=0.1)[0]) == LEAKED


def test_owner_leak_needs_the_owner_voice_in_sys():
    """Лаг и огибающая говорят «копия», но центроид голоса всех копий в sys по
    образцу не владельца (или его не посчитать) — это не утечка: оригиналы
    собеседника остаются все."""
    report = {}
    assert _leak_case(leak_voice=lambda spans: (False, 0.41), report=report)[0] == []
    assert report["gate"] == "voice_mismatch" and report["cos"] == 0.41 and report["candidates"] == 5
    report = {}
    assert _leak_case(leak_voice=None, report=report)[0] == []
    assert report["gate"] == "no_voice_check"


def test_owner_leak_needs_enough_audio_to_judge_the_voice():
    """Меньше LEAK_MIN_AUDIO_S кандидатов — голос не решить: ничего не удаляется,
    голос и не спрашивается."""
    asked = []
    report = {}
    drops, _ = _leak_case(n=2, leak_voice=lambda spans: asked.append(spans) or (True, 0.9), report=report)
    assert drops == [] and asked == [] and report["gate"] == "too_little_audio"


def test_owner_leak_voice_is_judged_once_on_all_candidates():
    asked = []
    drops, _ = _leak_case(leak_voice=lambda spans: asked.append(spans) or (True, 0.9))
    assert len(asked) == 1 and len(asked[0]) == 5 and _reasons(drops) == LEAKED


def test_echo_in_owner_window_with_jittered_word_lag_keeps_the_sys_original():
    """Регрессия ревью (I3): эхо колонок внутри окна владельца. По звуку копия
    без сдвига (огибающая та же при 0), а по словам ASR — на 0,1 с позже, и
    L* соседа (0,1) собран из таких же дрожащих пар. Даже если голос копии
    принят за владельца, оригинал собеседника в sys не удаляется."""
    report = {}
    drops, _ = _leak_case(lag=0.1, sound_lag=0.0, report=report)
    assert drops == [] and report["gate"] == "no_candidates"
    # По звуку сдвиг настоящий (0,25) — утечка.
    assert _reasons(_leak_case(lag=0.1, sound_lag=0.25)[0]) == LEAKED


def test_owner_window_with_echo_lag_keeps_both():
    drops, _ = _leak_case(lag=-0.02)
    assert drops == []


def test_asr_variants_still_match():
    """Копия в микрофоне тише — распознана с ошибками; ё, регистр, знаки не мешают."""
    mic = _words("давайте перенесем встречу на завтро", 10.0, seg="m1", role="room")
    sys = _words("Давайте перенесём встречу на завтра.", 10.0 + LAG, seg="s1")
    drops, _ = _find(mic, sys)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert 0.6 <= drops[0].coverage < 1.0


def test_partial_duplicate_inside_long_owner_phrase_drops_only_room_words():
    """Сосед вклинился в мою длинную фразу: убираются только его слова."""
    owner_a = _words("я думаю что нам", 40.0, seg="m1", role="owner")
    room = [Tok(key=("m1", 4 + i), seg="m1", start=w.start, end=w.end, text=w.text, role="room")
            for i, w in enumerate(_words(NEIGHBOUR, 41.6, seg="m1"))]
    owner_b = [Tok(key=("m1", 9 + i), seg="m1", start=w.start, end=w.end, text=w.text, role="owner")
               for i, w in enumerate(_words("и всё", 43.6, seg="m1"))]
    sys = _words(NEIGHBOUR, 41.6 + LAG, seg="s1")
    drops, _ = _find(owner_a + room + owner_b, sys)
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert [w.key for w in drops[0].words] == [w.key for w in room]


def test_mic_heard_only_part_of_long_sys_phrase():
    sys = _words("коллеги давайте перенесём встречу на завтра потому что сервер лежит", 50.0, seg="s1")
    mic = _words("перенесём встречу на завтра", 50.8 - LAG, seg="m1", role="room")
    drops, _ = _find(mic, sys)
    assert _reasons(drops) == [("mic", "neighbour", 4)]
    assert drops[0].lag == pytest.approx(LAG, abs=0.01)


# --- короткие реплики ----------------------------------------------------------------


def _short_case(*, sys_shift, env_same=True, with_env=True, lead=3):
    """Короткое «ага» соседа (отдельный сегмент) при установленном лаге 0,2 с."""
    mic = _words("Ага.", 70.0, seg="m2", role="room")
    sys = _words("ага", 70.0 + sys_shift, seg="s2")
    if not with_env:
        return _find(mic, sys, lead=lead)[0]
    # Та же речь — та же огибающая со сдвигом; другая — «ага» посреди
    # чужой сплошной речи.
    other = (69.0, 3.0, 9, -12.0)
    return _find(mic, sys, lead=lead, mic_ev=[(70.0, 0.32, 7, -30.0)],
                 sys_ev=[(70.0 + sys_shift, 0.32, 7, -12.0) if env_same else other])[0]


def test_short_aga_with_exact_text_lag_and_envelope_is_dropped():
    drops = _short_case(sys_shift=0.25)
    assert [(d.words[0].seg, d.reason) for d in drops] == [("m2", "neighbour")]
    assert drops[0].env_corr >= mic_dedupe.MIN_ENV_CORR


def test_short_aga_needs_envelope():
    assert _short_case(sys_shift=0.25, with_env=False) == []
    assert _short_case(sys_shift=0.25, env_same=False) == []


def test_short_aga_needs_tight_lag():
    assert _short_case(sys_shift=0.6) == []


def test_short_aga_without_established_lag_is_kept():
    assert _short_case(sys_shift=0.25, lead=0) == []


def test_single_word_inside_long_segment_is_never_dropped():
    """Одно совпавшее слово посреди длинной реплики — не прогон, не трогаем."""
    mic = _words("ну вот ага и всё тут", 70.0, seg="m2", role="room")
    sys = _words("ага", 70.8 + LAG, seg="s2")
    drops, _ = _find(mic, sys, mic_ev=[(70.8, 0.32, 7, -30.0)], sys_ev=[(70.8 + LAG, 0.32, 7, -12.0)])
    assert drops == []


# --- повторы и частые слова: не копии ------------------------------------------------


def _readback(role, *, owner_known=True, lead=0, lead_lag=LAG, quiet=True):
    """Владелец диктует номер заказа, собеседник через 1,6 с зачитывает его
    обратно — те же слова, другой голос."""
    mic = _words("номер заказа пять семь три два", 10.0, seg="m1", role=role)
    sys = _words("пять семь три два", 10.8 + 1.6, seg="s1")
    level = -20.0 if quiet else -10.0
    speech = [(w.start, w.end) for w in mic] + [(100.0, 104.0)]
    return _find(mic, sys, owner_known=owner_known, lead=lead, lag=lead_lag, lead_role="owner",
                 mic_ev=[(10.0, 2.4, 11, level), (100.0, 4.0, 12, -10.0)], sys_ev=[(12.4, 1.6, 13, -12.0)],
                 speech=speech)[0]


def test_readback_without_sample_keeps_owner_words():
    """Без образца: тихая фраза владельца не теряется из-за того, что
    собеседник её повторил, — ни без лага, ни с лагом соседа, ни с «лагом»
    самого повтора."""
    assert _readback("owner", owner_known=False) == []
    assert _readback("owner", owner_known=False, lead=3) == []
    assert _readback("owner", owner_known=False, lead=3, lead_lag=1.6) == []


def test_readback_with_sample_keeps_remote_words_in_sys():
    assert _readback("owner") == []
    assert _readback("owner", lead=3, lead_lag=1.6) == []


def test_readback_in_unsure_window_keeps_owner_words():
    """Окно `unsure` показано как владелец: без той же огибающей — не трогаем."""
    assert _readback("unsure") == []
    assert _readback("unsure", lead=3, lead_lag=1.6) == []


def test_unsure_window_copy_with_same_envelope_is_dropped():
    """Сильные улики есть (установленный лаг и та же огибающая) — копия из
    окна `unsure` уходит; другая огибающая или её нет — остаётся."""
    mic = _words(NEIGHBOUR, 10.0, seg="m1", role="unsure")
    sys = _words(NEIGHBOUR, 10.0 + LAG, seg="s1")
    drops, _ = _find(mic, sys, mic_ev=[(10.0, 2.0, 3, -30.0)], sys_ev=[(10.0 + LAG, 2.0, 3, -12.0)])
    assert _reasons(drops) == [("mic", "neighbour", 5)]
    assert _find(mic, sys, mic_ev=[(10.0, 2.0, 3, -30.0)], sys_ev=[(10.0 + LAG, 2.0, 4, -12.0)])[0] == []
    assert _find(mic, sys)[0] == []


def test_common_words_of_two_people_are_not_duplicates():
    """Владелец и собеседник независимо говорят одно и то же короткое:
    «да да конечно» / «да конечно», «ну да» / «ну да» (сосед с лагом 0,2 с
    известен; собеседник сказал «ну да» ровно с этим лагом, но посреди своей
    сплошной речи)."""
    mic = _words("да да конечно", 10.0, seg="m1", role="owner") + _words("ну да", 30.0, seg="m2", role="owner")
    sys = _words("да конечно", 11.0, seg="s1") + _words("ну да", 30.0 + LAG, seg="s2")
    ev = dict(mic_ev=[(10.0, 1.2, 1, -10.0), (30.0, 0.72, 2, -10.0)],
              sys_ev=[(11.0, 0.8, 3, -12.0), (28.0, 5.0, 4, -12.0)])
    assert _find(mic, sys, **ev)[0] == []
    assert _find(mic, sys)[0] == []
    room = [Tok(w.key, w.seg, w.start, w.end, w.text, "room") for w in mic]
    assert _find(room, sys, **ev)[0] == []


# --- без образца ---------------------------------------------------------------------


def test_no_owner_sample_drops_only_quiet_copy_with_evidence():
    """Без образца владельца: копию в микрофоне убираем, только если лаг
    установлен, огибающая та же и копия заметно тише речи микрофона; копию в
    sys не трогаем никогда."""
    owner = _words("я пришлю отчёт до пятницы", 5.0, seg="m0")
    copy = _words(NEIGHBOUR, 10.0, seg="m1")
    sys = _words(NEIGHBOUR, 10.0 + LAG, seg="s1")
    speech = [(w.start, w.end) for w in owner + copy]

    def run(level, *, with_env=True, copy_seed=1):
        if not with_env:
            return _find(owner + copy, sys, owner_known=False, lead_role="owner")[0]
        return _find(owner + copy, sys, owner_known=False, lead_role="owner", speech=speech,
                     mic_ev=[(5.0, 2.0, 5, -10.0), (10.0, 2.0, copy_seed, level)],
                     sys_ev=[(10.0 + LAG, 2.0, 1, -12.0)])[0]

    assert _reasons(run(-35.0)) == [("mic", "neighbour", 5)]
    assert run(-10.0) == []  # громко — может быть сам владелец
    assert run(-35.0, with_env=False) == []
    assert run(-35.0, copy_seed=2) == []  # другая огибающая — не копия
    # Владелец (громкий) с утечкой в sys: без образца sys не трогаем.
    leak = _words("я пришлю отчёт до пятницы", 5.0 + LAG, seg="s2")
    got = _find(owner, leak, owner_known=False, lead_role="owner", speech=speech,
                mic_ev=[(5.0, 2.0, 5, -10.0)], sys_ev=[(5.0 + LAG, 2.0, 5, -12.0)])[0]
    assert got == []


# --- живой режим ---------------------------------------------------------------------


def test_live_match_learns_lag_from_independent_lines():
    """Живой режим: реплика микрофона против кольца недавних слов sys. Пока
    независимых пар меньше трёх — ничего не удаляется; дальше копии уходят."""
    lags = Lags()
    got = []
    for i, text in enumerate(LEAD_PHRASES):
        mic = _words(text, 10.0 * i, seg=f"m{i}", role="room")
        sys = _words(text, 10.0 * i + LAG, seg=f"s{i}")
        got.append(_reasons(mic_dedupe.match(mic, sys, lags=lags)))
    assert got[:3] == [[], [], []] and got[3] == [("mic", "neighbour", len(LEAD_PHRASES[3].split()))]
    env = _env([(60.0, 0.32, 7, -30.0)], [(60.0 + LAG, 0.32, 7, -12.0)])
    short = mic_dedupe.match(_words("ага", 60.0, seg="m9", role="room"), _words("ага", 60.0 + LAG, seg="s9"),
                             lags=lags, env=env)
    assert _reasons(short) == [("mic", "neighbour", 1)]


def test_empty_inputs():
    assert mic_dedupe.find([], [], owner_known=True)[0] == []
    assert mic_dedupe.find(_words("ага", 1.0, seg="m"), [], owner_known=True)[0] == []
    assert mic_dedupe.find([], _words("ага", 1.0, seg="s"), owner_known=True)[0] == []
