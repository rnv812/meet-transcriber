"""Где хранить движок и модели: модели Meet при переносе в выбранную папку.

Переносом командует оболочка (`app/src-tauri/src/storage.rs`): ставит движок в
новую папку, потом зовёт отсюда копирование моделей (`python -m meet.storage
copy --to <папка>` — интерпретатором уже нового движка), переключает
`storage.json` и перезапускает резидент. Здесь — то, что знает только Python:
какие модели принадлежат Meet (каталог `meet.models`) и как устроен кэш
Hugging Face.

Раскладка папки (та же в `storage.rs`):

    <папка>/engine/<версия>   — окружение движка
    <папка>/models/hf         — свой кэш HF: только модели каталога Meet
    <папка>/models/gigaam     — веса GigaAM
    <папка>/models/xet        — кэш кусков загрузчика Xet

Без выбранной папки — как до 0.3.3: движок и GigaAM в data_dir, модели HF в
общем кэше (`models.shared_cache_root`). Чужие модели общего кэша не
копируются и не удаляются никогда; свои — удаляются из общего кэша только с
согласия человека (`answer_leftovers`), когда копия в новой папке на месте.

Копия проверяется: SHA-256 скопированного файла должен совпасть с SHA-256
прочитанного оригинала. Копирование идемпотентно: файл, который уже лежит
на месте и совпадает по размеру и сумме, не копируется заново — повтор после
сбоя докопирует только недостающее.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from meet import models, paths

# Журнал идущего переноса (пишет и удаляет оболочка). Есть — перенос идёт или
# прерван и будет доведён/откачен при следующем запуске оболочки: модели в это
# время не качаются, иначе новая модель легла бы в старое место мимо копии.
JOURNAL_FILE = "storage-move.json"
# После переезда из общего кэша оболочка оставляет этот файл: окно один раз
# спрашивает, удалить ли модели Meet из общего кэша (`answer_leftovers`).
LEFTOVERS_FILE = "storage-leftovers.json"

_CHUNK = 1 << 20
_TMP_SUFFIX = ".meet-copy"
# Недокачанное GigaAM (`gigaam_asr._PART`) не переносим: докачается заново.
_SKIP_SUFFIXES = (".part", _TMP_SUFFIX, ".lock", ".incomplete")
# Запас места сверх копии: журналы, временные файлы, метаданные ФС.
SPACE_MARGIN = 256 << 20


class CopyError(RuntimeError):
    """Копия не удалась; текст — для человека."""


@dataclass(frozen=True)
class Layout:
    """Где лежат модели Meet при данной папке (None — по умолчанию)."""

    home: Path
    custom: bool

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def gigaam(self) -> Path:
        return self.models / "gigaam"

    @property
    def hf(self) -> Path:
        return self.models / "hf" if self.custom else models.shared_cache_root()


def layout(root: Path | None) -> Layout:
    return Layout(home=root, custom=True) if root is not None else Layout(paths.data_dir(), False)


def current() -> Layout:
    return layout(paths.storage_root())


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _repo_folder(cache: Path, repo_id: str) -> Path:
    return cache / ("models--" + repo_id.replace("/", "--"))


def hf_repos() -> list[str]:
    """Модели каталога, которые живут в кэше Hugging Face (не GigaAM)."""
    return [m["id"] for m in models.CATALOGUE if models.gigaam_name(m["id"]) is None]


def _files_under(root: Path) -> list[Path]:
    """Обычные файлы под папкой; ссылка на файл (снапшот HF → blobs) — тоже
    файл: копируется содержимое. Недописанное и временное — мимо."""
    out = []
    if not root.is_dir():
        return out
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = Path(dirpath) / name
            if name.endswith(_SKIP_SUFFIXES):
                continue
            try:
                if path.is_file():
                    out.append(path)
            except OSError:
                continue
    return sorted(out)


def _repo_files(folder: Path) -> list[Path]:
    """Файлы репозитория для копии: `refs/` и `snapshots/` с разыменованными
    ссылками. `blobs/` не нужен: загрузчики находят файл по пути снапшота
    (так же лежит кэш HF на Windows без символических ссылок)."""
    return _files_under(folder / "refs") + _files_under(folder / "snapshots")


def _sources(src: Layout, dst: Layout | None = None) -> list[tuple[Path, Path]]:
    """(файл, его путь относительно места) моделей Meet: HF — от кэша, GigaAM —
    от своей папки. С `dst` места, которые уже совпадают с местами `dst` (HF
    или GigaAM уже там), пропускаются."""
    out = []
    if dst is None or not _same(src.hf, dst.hf):
        for repo in hf_repos():
            folder = _repo_folder(src.hf, repo)
            out += [(path, Path("hf") / path.relative_to(src.hf)) for path in _repo_files(folder)]
    if dst is None or not _same(src.gigaam, dst.gigaam):
        out += [(path, Path("gigaam") / path.relative_to(src.gigaam))
                for path in _files_under(src.gigaam)]
    return out


def _pairs(src: Layout, dst: Layout) -> list[tuple[Path, Path, int]]:
    """(откуда, куда, размер) для всех файлов моделей Meet."""
    sized = []
    for path, rel in _sources(src, dst):
        base = dst.hf if rel.parts[0] == "hf" else dst.gigaam
        try:
            sized.append((path, base.joinpath(*rel.parts[1:]), path.stat().st_size))
        except OSError:
            continue
    return sized


def models_bytes() -> int:
    """Сколько весят модели Meet на текущем месте (сколько займёт копия)."""
    total = 0
    for path, _ in _sources(current()):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            h.update(block)
    return h.hexdigest()


def _free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return shutil.disk_usage(target).free


def _already(src: Path, dst: Path, size: int) -> bool:
    try:
        if dst.stat().st_size != size:
            return False
    except OSError:
        return False
    return _digest(dst) == _digest(src)


def _copy_one(src: Path, dst: Path, on_bytes) -> None:
    """Скопировать через временный файл, сверить и только тогда положить на
    место; время изменения — как у оригинала (GigaAM по нему не пересчитывает
    сумму весов)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + _TMP_SUFFIX)
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while block := fin.read(_CHUNK):
                fout.write(block)
                h.update(block)
                on_bytes(len(block))
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(tmp, dst)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise CopyError(f"не удалось скопировать {src.name}: {e.strerror or type(e).__name__}") from e
    st = src.stat()
    try:
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass  # ФС без точного времени (FAT): GigaAM один раз пересчитает сумму
    if _digest(dst) != h.hexdigest():
        dst.unlink(missing_ok=True)
        raise CopyError(f"копия {src.name} не совпала с оригиналом — диск неисправен или отключился")


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f}".replace(".", ",")


def copy_models(to_root: Path, on_progress=None) -> dict:
    """Скопировать модели Meet из текущего места в раскладку папки `to_root`.

    `on_progress(сделано, всего)` — байты. Источник не меняется. → {"files",
    "bytes", "copied"}; сбой — CopyError с текстом для человека."""
    pairs = _pairs(current(), layout(Path(to_root)))
    total = sum(size for _, _, size in pairs)
    missing = sum(size for src, dst, size in pairs
                  if not dst.is_file() or dst.stat().st_size != size)
    target = Path(to_root) / "models"
    try:
        free = _free_bytes(target)
    except OSError as e:
        raise CopyError(f"папка {to_root} недоступна: {e.strerror or type(e).__name__}") from e
    if missing and free < missing + SPACE_MARGIN:
        raise CopyError(f"Недостаточно места для моделей: нужно {_gb(missing + SPACE_MARGIN)} ГБ, "
                        f"свободно {_gb(free)} ГБ")
    done = copied = 0

    def advance(n: int) -> None:
        nonlocal done
        done += n
        if on_progress:
            on_progress(done, total)

    for src, dst, size in pairs:
        if _already(src, dst, size):
            advance(size)
            continue
        before = done
        _copy_one(src, dst, advance)
        copied += done - before
    if on_progress:
        on_progress(total, total)
    return {"files": len(pairs), "bytes": total, "copied": copied}


# --- остатки в общем кэше ----------------------------------------------------


def moving() -> bool:
    """Идёт перенос (до переключения на новую папку). Уборка прежней папки
    после переключения (`"phase": "cleanup"`) загрузкам не мешает: качается
    уже в новую папку. Нечитаемый журнал — считаем, что идёт."""
    try:
        raw = json.loads((paths.data_dir() / JOURNAL_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not (isinstance(raw, dict) and raw.get("phase") == "cleanup")


def _complete_in_own(repo: str) -> bool:
    """Копия репозитория в своём кэше цела: refs/main указывает на снапшот, и
    в нём есть файлы."""
    folder = _repo_folder(models.cache_root(), repo)
    try:
        ref = (folder / "refs" / "main").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return False
    snapshot = folder / "snapshots" / ref
    return bool(ref) and bool(_files_under(snapshot))


def _size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def leftovers() -> dict | None:
    """Модели Meet, оставшиеся в общем кэше HF после переезда в свою папку, —
    только те, чья копия в своём кэше цела. Вопроса нет (не переезжали, уже
    ответили, общий кэш и есть свой) — None."""
    if not (paths.data_dir() / LEFTOVERS_FILE).exists() or paths.storage_root() is None:
        return None
    shared = models.shared_cache_root()
    if _same(shared, models.cache_root()):
        return None
    repos = []
    for repo in hf_repos():
        folder = _repo_folder(shared, repo)
        if folder.is_dir() and _complete_in_own(repo):
            repos.append({"id": repo, "bytes": _size(folder)})
    if not repos:
        return None
    return {"cache": str(shared), "repos": repos, "bytes": sum(r["bytes"] for r in repos)}


def answer_leftovers(delete: bool) -> dict:
    """Ответ на вопрос об остатках: `delete` — удалить модели Meet из общего
    кэша (только из списка `leftovers`), иначе — оставить. Вопрос больше не
    задаётся в обоих случаях. → {"ok", "removed", "error"?}."""
    found = leftovers() if delete else None
    removed, failed = [], []
    shared = models.shared_cache_root()
    for item in (found or {}).get("repos", []):
        folder = _repo_folder(shared, item["id"])
        # Имя папки — строго из каталога, и она — прямо в общем кэше.
        if not _same(folder.parent, shared):
            continue
        try:
            shutil.rmtree(folder)
            removed.append(item["id"])
        except OSError:
            failed.append(item["id"])
    if failed:
        return {"ok": False, "removed": removed,
                "error": "не удалось удалить: " + ", ".join(failed)
                         + " — файлы заняты другой программой, попробуйте позже"}
    (paths.data_dir() / LEFTOVERS_FILE).unlink(missing_ok=True)
    return {"ok": True, "removed": removed}


def info() -> dict:
    """Где сейчас движок и модели — для окна и оболочки."""
    root = paths.storage_root()
    here = current()
    return {
        "root": str(root) if root is not None else None,
        "custom": root is not None,
        "home": str(here.home),
        "engine_dir": str(paths.engine_dir()),
        "models_dir": str(paths.models_dir()),
        "hf_cache": str(here.hf),
        "shared_cache": str(models.shared_cache_root()),
        "missing": str(m) if (m := paths.storage_missing()) is not None else None,
        "models_bytes": models_bytes(),
        "moving": moving(),
        "leftovers": leftovers(),
    }


# --- командная строка для оболочки -------------------------------------------

_PROGRESS_GAP_S = 0.2


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    """`copy --to <папка>`: построчный JSON — `{"done", "total"}` по ходу (не
    чаще 5 раз в секунду), в конце `{"ok": true, "bytes"}` или `{"error"}`."""
    parser = argparse.ArgumentParser(prog="python -m meet.storage")
    sub = parser.add_subparsers(dest="command", required=True)
    p_copy = sub.add_parser("copy")
    p_copy.add_argument("--to", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    last = [0.0]

    def progress(done: int, total: int) -> None:
        now = time.monotonic()
        if done >= total or now - last[0] >= _PROGRESS_GAP_S:
            last[0] = now
            _emit({"done": done, "total": total})

    try:
        result = copy_models(Path(args.to), on_progress=progress)
    except CopyError as e:
        _emit({"error": str(e)})
        return 1
    except OSError as e:
        _emit({"error": f"копирование моделей не удалось: {e.strerror or type(e).__name__}"})
        return 1
    _emit({"ok": True, "bytes": result["bytes"], "copied": result["copied"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
