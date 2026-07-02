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

Полезные флаги transcribe:

    --speakers N      # число говорящих, если уверены (обычно авто точнее;
                      # для папки записи — не считая вас)
    --hotwords "..."  # термины через запятую — подсказка распознаванию
    --no-align        # отключить forced alignment (по умолчанию включён:
                      # уточняет пословные таймкоды wav2vec2 — точнее стыки спикеров)

Имена спикеров («Спикер 1» → «Демьян») проще всего поправить заменой в md.

## Кнопка записи (трей)

Запись без Claude: ярлык запускает `meet-tray` — в трее появляется красная
иконка (наведение — сколько идёт запись), правый клик → «Остановить запись»
→ уведомление с папкой записи. Повторный запуск при идущей записи просто
сообщит «Запись уже идёт».

Создать ярлык на рабочем столе (из корня репозитория, powershell):

    $root = (Get-Location).Path
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:USERPROFILE\Desktop\Запись встречи.lnk")
    $s.TargetPath = "$root\.venv\Scripts\meet-tray.exe"
    $s.WorkingDirectory = $root
    $s.Save()

На ноутбуке: `git pull`, `.venv/Scripts/pip install -e .` (подтянет pystray),
затем та же команда ярлыка.

## Установка

    py -3.12 -m venv .venv
    source .venv/Scripts/activate
    pip install torch --index-url https://download.pytorch.org/whl/cu128
    pip install -r requirements.txt
    pip install -e .

Также нужны: ffmpeg (`winget install Gyan.FFmpeg`) и токен HuggingFace
в переменной `HF_TOKEN` (принять условия модели на hf.co:
pyannote/speaker-diarization-community-1).

Если в консоли кракозябры вместо русского — выполнить `set PYTHONUTF8=1`
(или `setx PYTHONUTF8 1`), на сам транскрипт это не влияет.

## Запись на другой машине

Запись не требует ни GPU, ни токена, ни ffmpeg — на ноутбуке достаточно
минимальной установки:

    py -3.12 -m venv .venv
    .venv\Scripts\pip install pyaudiowpatch
    .venv\Scripts\pip install -e .
    .venv\Scripts\meet record

Потом перенести папку `recordings/<дата_время>/` на машину с GPU и там
запустить `meet transcribe recordings/<папка>`. Файлы `sys.opus` и `mic.opus`
внутри не переименовывать. Запись сжата в Ogg/Opus 16 кГц моно — порядка
25 МБ на час встречи. Звук встречи должен играть через устройство вывода ноутбука
(наушники/динамики) — loopback пишет именно его.

Подробности: docs/superpowers/specs/2026-06-12-transcriber-design.md
