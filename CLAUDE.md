# meet — локальная запись и расшифровка встреч

Записывает встречу двумя дорожками (системный звук через WASAPI loopback и
микрофон) и расшифровывает её локально: Whisper (faster-whisper), разделение на
спикеров (pyannote), узнавание голосов из базы. Итоги, вопросы по встрече и
выгрузка в базу знаний — через подключённую модель (Claude Code, Codex или
OpenAI-совместимый сервер). Платформа — Windows 10/11 x64.

## Устройство

- `src/meet/` — движок на Python: запись (`recorder.py`), расшифровка
  (`transcribe.py`, `asr.py`, `diarize.py`), библиотека записей (`library.py`),
  резидент с треем и автозаписью (`tray.py`, `tray_control.py`, `watch.py`),
  локальный control API (`control.py`), настройки (`settings.py`), CLI
  (`cli.py`, `cli_library.py`).
- `app/` — окно приложения: React + TypeScript (Vite, тесты — Vitest).
- `app/src-tauri/` — оболочка на Tauri 2 (Rust): трей, уведомления, запуск
  резидента, установка движка, обновления, установщик NSIS.
- `tests/` — тесты Python; `scripts/` — сборка выпуска и вспомогательные
  утилиты; `docs/` — планы и заметки по разработке.

Данные установленного приложения — `%LOCALAPPDATA%\meet` (настройки
`config.json`, записи, база голосов, журналы). При запуске из исходников
записи, `voices/`, `hotwords.txt` и `glossary.txt` лежат в корне репозитория и в
git не попадают.

## Тесты

Python (из корня, venv с установленным пакетом):

    PYTHONPATH=src PYTHONUTF8=1 .venv/Scripts/python -m pytest -q

Окно (из `app/`):

    npm install
    npm run check && npm test && npm run build

Оболочка (из `app/src-tauri/`):

    cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check

## Запуск для разработки

    py -3.12 -m venv .venv
    .venv/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cu128
    .venv/Scripts/pip install -e .[engine-cuda]   # без NVIDIA: только .[engine-cpu]
    cd app && npm install && npm run app

`npm run app` — это `tauri dev`: окно с горячей перезагрузкой; оболочка сама
находит резидента из `.venv` репозитория. Для разделения на спикеров нужен
токен Hugging Face с принятыми условиями модели
`pyannote/speaker-diarization-community-1`: его задают в окне приложения
(хранится в диспетчере учётных данных Windows) или переменной `HF_TOKEN`.

## Типовые просьбы пользователя

Команды — `.venv/Scripts/meet …` (или `meet`, если пакет установлен). Вывод —
в stdout (UTF-8), ход работы и ошибки — в stderr; `--json` — машиночитаемый
ответ. Расшифровка идёт минуты — запускать в фоне.

- **Начать запись**: `meet record` (папка записи появится в выводе). Если
  работает приложение, запись можно начать и в нём.
- **Что сейчас происходит**: `meet status` — идёт ли запись, куда, вручную или
  автоматически.
- **Остановить запись**: запись с ассистентом — только `meet live-stop`
  (процесс не убивать: потеряется хвост). Свою фоновую `meet record` можно
  просто завершить — Ogg/Opus финализируется сам. Запись, которую ведёт
  резидент, останавливают в приложении.
- **Расшифровать**: `meet transcribe <папка записи или файл>`
  (`--speakers N`, `--hotwords "термин, термин"`); чужой файл — `meet import
  <файл>`. Готовый текст — `meet export <запись> --format md|txt|srt`.
- **Итоги и вопросы**: `meet summary <запись>` (→ `summary.md`),
  `meet ask <запись> "вопрос"` (→ `qa.jsonl`); `--provider` выбирает модель.
- **В базу знаний**: `meet kb-export <запись>` (синоним — `meet notes`) —
  папка по шаблону `export.folder_template` внутри `export.meetings_dir`.
- **Голоса**: `meet enroll <папка> "Спикер 1=Имя"`, `meet voices list|rename|merge|delete`.
- **Найти встречу**: поиск в окне приложения или `GET /search?q=…` control API
  резидента (адрес и токен — в `%LOCALAPPDATA%\meet\daemon.json`, заголовок
  `Authorization: Bearer <токен>`); без резидента — поиск по `*_transcript.md`
  в папке записей.

## Соглашения

- Тексты интерфейса, сообщения об ошибках, комментарии и docstring — на русском.
- Никаких секретов в журналах и выводе: токены (HF, API) не печатать и не
  писать в логи, `config.json` и отчёты диагностики.
- PortAudio (PyAudio) — только в процессе записи или в подпроцессе
  (`python -m meet.devices_probe`). Второй PyAudio-инстанс в резиденте рушит
  идущую запись.
- `config.json` версионирован: старый формат мигрируется при чтении, у
  существующего пользователя поведение не меняется (`settings.migrate`).
- Удаление записей — «все или ни одной» (`library.remove_folders`); папку,
  которую держит другая программа, не удалять по частям.
- Новое поведение — сначала тест; все три набора тестов должны быть зелёными.
