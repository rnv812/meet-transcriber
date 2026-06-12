# meet — локальный транскрибатор встреч

Запись и расшифровка рабочих встреч целиком на своей машине (GPU).
Ничего не отправляется наружу.

## Использование

Проще всего — двойной клик по `meet.bat` в корне проекта: откроется меню
(записать / транскрибировать последнюю запись / транскрибировать файл).
Активировать venv не нужно. Те же команды из терминала:

    meet record                      # записать встречу (Ctrl+C — стоп)
    meet transcribe recordings/...   # папка записи → transcript.md
    meet transcribe запись.mp4       # или любой аудио/видеофайл → запись.md

Имена спикеров («Спикер 1» → «Демьян») проще всего поправить заменой в md.

## Установка

    py -3.12 -m venv .venv
    source .venv/Scripts/activate
    pip install torch --index-url https://download.pytorch.org/whl/cu128
    pip install -r requirements.txt
    pip install -e .

Также нужны: ffmpeg (`winget install Gyan.FFmpeg`) и токен HuggingFace
в переменной `HF_TOKEN` (принять условия ТРЁХ моделей на hf.co:
pyannote/speaker-diarization-3.1, pyannote/segmentation-3.0
и pyannote/speaker-diarization-community-1 — последнюю pyannote 4.x
тянет как зависимость пайплайна 3.1).

Если в консоли кракозябры вместо русского — выполнить `set PYTHONUTF8=1`
(или `setx PYTHONUTF8 1`), на сам транскрипт это не влияет.

Подробности: docs/superpowers/specs/2026-06-12-transcriber-design.md
