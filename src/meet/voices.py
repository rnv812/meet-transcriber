"""База голосов: имя человека → эмбеддинги его голоса (WeSpeaker-центроиды
из pyannote community-1). Только векторы, без звука; см. спеку
docs/superpowers/specs/2026-07-02-speaker-enrollment-design.md."""

import json
import os
import uuid
from pathlib import Path

import numpy as np

from meet import paths
from meet.diarize import DIARIZATION_MODEL


def voices_dir() -> Path:
    """Папка базы голосов: настройка, иначе путь по умолчанию из paths.

    Функция, а не константа: раньше путь был относительным (`Path("voices")`) и
    зависел от рабочей папки процесса, а под треем и в установленном режиме она
    произвольная."""
    from meet import settings

    return settings.load().recording.voices
# Калибровка 03.07.2026 (встреча 16-31): свои cos 0.89-1.00, дальние чужие
# 0.32-0.54, а ПОХОЖИЙ чужой голос — 0.61 (кластер Соколова ложно прошёл
# прежний порог 0.6 по образцу Вадима и «исчез» под чужим именем). Порог
# поднят в зазор 0.61..0.89: похожий голос честно остаётся «Спикер N».
# Прежняя калибровка 02.07 (свои 0.91-0.93 между встречами) запас сохраняет.
THRESHOLD = 0.75
MARGIN = 0.05


def load_voices(folder: Path | None = None) -> dict[str, list[np.ndarray]]:
    """Имя → список эмбеддингов. Битый файл пропускается с предупреждением."""
    folder = folder or voices_dir()
    out: dict[str, list[np.ndarray]] = {}
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            samples = [np.asarray(s["embedding"], dtype=np.float32) for s in data["samples"]]
        except Exception as e:
            print(f"голоса: пропускаю {f.name} (ошибка: {e})")
            continue
        if samples:
            out[f.stem] = samples
    return out


def _read_samples(f: Path) -> list[dict]:
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    return [s for s in data.get("samples", []) if isinstance(s, dict)]


def _write_samples(f: Path, samples: list[dict]) -> None:
    """Атомарно: оборванная запись не должна оставить человека без голоса."""
    tmp = f.with_name(f".{f.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"samples": samples}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
    finally:
        tmp.unlink(missing_ok=True)


def _same_cluster(sample: dict, source: str, label: str | None) -> bool:
    """Образец того же кластера той же встречи. Старые образцы (и `meet enroll`)
    метки не знают: для них встреча = кластер, как и было."""
    if sample.get("source") != source:
        return False
    return label is None or sample.get("label") in (None, label)


def enroll_sample(
    name: str,
    embedding: list[float],
    source: str,
    date: str,
    folder: Path | None = None,
    *,
    label: str | None = None,
    recording: str | None = None,
) -> dict:
    """Дописать образец голоса с id и сказать, что при этом случилось:
    {"person", "sample_id", "created": файла не было, "replaced": [{"person",
    "sample"}] — образцы того же кластера, которые запись вытеснила}. По этому
    откат из окна (панель «Спикеры») убирает ровно свой образец и возвращает
    вытесненные.

    Повтор из того же source заменяет прежний образец. С меткой кластера
    (`label`) — только образец этого кластера, зато у любого человека: кластер
    переназвали — голос переезжает, а не остаётся и у прежнего имени.

    Имя = имя файла: `..\\..\\x` писал бы мимо папки голосов, `Демьян: ПМ` —
    в альтернативный поток NTFS, а сверхдлинное потом не переименовать и не
    удалить из окна. Недопустимое имя — ValueError до записи чего-либо."""
    from meet.people import valid_name

    name = valid_name(name)
    folder = folder or voices_dir()
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{name}.json"
    created = not f.exists()
    replaced: list[dict] = []
    if label is not None:
        for other in sorted(folder.glob("*.json")):
            if other.stem == name or (not created and os.path.samefile(other, f)):
                continue  # сам человек (и «демьян» = «Демьян» на Windows)
            try:
                samples = _read_samples(other)
            except (OSError, ValueError):
                continue
            moved = [s for s in samples if s.get("source") == source and s.get("label") == label]
            if moved:
                replaced += [{"person": other.stem, "sample": s} for s in moved]
                _write_samples(other, [s for s in samples if s not in moved])
    samples = _read_samples(f)
    replaced += [{"person": name, "sample": s} for s in samples if _same_cluster(s, source, label)]
    kept = [s for s in samples if not _same_cluster(s, source, label)]
    sample = {"embedding": [float(x) for x in embedding], "source": source, "date": date,
              "id": uuid.uuid4().hex}
    if label is not None:
        sample["label"] = label
    if recording is not None:
        sample["recording"] = recording
    _write_samples(f, kept + [sample])
    return {"person": name, "sample_id": sample["id"], "created": created, "replaced": replaced}


def add_sample(
    name: str,
    embedding: list[float],
    source: str,
    date: str,
    folder: Path | None = None,
) -> Path:
    """Дописать образец голоса; повтор из того же source заменяет старый образец.
    Недопустимое имя — ValueError до записи чего-либо (см. enroll_sample)."""
    folder = folder or voices_dir()
    got = enroll_sample(name, embedding, source, date, folder)
    return folder / f"{got['person']}.json"


def remove_sample(sample_id: str, person: str, folder: Path | None = None,
                  restore: list[dict] | None = None) -> dict:
    """Убрать образец по id и вернуть вытесненные им (`restore` — как в
    ответе enroll_sample). Ищем у `person`, а если человека с тех пор
    переименовали или слили — по всей базе.

    Ответ: {"left": {человек: сколько у него осталось образцов} — для файла,
    где образец нашёлся (не нашёлся — {}), "skipped": [люди]} — кому вытесненные
    не вернули, потому что их с тех пор удалили из базы: откат не воскрешает
    удалённого человека. Ошибка записи файла — OSError наружу."""
    folder = folder or voices_dir()
    candidates = [folder / f"{person}.json"] + sorted(folder.glob("*.json"))
    found: dict[str, int] = {}
    for f in candidates:
        try:
            samples = _read_samples(f)
        except (OSError, ValueError):
            continue
        left = [s for s in samples if s.get("id") != sample_id]
        if len(left) != len(samples):
            _write_samples(f, left)
            found = {f.stem: len(left)}
            break
    from meet.people import valid_name

    skipped: list[str] = []
    for item in restore or []:
        name = str(item.get("person") or "")
        try:
            f = folder / f"{valid_name(name)}.json"
        except ValueError:
            continue
        if not f.exists():
            if name not in skipped:
                skipped.append(name)
            continue
        samples = _read_samples(f)
        sample = item.get("sample")
        if isinstance(sample, dict) and sample not in samples:
            _write_samples(f, samples + [sample])
            if f.stem in found:
                found[f.stem] += 1
    return {"left": found, "skipped": skipped}


def sidecar_path(out_md: Path) -> Path:
    """Путь сайдкара эмбеддингов рядом с транскриптом."""
    return out_md.with_name(f"{out_md.stem.removesuffix('_transcript')}_speakers.json")


def write_sidecar(out_md: Path, source: str, date: str, speakers: list[dict]) -> Path:
    p = sidecar_path(out_md)
    p.write_text(
        json.dumps(
            {"model": DIARIZATION_MODEL, "source": source, "date": date, "speakers": speakers},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def read_sidecar(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else -1.0


def best_match(
    emb: np.ndarray, voices: dict[str, list[np.ndarray]]
) -> tuple[float, str, float | None]:
    """Лучший человек по косинусу (максимум по его образцам) и score
    ближайшего ДРУГОГО человека (None, если в базе один человек).
    voices должен быть непустым — проверяет вызывающий."""
    scores = sorted(
        ((max(_cos(emb, s) for s in samples), name) for name, samples in voices.items()),
        reverse=True,
    )
    best_score, best_name = scores[0]
    second = scores[1][0] if len(scores) > 1 else None
    return best_score, best_name, second


def match_speakers(
    embeddings: dict[str, np.ndarray],
    voices: dict[str, list[np.ndarray]],
    threshold: float = THRESHOLD,
    margin: float = MARGIN,
) -> dict[str, str]:
    """Уверенные совпадения «метка диаризации → имя из базы».

    Уверенность: близость лучшего кандидата ≥ threshold И отрыв от лучшего
    ДРУГОГО человека ≥ margin (иначе честный «Спикер N», а не угаданное имя).
    Человек = максимум косинуса по его образцам."""
    if not voices:
        return {}
    matched: dict[str, str] = {}
    for label, emb in embeddings.items():
        best_score, best_name, second = best_match(emb, voices)
        if best_score >= threshold and (second is None or best_score - second >= margin):
            matched[label] = best_name
            # IMPORTANT: в консольных логах только ASCII-пунктуация («->», не «→»):
            # консоль Windows (cp866) не кодирует стрелки/тире, print падает
            # UnicodeEncodeError, и except в _match_names глотает весь матчинг.
            print(f"голоса: {label} -> {best_name} (cos {best_score:.2f})")
        else:
            print(f"голоса: {label} -> не распознан (лучший кандидат: {best_name}, cos {best_score:.2f})")
    return matched


def _find_sidecar(path: Path) -> Path:
    """Сайдкар по папке записи, транскрипту или прямому пути к .json."""
    retry = "перетранскрибируй запись (meet transcribe) — сайдкар появится рядом с транскриптом"
    if path.is_dir():
        found = sorted(path.glob("*_speakers.json"))
        if not found:
            raise SystemExit(f"В {path} нет *_speakers.json — {retry}")
        return found[-1]
    if path.suffix == ".json" and path.exists():
        return path
    if path.suffix == ".md":
        p = sidecar_path(path)
        if p.exists():
            return p
        raise SystemExit(f"Рядом с {path.name} нет {p.name} — {retry}")
    raise SystemExit(f"Не найден сайдкар для {path} — дай папку записи или *_speakers.json")


def enroll(path_str: str, mappings: list[str], folder: Path | None = None) -> None:
    """Перенести эмбеддинги из сайдкара записи в базу голосов.

    mappings: «Спикер 1=Демьян Петров» (имя из транскрипта или сырая метка SPEAKER_XX)."""
    folder = folder or voices_dir()
    sidecar = _find_sidecar(Path(path_str))
    try:
        data = read_sidecar(sidecar)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Битый сайдкар {sidecar.name}: {e}")
    by_key: dict[str, dict] = {}
    for s in data["speakers"]:
        # если два кластера авто-совпали с одним человеком, их display-имена
        # совпадают и побеждает последняя запись — приемлемо: авто-совпавших
        # спикеров в описанном сценарии повторно не энроллят
        by_key[s["display"]] = s
        by_key[s["label"]] = s
    source = data.get("source", str(sidecar))
    date = data.get("date", "")
    from meet.people import valid_name

    # Сначала разобрать и проверить все соответствия, потом писать: опечатка во
    # втором не должна оставить первое наполовину применённым.
    pairs: list[tuple[str, str, dict]] = []
    for m in mappings:
        who, sep, name = m.partition("=")
        who, name = who.strip(), name.strip()
        if not sep or not who or not name:
            raise SystemExit(f"Непонятное соответствие «{m}» — формат: \"Спикер 1=Демьян Петров\" (со знаком =)")
        entry = by_key.get(who)
        if entry is None:
            known = ", ".join(s["display"] for s in data["speakers"])
            raise SystemExit(f"В {sidecar.name} нет спикера «{who}»; есть: {known}")
        try:
            name = valid_name(name)
        except ValueError as e:
            raise SystemExit(f"Недопустимое имя «{name}»: {e} (имя становится именем файла)")
        pairs.append((who, name, entry))
    for who, name, entry in pairs:
        f = add_sample(name, entry["embedding"], source=source, date=date, folder=folder)
        total = len(json.loads(f.read_text(encoding="utf-8"))["samples"])
        print(f"голоса: {name} += образец из {source} (всего образцов: {total})")
    renamed = _name_in_transcript(sidecar.parent, {entry["display"]: name for _, name, entry in pairs})
    if renamed:
        print(f"транскрипт: переименовано реплик: {renamed}")


def _name_in_transcript(recording: Path, pairs: dict[str, str]) -> int:
    """Имена в transcript.json папки записи — как при «Назвать спикеров» в окне
    (tray_control.name_speakers): реплики «Спикер N» → имя, соответствие
    в `names`. Иначе `meet enroll` из консоли пополнял базу, а транскрипт
    оставался с «Спикер N» — редактор и статистика людей имени не видели.
    Нет transcript.json (одиночный файл, запись до v1) — нечего править.
    Пишем только при изменениях: окно зовёт enroll уже после своей правки."""
    from meet import library

    data = library.read_transcript(recording)
    if not data or not pairs:
        return 0
    renamed = 0
    segments = data.get("segments")
    for segment in segments if isinstance(segments, list) else []:
        if isinstance(segment, dict) and segment.get("speaker") in pairs:
            segment["speaker"] = pairs[segment["speaker"]]
            renamed += 1
    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    updated = {**names, **pairs}
    if renamed or updated != names:
        data["names"] = updated
        library.write_transcript(recording, data)
    return renamed
