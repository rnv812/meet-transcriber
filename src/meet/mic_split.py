"""Микрофон по голосам: владелец, люди рядом с ним в комнате и дубли.

Сейчас вся дорожка микрофона подписывается владельцем («Вы»). Но в микрофон
говорят и люди рядом — коллега за соседним столом, переговорная с колонками,
сосед со своим ноутбуком в том же звонке (его слова тогда приходят ещё и через
звонок). Здесь микрофон делится по голосам «лёгкой диаризацией», без pyannote:

1. окна голоса по словам ASR (1,5–3 с: разрыв на паузе или на 3 с; короткие
   хвосты — к соседнему окну, иначе окно «короткое», без голоса);
2. голос окна — эмбеддинг WeSpeaker (та же модель, что у базы голосов);
3. быстрый путь: почти все окна похожи на образец владельца — весь микрофон его;
4. иначе кластеры окон (средняя связь до порога, уточнение), мелкие — к
   ближайшему; роль кластера по образцу владельца (meet.owner_voice):
   `owner`, `room` (человек в комнате) или `unsure` (владелец с пометкой);
5. дубли соседа и эхо (meet.mic_dedupe) — по ролям окон;
6. человек в комнате получает подпись кластера sys с тем же голосом, имя из
   базы или «Спикер N» (SPEAKER_M…);
7. слова раздаются по меткам окон; короткие берут метку соседнего окна.

Без образца владельца, без эмбеддера или если образец не похож ни на один
крупный голос микрофона — поведение как раньше (весь микрофон — владелец),
дубли — только заметно тихие копии. Чистая функция `run`: встраивание в
пайплайн, шаг и артефакты на диске — задача T6.

Пороги откалиброваны T0 на записях владельца с его согласия (2026-10-05,
.superpowers/sdd/v033/t0-calibration.md; scripts/speakers_calib.py). Важное
из калибровки: окна одного человека похожи друг на друга слабо (cos ≈
0.5–0.6) — решения принимаются по кластерам, пороги для окон низкие;
громкость людей в комнате не отличает (на 1–2 дБ тише владельца) — серая
зона только `unsure`. См. .superpowers/sdd/v033/speakers-design.md, §2.3, §3.2."""

from __future__ import annotations

import wave
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from meet import mic_dedupe, owner_voice, speaker_split
from meet.asr import Segment, Word

RULE = 1
SAMPLE_RATE = 16000

# --- окна голоса ---
# Окно режется на паузе длиннее WIN_GAP и на длине WIN_MAX (T0 мерил на таких окнах).
WIN_GAP = 0.3
WIN_MAX = 3.0
# Хвост короче TAIL_MIN — к соседнему окну, если пауза меньше TAIL_JOIN_GAP. Старт (T0).
TAIL_MIN = 1.0
TAIL_JOIN_GAP = 0.5
# Короче — эмбеддинг неустойчив (как voice_id.MIN_SECONDS): окно «короткое».
EMBED_MIN = 1.0
# Короткое окно берёт метку ближайшего окна с голосом не дальше INHERIT_S. Старт (T0).
INHERIT_S = 1.5

# --- владелец (§2.3) ---
# Центроид кластера против образца: от T_OWN — владелец, ниже T_OTHER — точно
# не он, между — `unsure` (владелец с пометкой). T0: кластеры владельца
# 0.81–0.95 (другой микрофон — 0.855), не-владельцы до 0.58 (человек в
# комнате). По громкости не решаем: люди в комнате на 1–2 дБ тише владельца.
T_OWN = 0.75
T_OTHER = 0.65
# Быстрый путь: от FAST_SHARE секунд окон со сходством от T_WIN_FAST — весь
# микрофон владельца. T0: у одного владельца от 0.45 — 98–99 % секунд (от
# 0.60 — только 88 %), при людях в комнате — 60–71 % (верный отказ).
T_WIN_FAST = 0.45
FAST_SHARE = 0.95
# Кластер короче MIN_DECIDE_S речи — `unsure` (владелец с пометкой), если он не
# похож на образец хотя бы на T_OWN: при остановке 0.35 остаются хвосты по
# 4–12 с с cos 0.2–0.45 (T0).
MIN_DECIDE_S = 10.0
# Владелец найден, если среди кластеров от OWNER_PRESENT_SHARE речи есть
# похожий на образец хотя бы на T_OTHER. Иначе — owner_not_found. Старт (T0).
OWNER_PRESENT_SHARE = 0.30
# Решать по голосу (и быстрый путь, и кластеры) — только от MIN_VOICED_S
# секунд окон с голосом: меньше — статус no_voice, микрофон как раньше и
# никаких удалений «по голосу владельца». Старт (T0).
MIN_VOICED_S = 10.0

# --- кластеры ---
# Слияние групп окон — пока средняя близость не ниже AHC_STOP. T0: при 0.55
# даже микрофон одного владельца рассыпался на 10 кластеров (а с людьми в
# комнате — на 67), при 0.35 — владелец одним кластером, соседи — своими.
AHC_STOP = 0.35
# Человек в комнате с голосом кластера sys (сосед в том же звонке) — от
# SYS_LINK. T0: центроиды community-1 в том же пространстве, что сырые
# эмбеддинги (cos 0.91–0.98), но не единичной длины — сравниваем нормированные.
SYS_LINK = 0.65
# Потоков torch на эмбеддинг окон: с потоками по умолчанию время на гибридных
# ядрах скачет 170–1300 мс на окно, с 4 — 60–110 мс (T0).
EMBED_THREADS = 4

# --- живые имена (T9/T10, калибровка T0; здесь не используются) ---
# Имя в живом режиме: накоплено от LIVE_MIN_SPEECH_S речи, сходство от
# live_threshold(asr.voice_threshold), отрыв от второго от LIVE_MARGIN, тот же
# кандидат LIVE_CHECKS проверки подряд (каждые +3 с речи). T0: при 0.68–0.70
# ложных имён 0 из 30, при нынешних 0.77 названо 8 из 12 известных.
LIVE_THRESHOLD_CAP = 0.70
LIVE_THRESHOLD_BELOW = 0.05
LIVE_MIN_SPEECH_S = 8.0
LIVE_CHECKS = 2
LIVE_MARGIN = 0.05
# Сегмент → онлайн-кластер дорожки при cos от LIVE_ASSIGN (и отрыве
# LIVE_MARGIN); кластеры сливаются при cos от LIVE_MERGE, если оба набрали от
# LIVE_MERGE_MIN_S речи (T0: один человек — p10 0.70, разные — max 0.55 на 15 с).
LIVE_ASSIGN = 0.55
LIVE_MERGE = 0.65
LIVE_MERGE_MIN_S = 15.0


def live_threshold(voice_threshold: float) -> float:
    """Порог живого имени: на LIVE_THRESHOLD_BELOW ниже порога узнавания, но
    не выше LIVE_THRESHOLD_CAP."""
    return min(float(voice_threshold) - LIVE_THRESHOLD_BELOW, LIVE_THRESHOLD_CAP)


STATUS_OK = "ok"
STATUS_NO_PROFILE = "no_profile"
STATUS_NOT_FOUND = "owner_not_found"
STATUS_NO_TOKEN = "skipped_no_token"
STATUS_OFF = "off"
STATUS_NO_VOICE = "no_voice"
STATUS_ERROR = "skipped_error"
OWNER_LABEL = "OWNER"
REASONS = ("echo", "neighbour", "owner_leak")


@dataclass
class Window:
    """Окно голоса: слова (номер сегмента, номер слова) подряд одного куска речи."""
    start: float
    end: float
    keys: list[tuple[int, int]]
    short: bool = False
    emb: np.ndarray | None = None
    role: str = mic_dedupe.OWNER
    label: str | None = None

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class MicResult:
    """mic — сегменты микрофона с подписями (track="mic"); sys — сегменты sys
    без утечек владельца; dropped — удалённые копии (mic_voices.json);
    sidecar — голоса микрофона для сайдкара; report — поле `mic_split`
    транскрипта; voices — содержимое mic_voices.json."""
    mic: list[Segment]
    sys: list[Segment]
    dropped: list[dict]
    sidecar: list[dict]
    report: dict
    voices: dict = field(default_factory=dict)


# --- слова и окна ---------------------------------------------------------------


def _words_of(seg: Segment) -> tuple[list[Word], bool]:
    """Слова сегмента; без пословных таймкодов — слова текста, равномерно по
    времени сегмента (False: времена выдуманы, в сегмент они не вернутся)."""
    if seg.words:
        return list(seg.words), True
    tokens = str(seg.text or "").split()
    if not tokens:
        return [], False
    step = max(0.0, float(seg.end) - float(seg.start)) / len(tokens)
    return [Word(seg.start + i * step, seg.start + (i + 1) * step, " " + t) for i, t in enumerate(tokens)], False


def windows(segs: list[Segment]) -> list[Window]:
    """Окна голоса по словам всех сегментов микрофона."""
    items = sorted(((w.start, w.end, (si, wi)) for si, seg in enumerate(segs) if seg.kind != "break"
                    for wi, w in enumerate(_words_of(seg)[0])), key=lambda x: (x[0], x[1]))
    raw: list[Window] = []
    for start, end, key in items:
        cur = raw[-1] if raw else None
        if cur is not None and start - cur.end <= WIN_GAP and end - cur.start <= WIN_MAX:
            cur.end = max(cur.end, end)
            cur.keys.append(key)
        else:
            raw.append(Window(start, end, [key]))
    out: list[Window] = []
    for win in raw:
        last = out[-1] if out else None
        if last is not None and win.start - last.end < TAIL_JOIN_GAP and (
                win.seconds < TAIL_MIN or last.seconds < TAIL_MIN):
            last.end = max(last.end, win.end)
            last.keys += win.keys
            continue
        out.append(win)
    for win in out:
        win.short = win.seconds < EMBED_MIN
    return out


# --- звук -----------------------------------------------------------------------


def _read(path: Path) -> np.ndarray:
    """WAV 16 кГц моно (как его пишет пайплайн) — напрямую, прочее — через ffmpeg."""
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getframerate() == SAMPLE_RATE and wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    except (wave.Error, EOFError):
        pass
    from meet import segvoices

    return segvoices.decode(Path(path), SAMPLE_RATE)


@contextmanager
def _torch_threads(n: int):
    """Потоков torch не больше n на время эмбеддинга окон; потом — как было."""
    try:
        import torch
    except Exception:
        yield
        return
    before = torch.get_num_threads()
    torch.set_num_threads(max(1, min(n, before)))
    try:
        yield
    finally:
        torch.set_num_threads(before)


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _embed(wins: list[Window], audio: np.ndarray, embed) -> None:
    for win in wins:
        if win.short or win.emb is not None:
            continue
        a, b = int(win.start * SAMPLE_RATE), int(win.end * SAMPLE_RATE)
        clip = audio[max(0, a):max(0, b)].astype(np.float32) / 32768.0
        if clip.size < int(EMBED_MIN * SAMPLE_RATE):
            win.short = True
            continue
        vec = embed(clip)
        vec = None if vec is None else np.asarray(vec, dtype=np.float64)
        if vec is None or not vec.size or not np.isfinite(vec).all() or not np.linalg.norm(vec):
            win.short = True
            continue
        win.emb = _unit(vec)


def _centroid(wins: list[Window]) -> np.ndarray | None:
    use = [w for w in wins if w.emb is not None]
    if not use:
        return None
    return _unit((np.stack([w.emb for w in use]) * np.array([w.seconds for w in use])[:, None]).sum(0))


def _cos(a: np.ndarray, b) -> float:
    b = np.asarray(b, dtype=np.float64)
    if a is None or b.shape != a.shape or not np.linalg.norm(b):
        return -1.0
    return float(a @ _unit(b))


# --- роли -----------------------------------------------------------------------


def _fast(voiced: list[Window], owner, device: str | None) -> bool:
    """Быстрый путь: почти все секунды окон похожи на образец."""
    secs = np.array([w.seconds for w in voiced])
    scores = np.array([owner_voice.score(w.emb, owner, device) for w in voiced])
    return bool(secs.sum() >= MIN_VOICED_S and secs[scores >= T_WIN_FAST].sum() >= FAST_SHARE * secs.sum())


def _groups(wins: list[Window]) -> list[list[Window]]:
    """Кластеры окон с голосом: средняя связь до AHC_STOP и уточнение к центрам."""
    x = np.stack([w.emb for w in wins])
    secs = np.array([w.seconds for w in wins])
    labels = speaker_split.ahc_threshold(x, secs, AHC_STOP)
    k = int(labels.max()) + 1
    if k > 1:
        labels, _ = speaker_split._refine(x, secs, labels, k)
    groups: dict[int, list[Window]] = {}
    for win, g in zip(wins, labels):
        groups.setdefault(int(g), []).append(win)
    return list(groups.values())


def _roles(wins: list[Window], owner, device: str | None
           ) -> tuple[str, bool, list[dict], list[list[Window]], np.ndarray | None]:
    """Роли окон с голосом по образцу владельца. → (статус, быстрый путь,
    кластеры для mic_voices.json, группы окон в том же порядке, кандидат в
    голос владельца при owner_not_found — центроид крупнейшего голоса от
    OWNER_PRESENT_SHARE речи)."""
    voiced = [w for w in wins if w.emb is not None]
    secs = np.array([w.seconds for w in voiced])
    if secs.sum() < MIN_VOICED_S:
        # Голоса почти нет (короткие окна, эмбеддер не справился): решать
        # «по голосу владельца» не на чем.
        return STATUS_NO_VOICE, False, [], [], None
    if _fast(voiced, owner, device):
        c = _centroid(voiced)
        return STATUS_OK, True, [{"id": None, "seconds": round(float(secs.sum()), 2),
                                  "owner_cos": round(owner_voice.score(c, owner, device), 3),
                                  "role": "owner", "label": None, "link": None}], [voiced], None
    groups = _groups(voiced)
    total = float(secs.sum())
    info = [{"seconds": sum(w.seconds for w in g), "cos": owner_voice.score(_centroid(g), owner, device)}
            for g in groups]
    if not any(i["seconds"] >= OWNER_PRESENT_SHARE * total and i["cos"] >= T_OTHER for i in info):
        # Образец не узнал владельца (другой микрофон, шум): крупнейший голос
        # микрофона — кандидат, по нему «Это я» поправит образец из встречи.
        big = max(range(len(groups)), key=lambda k: info[k]["seconds"])
        candidate = _centroid(groups[big]) if info[big]["seconds"] >= OWNER_PRESENT_SHARE * total else None
        return STATUS_NOT_FOUND, False, [], [], candidate
    clusters = []
    for g, i in zip(groups, info):
        if i["cos"] >= T_OWN:
            role = "owner"
        elif i["cos"] < T_OTHER and i["seconds"] >= MIN_DECIDE_S:
            role = "room"
        else:
            role = "unsure"  # серая зона или мало речи: владелец с пометкой
        for w in g:
            w.role = role
        clusters.append({"id": None, "seconds": round(i["seconds"], 2), "owner_cos": round(i["cos"], 3),
                         "role": role, "label": None, "link": None})
    return STATUS_OK, False, clusters, groups, None


def _name_room(center: np.ndarray, diar, names: dict, base: dict, threshold: float) -> tuple[str | None, dict]:
    """Подпись человека в комнате: кластер sys с тем же голосом, имя из базы
    или None (новый «Спикер N»)."""
    sys_emb = getattr(diar, "embeddings", None) if diar is not None and not getattr(diar, "skipped", None) else None
    if sys_emb:
        scored = sorted(((_cos(center, v), label) for label, v in sys_emb.items()), reverse=True)
        score, label = scored[0]
        if score >= SYS_LINK:
            return names.get(label, label), {"kind": "sys", "to": label, "score": round(score, 3)}
    if base:
        from meet import voices

        got = voices.match_speakers({"mic": center.astype(np.float32)}, base, threshold=threshold)
        if "mic" in got:
            score = max(_cos(center, s) for s in base[got["mic"]])
            return got["mic"], {"kind": "base", "to": got["mic"], "score": round(score, 3)}
    return None, {"kind": "new", "to": None, "score": None}


def _inherit(wins: list[Window]) -> None:
    """Короткие окна — роль и метка ближайшего окна с голосом не дальше
    INHERIT_S (при равенстве — предыдущего); иначе — владелец."""
    voiced = [w for w in wins if w.emb is not None]
    for win in wins:
        if win.emb is not None:
            continue
        best, gap = None, float("inf")
        for other in voiced:  # по времени: при равенстве остаётся предыдущее
            d = max(0.0, other.start - win.end, win.start - other.end)
            if d < gap:
                best, gap = other, d
        if best is not None and gap <= INHERIT_S:
            win.role, win.label = best.role, best.label
        else:
            win.role, win.label = mic_dedupe.OWNER, None


# --- сборка ---------------------------------------------------------------------


def _rebuild(segs: list[Segment], dropped: set, label_of=None, track: str | None = None) -> list[Segment]:
    """Сегменты без удалённых слов, разрезанные по меткам слов `label_of(key)
    -> (подпись, uncertain)`. Неизменный сегмент остаётся как был (текст, поля)."""
    out: list[Segment] = []
    for si, seg in enumerate(segs):
        if seg.kind == "break":
            out.append(seg)
            continue
        words, real = _words_of(seg)
        keep = [(wi, w) for wi, w in enumerate(words) if (si, wi) not in dropped]
        if not keep and words:
            continue
        runs: list[tuple[tuple, list[Word]]] = []
        for wi, w in keep:
            key = label_of((si, wi)) if label_of else (seg.speaker, seg.uncertain)
            if runs and runs[-1][0] == key:
                runs[-1][1].append(w)
            else:
                runs.append((key, [w]))
        if len(runs) <= 1 and len(keep) == len(words):
            if runs:
                speaker, uncertain = runs[0][0]
            else:  # сегмент без текста — подпись по умолчанию
                speaker, uncertain = label_of((si, -1)) if label_of else (seg.speaker, seg.uncertain)
            out.append(replace(seg, speaker=speaker, uncertain=bool(uncertain or seg.uncertain),
                               track=track if track is not None else seg.track))
            continue
        for (speaker, uncertain), run in runs:
            # Прочие поля сегмента (уверенность распознавания) — от исходного.
            out.append(replace(seg, start=run[0].start, end=run[-1].end,
                               text="".join(w.text for w in run).strip(), speaker=speaker,
                               words=list(run) if real else [], uncertain=bool(uncertain or seg.uncertain),
                               track=track if track is not None else seg.track))
    return out


def _release() -> None:
    """Эмбеддер, загруженный здесь, отпущен: видеопамять — следующим шагам
    (как распознавание: del model, empty_cache)."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _leak_voice(sys_audio, embed, owner, device: str | None):
    """Голос кандидатов в утечку владельца (mic_dedupe, вся встреча): центроид
    эмбеддингов отрезков sys (вес — длительность, отрезки короче EMBED_MIN не
    считаются) против образца владельца — от T_OWN, как у кластера микрофона.
    → (владелец?, cos | None, номера посчитанных отрезков | None — сбой)."""
    def check(spans: list[tuple[float, float]]) -> tuple[bool, float | None, list[int] | None]:
        vecs, weights, used = [], [], []
        try:
            with _torch_threads(EMBED_THREADS):
                for n, (start, end) in enumerate(spans):
                    a, b = max(0, int(start * SAMPLE_RATE)), max(0, int(end * SAMPLE_RATE))
                    clip = sys_audio[a:b].astype(np.float32) / 32768.0
                    if clip.size < int(EMBED_MIN * SAMPLE_RATE):
                        continue
                    vec = embed(clip)
                    vec = None if vec is None else np.asarray(vec, dtype=np.float64)
                    if vec is None or not vec.size or not np.isfinite(vec).all() or not np.linalg.norm(vec):
                        continue
                    vecs.append(_unit(vec))
                    weights.append(clip.size / SAMPLE_RATE)
                    used.append(n)
        except Exception:
            return False, None, None
        if not vecs:
            return False, None, []
        cos = owner_voice.score(_unit((np.stack(vecs) * np.array(weights)[:, None]).sum(0)), owner, device)
        return cos >= T_OWN, cos, used
    return check


def _load_embedder(log) -> tuple:
    """Эмбеддер segvoices. → (эмбеддер, статус). Нет токена HF —
    skipped_no_token (подсказка про токен), прочие сбои — skipped_error."""
    from meet import credentials, segvoices

    try:
        return segvoices.load_embedder(), STATUS_OK
    except Exception as e:
        log(f"микрофон: голоса не посчитать, разделения нет ({type(e).__name__}: {e})")
        try:
            token = credentials.get_hf_token()
        except Exception:
            token = None
        return None, STATUS_NO_TOKEN if not token else STATUS_ERROR


def _toks(segs: list[Segment], track: str, role_of=None) -> list[mic_dedupe.Tok]:
    out = []
    for si, seg in enumerate(segs):
        if seg.kind == "break":
            continue
        for wi, w in enumerate(_words_of(seg)[0]):
            role = role_of((si, wi)) if role_of else mic_dedupe.OWNER
            out.append(mic_dedupe.Tok(key=(track, si, wi), seg=(track, si), start=float(w.start),
                                      end=float(w.end), text=w.text, role=role))
    return out


def run(mic_segs: list[Segment], sys_segs: list[Segment], mic_wav: Path, sys_wav: Path, diar, *,
        owner: list, base: dict, threshold: float, owner_label: str, embed=None, log=print,
        names: dict | None = None, device: str | None = None, speakers: bool = True,
        dedupe: bool = True, no_token: bool = False, aligned: bool | None = None) -> MicResult:
    """Микрофон по голосам и без дублей.

    mic_segs — распознанный микрофон (слова с таймкодами); sys_segs — sys
    после split_by_speaker (подписи); diar — диаризация sys (центроиды
    кластеров — для людей в комнате, которые есть и в звонке), names — её
    метки → имена из базы; owner — образцы владельца (owner_voice.load),
    device — микрофон записи; base — база голосов, threshold — порог узнавания.
    speakers/dedupe — настройки asr.mic_speakers / asr.mic_dedupe. `embed`
    (звук → вектор) подменяется в тестах; None — эмбеддер segvoices.
    no_token — диаризация уже не получила модель (нет токена HF или
    доступа): эмбеддер из того же чекпойнта и не пробуем (и образец без неё
    не записать — статус «нет токена», а не «нет образца»). aligned —
    выравнивались ли слова sys (wav2vec): лаги пар — для калибровки.

    Эмбеддер, загруженный здесь, — вторая копия WeSpeaker после того, как
    pyannote свою уже отпустил (~3 с загрузки); в конце он отпускается."""
    from meet.diarize import DIARIZATION_MODEL

    names = names or {}
    wins = windows(mic_segs)
    want_split = speakers and bool(owner) and bool(wins)
    want_dedupe = dedupe and bool(wins) and bool(sys_segs)
    # Звук микрофона — только если он нужен; не прочитался — как раньше.
    mic_audio = None
    if want_split or want_dedupe:
        try:
            mic_audio = _read(mic_wav)
        except (Exception, SystemExit) as e:
            log(f"микрофон: звук не прочитать, разделения и дублей нет ({type(e).__name__})")
    status, fast, clusters, groups, candidate = STATUS_OK, False, [], [], None
    loaded = False
    if not speakers:
        status = STATUS_OFF
    elif no_token and embed is None and wins:
        status = STATUS_NO_TOKEN
    elif not owner:
        status = STATUS_NO_PROFILE
    elif wins and mic_audio is None:
        status = STATUS_ERROR
    elif wins:
        if embed is None:
            embed, status = _load_embedder(log)
            loaded = embed is not None
        if embed is not None:
            # Голос — у всех окон: быстрый путь по выборке пропускал соседа,
            # говорящего несколько процентов времени, и тот становился «Вы»,
            # а его копии в sys удалялись как утечка владельца (~1 мин на час CPU).
            try:
                with _torch_threads(EMBED_THREADS):
                    _embed(wins, mic_audio, embed)
            except Exception as e:  # CUDA OOM, сбой модели посреди окон
                log(f"микрофон: голоса окон не посчитать, разделения нет ({type(e).__name__})")
                status = STATUS_ERROR
            else:
                status, fast, clusters, groups, candidate = _roles(wins, owner, device)
    split = status == STATUS_OK
    if not split:
        for win in wins:
            win.emb, win.role, win.label = None, mic_dedupe.OWNER, None

    # Подписи людей в комнате — по порядку первого появления.
    rooms = sorted((i for i, c in enumerate(clusters) if c["role"] == "room"),
                   key=lambda i: min(w.start for w in groups[i]))
    room_center: dict[str, np.ndarray] = {}
    for n, i in enumerate(rooms):
        center = _centroid(groups[i])
        name, link = _name_room(center, diar, names, base, threshold)
        label = name or f"SPEAKER_M{n}"
        clusters[i].update(id=f"M{n}", label=label, link=link)
        room_center[f"SPEAKER_M{n}"] = center
        for w in groups[i]:
            w.label = label
    counters = {"O": 0, "U": 0}
    for c in clusters:
        if c["id"] is None:
            prefix = "O" if c["role"] == "owner" else "U"
            c["id"], c["label"] = f"{prefix}{counters[prefix]}", owner_label
            counters[prefix] += 1
    _inherit(wins)

    word: dict[tuple[int, int], Window] = {k: w for w in wins for k in w.keys}

    def label_of(key):
        w = word.get(key)
        if w is None or w.role == mic_dedupe.OWNER:
            return owner_label, False
        if w.role == "unsure":
            return owner_label, True
        return w.label or owner_label, False

    def role_of(key):
        w = word.get(key)
        return w.role if w is not None else mic_dedupe.OWNER

    drops: list[mic_dedupe.Drop] = []
    lags = None
    # Чем решено об утечках владельца (mic_dedupe._leaks): кандидатов,
    # секунд, cos центроида, gate — для калибровки.
    leak: dict = {}
    if want_dedupe:
        mic_toks, sys_toks = _toks(mic_segs, "mic", role_of), _toks(sys_segs, "sys")
        env = leak_voice = None
        if mic_audio is not None:
            try:
                sys_audio = _read(sys_wav)
                env = mic_dedupe.Envelope.from_audio(mic_audio, sys_audio, SAMPLE_RATE,
                                                     speech=[(t.start, t.end) for t in mic_toks])
            except (Exception, SystemExit) as e:
                log(f"микрофон: громкость дорожек не прочитать, дубли только по словам ({type(e).__name__})")
            else:
                if split and embed is not None:
                    leak_voice = _leak_voice(sys_audio, embed, owner, device)
        drops, lags = mic_dedupe.find(mic_toks, sys_toks, owner_known=split, env=env, leak_voice=leak_voice,
                                      leak_report=leak)
    gone = {"mic": set(), "sys": set()}
    for d in drops:
        for w in d.words:
            gone[d.track].add(w.key[1:])
    mic_out = _rebuild(mic_segs, gone["mic"], label_of, track="mic")
    sys_out = _rebuild(sys_segs, gone["sys"]) if gone["sys"] else list(sys_segs)

    present = {s.speaker for s in mic_out}
    room_labels = {c["label"] for c in clusters if c["role"] == "room"} & present
    sidecar = []
    owner_center = _centroid([w for w in wins if w.role == mic_dedupe.OWNER and w.emb is not None]) if split else None
    if owner_center is not None:
        sidecar.append({"label": OWNER_LABEL, "display": owner_label,
                        "embedding": [float(v) for v in owner_center], "track": "mic", "owner": True})
    elif candidate is not None:
        # owner_not_found: не проверенный образцом голос, только кандидат.
        sidecar.append({"label": OWNER_LABEL, "display": owner_label, "embedding": [float(v) for v in candidate],
                        "track": "mic", "owner": True, "candidate": True})
    for c in clusters:
        if c["role"] == "room" and c["label"] in present:
            center = room_center[f"SPEAKER_{c['id']}"]
            sidecar.append({"label": f"SPEAKER_{c['id']}", "display": c["label"],
                            "embedding": [float(v) for v in center], "track": "mic"})
    dropped = [d.to_raw() for d in drops]
    counts = {r: sum(d.reason == r for d in drops) for r in REASONS}
    report = {"rule": RULE, "status": status,
              "owner_profile": owner_voice.best_source(owner_center, owner, device) if owner_center is not None
              else None,
              "room_speakers": len(room_labels), "dropped": counts}
    lag_s = lags.to_raw() if lags is not None else None
    if lag_s is not None:
        lag_s["align"] = aligned
    voices_file = {"version": 1, "rule": RULE, "model": DIARIZATION_MODEL, "status": status, "fast": fast,
                   "align": aligned, "lag_s": lag_s, "leak": leak or None, "clusters": clusters,
                   "dropped": dropped}
    if loaded:
        # Замыкание проверки утечек тоже держит модель: без него empty_cache
        # видеопамять не вернёт.
        leak_voice = None  # noqa: F841
        del embed
        _release()
    log(f"микрофон: окон {len(wins)}, голосов {len(clusters)}, людей в комнате {len(room_labels)}, "
        f"убрано дублей {len(drops)} ({status}{', быстрый путь' if fast else ''})")
    return MicResult(mic=mic_out, sys=sys_out, dropped=dropped, sidecar=sidecar, report=report, voices=voices_file)
