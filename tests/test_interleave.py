from meet.asr import Segment, Word
from meet.interleave import _cut_segment, interleave_tracks


def _seg(speaker, words):
    ws = [Word(*w) for w in words]
    text = "".join(w[2] for w in words).strip()
    return Segment(ws[0].start, ws[-1].end, text, speaker, words=ws)


def test_reply_inside_long_block_lands_in_place():
    long = _seg("Даня", [(0.0, 2.0, " Смотрите"), (2.0, 4.0, " адаптивные"), (8.0, 10.0, " и потом решим")])
    reply = _seg("Вы", [(5.0, 6.0, " Ну да")])
    got = interleave_tracks([long], [reply])
    assert [(s.speaker, s.text) for s in got] == [
        ("Даня", "Смотрите адаптивные"),
        ("Вы", "Ну да"),
        ("Даня", "и потом решим"),
    ]


def test_parts_keep_words_and_word_timecodes():
    long = _seg("Даня", [(0.0, 2.0, " а"), (6.0, 8.0, " б")])
    reply = _seg("Вы", [(3.0, 4.0, " ок")])
    got = interleave_tracks([long], [reply])
    assert got[0].words and got[0].end == 2.0
    assert got[2].words and got[2].start == 6.0


def test_multiple_replies_inside_one_block():
    long = _seg("Даня", [(0.0, 1.0, " а"), (2.0, 3.0, " б"), (4.0, 5.0, " в")])
    r1 = _seg("Вы", [(1.5, 1.8, " раз")])
    r2 = _seg("Вы", [(3.5, 3.8, " два")])
    got = interleave_tracks([long], [r1, r2])
    assert [(s.speaker, s.text) for s in got] == [
        ("Даня", "а"), ("Вы", "раз"), ("Даня", "б"), ("Вы", "два"), ("Даня", "в"),
    ]


def test_cut_outside_or_at_boundary_no_split():
    seg = _seg("Даня", [(1.0, 2.0, " а"), (2.0, 3.0, " б")])
    # до сегмента, ровно start, ровно end, после — разреза нет
    assert _cut_segment(seg, [0.5, 1.0, 3.0, 9.9]) == [seg]


def test_segment_without_words_not_cut():
    seg = Segment(0.0, 10.0, "без слов", "Даня")
    reply = _seg("Вы", [(5.0, 6.0, " ок")])
    got = interleave_tracks([seg], [reply])
    assert [(s.speaker, s.text) for s in got] == [("Даня", "без слов"), ("Вы", "ок")]


def test_empty_track_just_sorts():
    a = [_seg("Даня", [(3.0, 4.0, " б")]), _seg("Даня", [(0.0, 1.0, " а")])]
    got = interleave_tracks(a, [])
    assert [s.text for s in got] == ["а", "б"]


def test_symmetric_mic_long_phrase_cut_by_sys_reply():
    mic = _seg("Вы", [(0.0, 2.0, " Я думаю"), (6.0, 8.0, " и вот почему")])
    sys_r = _seg("Зоя", [(3.0, 4.0, " Ага")])
    got = interleave_tracks([sys_r], [mic])
    assert [(s.speaker, s.text) for s in got] == [
        ("Вы", "Я думаю"), ("Зоя", "Ага"), ("Вы", "и вот почему"),
    ]


def test_equal_start_tie_break_short_first():
    # продолжение начинается ровно в start реплики -> короткая реплика раньше
    long = _seg("Даня", [(0.0, 1.0, " а"), (5.0, 9.0, " продолжение")])
    reply = _seg("Вы", [(5.0, 6.0, " ок")])
    got = interleave_tracks([long], [reply])
    assert [(s.speaker, s.text) for s in got] == [
        ("Даня", "а"), ("Вы", "ок"), ("Даня", "продолжение"),
    ]
