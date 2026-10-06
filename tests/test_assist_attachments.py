"""Изображения для чата ассистента (`meet.assist.attachments`): уменьшение до
1 568 px, поворот по EXIF и удаление метаданных, PNG или JPEG, пределы."""

import io
import os

import pytest
from PIL import Image

from meet.assist import attachments


def _gradient(width: int, height: int):
    return Image.linear_gradient("L").resize((width, height)).convert("RGB")


def _jpeg_with_exif(width: int, height: int, orientation: int = 1) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    exif[0x010F] = "Камера Тест"  # Make
    exif[0x0131] = "редактор"  # Software
    out = io.BytesIO()
    _gradient(width, height).save(out, "JPEG", quality=90, exif=exif.tobytes())
    return out.getvalue()


def _png(image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_big_photo_is_scaled_rotated_and_stripped_of_exif():
    data = _jpeg_with_exif(4000, 2000, orientation=6)  # 6 — повернуть на 90° по часовой
    assert b"Exif" in data
    got = attachments.normalize(data)
    # поворот применён к пикселям: была 4000×2000, стала стоя
    assert (got.width, got.height) == (784, 1568)
    assert got.original == {"format": "JPEG", "width": 4000, "height": 2000, "bytes": len(data)}
    assert len(got.data) <= attachments.MAX_OUTPUT_BYTES
    out = Image.open(io.BytesIO(got.data))
    assert out.size == (784, 1568) and max(out.size) <= attachments.MAX_SIDE
    assert dict(out.getexif()) == {}
    assert b"Exif" not in got.data and "Камера".encode() not in got.data
    assert "exif" not in out.info and "icc_profile" not in out.info


def test_small_screenshot_stays_png_with_transparency():
    image = Image.new("RGBA", (300, 200), (10, 20, 30, 0))
    image.paste((200, 0, 0, 255), (50, 50, 150, 150))
    got = attachments.normalize(_png(image))
    assert got.media_type == "image/png" and got.ext == ".png"
    assert (got.width, got.height) == (300, 200)
    out = Image.open(io.BytesIO(got.data))
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0


def test_noisy_image_falls_back_to_jpeg_under_the_size_limit():
    noise = Image.frombytes("RGB", (1500, 1100), os.urandom(1500 * 1100 * 3))
    data = _png(noise)
    assert len(data) > attachments.MAX_OUTPUT_BYTES
    got = attachments.normalize(data)
    assert got.media_type == "image/jpeg" and got.ext == ".jpg"
    assert len(got.data) <= attachments.MAX_OUTPUT_BYTES
    assert Image.open(io.BytesIO(got.data)).format == "JPEG"


def test_input_limits_and_garbage(monkeypatch):
    with pytest.raises(attachments.AttachmentError, match="не изображение"):
        attachments.normalize(b"%PDF-1.4 not an image")
    with pytest.raises(attachments.AttachmentError, match="8000"):
        attachments.normalize(_png(Image.new("L", (8001, 4))))
    monkeypatch.setattr(attachments, "MAX_INPUT_BYTES", 100)
    with pytest.raises(attachments.AttachmentError, match="10 МБ"):
        attachments.normalize(_png(_gradient(64, 64)))


def test_save_writes_into_assistant_files_with_a_shared_id(tmp_path, monkeypatch):
    rec = tmp_path / "2026-10-06_10-00"
    (rec / "assistant" / "materials").mkdir(parents=True)
    (rec / "assistant" / "materials" / "a1.json").write_text("{}", encoding="utf-8")
    got = attachments.save(rec, _jpeg_with_exif(2000, 1000), name="скрин.jpg")
    assert got["id"] == "a2" and got["type"] == "image" and got["name"] == "скрин.jpg"
    assert got["path"] == str(rec / "assistant" / "files" / "a2.png")
    assert (got["width"], got["height"]) == (1568, 784)
    assert open(got["path"], "rb").read()[:8] == b"\x89PNG\r\n\x1a\n"
    assert attachments.count(rec) == 1
    monkeypatch.setattr(attachments, "MAX_PER_SESSION", 1)
    with pytest.raises(attachments.AttachmentError, match="20 изображений"):
        attachments.save(rec, _png(_gradient(10, 10)))


def test_save_file_reads_from_disk_without_touching_the_source(tmp_path):
    src = tmp_path / "фото.jpg"
    src.write_bytes(_jpeg_with_exif(1800, 900))
    before = src.read_bytes()
    got = attachments.save_file(tmp_path / "rec", src)
    assert got["name"] == "фото.jpg" and got["width"] == 1568
    assert src.read_bytes() == before
    with pytest.raises(attachments.AttachmentError, match="Файла нет"):
        attachments.save_file(tmp_path / "rec", tmp_path / "нет.png")
