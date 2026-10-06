"""Изображения, вставленные или перетащенные в чат ассистента (дизайн V4, §5.1).

Модель получает картинку не как пришла, а нормализованной:

* длинная сторона — не больше MAX_SIDE (1 568 px — больше модель всё равно
  уменьшит, а за пиксели платится контекстом);
* поворот из EXIF применяется к пикселям, сам EXIF (и прочие метаданные:
  GPS, модель камеры, профили) отбрасывается;
* PNG, если он после уменьшения не больше MAX_OUTPUT_BYTES, иначе JPEG
  q=85 (при необходимости — ниже);
* на входе — не больше MAX_INPUT_BYTES и MAX_INPUT_SIDE по каждой стороне,
  только растровые форматы из FORMATS (никаких EPS и прочего, что Pillow
  открывает через внешние программы).

Результат — `<папка записи>/assistant/files/<id>.png|jpg`; id общий с
материалами (`a<N>`, `materials.claim_id`). На сессию — не больше
MAX_PER_SESSION изображений, в одном сообщении — MAX_PER_MESSAGE (это
проверяет тот, кто собирает сообщение).
"""

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from meet import materials

MAX_SIDE = 1568
MAX_OUTPUT_BYTES = 1_500_000
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_INPUT_SIDE = 8000
MAX_PER_MESSAGE = 5
MAX_PER_SESSION = 20
JPEG_QUALITY = 85
# Если и q=85 не влезает (шум, мелкая текстура) — ниже, потом меньше.
JPEG_FALLBACK = (75, 65, 50)
FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "BMP", "TIFF")
EXTS = {"image/png": ".png", "image/jpeg": ".jpg"}

TOO_BIG = "Изображение больше 10 МБ"
TOO_LARGE = "Изображение больше 8000 × 8000 точек"
NOT_IMAGE = "Это не изображение (или файл повреждён): нужен PNG, JPEG, GIF, WEBP, BMP или TIFF"
TOO_MANY = "В сессии уже 20 изображений — больше модели не отправить"
NOT_FIT = "Изображение не сжать до 1,5 МБ"


class AttachmentError(ValueError):
    """Изображение не принять (текст — человеку)."""


@dataclass(frozen=True)
class Normalized:
    data: bytes
    media_type: str
    width: int
    height: int
    original: dict  # {"format", "width", "height", "bytes"}

    @property
    def ext(self) -> str:
        return EXTS[self.media_type]


def _open(data: bytes):
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError,
            SyntaxError) as e:
        raise AttachmentError(NOT_IMAGE) from e
    if image.width > MAX_INPUT_SIDE or image.height > MAX_INPUT_SIDE:
        raise AttachmentError(TOO_LARGE)
    return image


def _has_alpha(image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def _flatten(image):
    """Без прозрачности — для JPEG: на белом фоне (скриншоты, схемы)."""
    from PIL import Image

    if _has_alpha(image):
        rgba = image.convert("RGBA")
        back = Image.new("RGB", rgba.size, (255, 255, 255))
        back.paste(rgba, mask=rgba.getchannel("A"))
        return back
    return image.convert("RGB") if image.mode != "RGB" else image


def _encode(image, fmt: str, **options) -> bytes:
    out = io.BytesIO()
    # Ни exif, ни icc_profile, ни текстовых полей: save получает только то,
    # что передано явно, а info исходника не переносится.
    image.save(out, fmt, **options)
    return out.getvalue()


def normalize(data: bytes) -> Normalized:
    """Байты изображения → нормализованное (см. описание модуля)."""
    from PIL import Image, ImageOps

    if len(data) > MAX_INPUT_BYTES:
        raise AttachmentError(TOO_BIG)
    image = _open(data)
    original = {"format": image.format, "width": image.width, "height": image.height, "bytes": len(data)}
    try:
        image.seek(0)  # анимация — первый кадр
        image.load()
        image = ImageOps.exif_transpose(image) or image
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise AttachmentError(NOT_IMAGE) from e
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "PA"):
        image = image.convert("RGBA" if _has_alpha(image) else "RGB")
    if image.mode in ("P", "PA"):
        image = image.convert("RGBA" if _has_alpha(image) else "RGB")
    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
    # Новый объект без метаданных исходника: info (exif, icc, dpi, комментарии)
    # не уходит дальше ни в PNG, ни в JPEG.
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    png = _encode(clean, "PNG", optimize=True)
    if len(png) <= MAX_OUTPUT_BYTES:
        return Normalized(png, "image/png", clean.width, clean.height, original)
    rgb = _flatten(clean)
    for quality in (JPEG_QUALITY, *JPEG_FALLBACK):
        jpeg = _encode(rgb, "JPEG", quality=quality, optimize=True)
        if len(jpeg) <= MAX_OUTPUT_BYTES:
            return Normalized(jpeg, "image/jpeg", rgb.width, rgb.height, original)
    raise AttachmentError(NOT_FIT)


def files_dir(folder) -> Path:
    return materials.assistant_dir(folder) / materials.FILES_DIR


def count(folder) -> int:
    """Сколько изображений уже в сессии."""
    try:
        return sum(1 for p in files_dir(folder).iterdir()
                   if p.suffix.lower() in EXTS.values() and p.stat().st_size > 0)
    except OSError:
        return 0


def save(folder, data: bytes, *, name: str | None = None) -> dict:
    """Нормализовать и положить в `assistant/files/<id>.<ext>` → описание
    вложения для журнала: {id, type: "image", name, path, media_type, width,
    height, bytes, sha256, original}."""
    if count(folder) >= MAX_PER_SESSION:
        raise AttachmentError(TOO_MANY)
    got = normalize(data)
    aid, path = materials.claim_id(folder, got.ext)
    try:
        path.write_bytes(got.data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"id": aid, "type": "image", "name": name or f"{aid}{got.ext}", "path": str(path),
            "media_type": got.media_type, "width": got.width, "height": got.height,
            "bytes": len(got.data), "sha256": hashlib.sha256(got.data).hexdigest(),
            "original": got.original}


def save_file(folder, path) -> dict:
    """Изображение с диска (перетаскивание, «📎»): исходник не трогается."""
    path = Path(path)
    try:
        # Не больше предела (+1, чтобы заметить больший): файл мог вырасти
        # между проверкой и чтением.
        with open(path, "rb") as f:
            data = f.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise AttachmentError(TOO_BIG)
    except FileNotFoundError:
        raise AttachmentError(f"Файла нет: {path.name}") from None
    except OSError as e:
        raise AttachmentError(f"Файл не прочитать: {path.name} ({e.strerror or e})") from None
    return save(folder, data, name=path.name)
