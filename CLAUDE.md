# meet — локальная запись и расшифровка встреч

Записывает встречу двумя дорожками (системный звук через WASAPI loopback и
микрофон) и расшифровывает её локально: на процессоре по умолчанию GigaAM
(`gigaam_asr.py`), на видеокарте и для не русской речи — Whisper
(faster-whisper); разделение на спикеров (pyannote), узнавание голосов из базы.
Анализ встречи, итоги, названия, «Улучшить расшифровку», профили людей и живой
ассистент — через подключённую модель (Claude Code, Codex или
OpenAI-совместимый сервер). Платформа — Windows 10/11 x64, macOS — экспериментально.

## Устройство

- `src/meet/` — движок на Python: запись (`recorder.py`), расшифровка
  (`transcribe.py`, `asr.py`, `gigaam_asr.py`, `diarize.py`), библиотека
  записей (`library.py`), резидент с треем и автозаписью (`tray.py`,
  `tray_control.py`, `watch.py`), фоновые задачи (`jobs.py`, `job_worker.py`),
  локальный control API (`control.py`), настройки (`settings.py`), CLI
  (`cli.py`, `cli_library.py`).
- Работа с моделью: анализ встречи (`analysis.py`), названия (`titles.py`),
  категории (`categories.py`), улучшение расшифровки (`improve.py`), профили
  людей (`profiles.py`, `profile_safety.py`, `pcm.py`); ручные исправления и
  правила замены — `textfix.py`, `replacements.py`. Живой ассистент —
  `src/meet/assist/` (сводка и подсказки — `digester.py`, вопросы — `qa.py`,
  промпты — `prompts.py`), живое распознавание — `live_asr.py`.
- `app/` — окно приложения: React + TypeScript (Vite, тесты — Vitest).
- `app/src-tauri/` — оболочка на Tauri 2 (Rust): трей, уведомления, запуск
  резидента, установка движка, обновления, установщик NSIS.
- `tests/` — тесты Python; `scripts/` — сборка выпуска и вспомогательные
  утилиты; `docs/` — планы и заметки по разработке.
- macOS (экспериментально, Apple Silicon, не проверено на реальном Mac):
  различия ОС — `meet/plat.py` и `app/src-tauri/src/platform.rs`; звук —
  `meet/mac_audio.py` (sounddevice) и помощник `mac/audiotap/main.swift`
  (ScreenCaptureKit, `--mic-users`), протокол — `meet/audiotap.py`; сборка —
  `scripts/build_release_macos.sh`, job `macos` в `release.yml` (пробный
  прогон — workflow_dispatch с `macos_only`). Модули Windows (winreg,
  ctypes.windll, pyaudiowpatch) импортировать только на Windows.

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
    .venv/Scripts/pip install -e .[engine-cuda,gigaam]   # без NVIDIA: .[engine-cpu,gigaam]
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
- **Анализ встречи**: `meet analyze <запись>` (→ `analysis.json`: типы и
  важность реплик, главы, наблюдения, категория).
- **Исправить слово**: `meet fix <запись> "как распознано" "как правильно"`
  (`--all` — во всей встрече, `--hotword`, `--rule`); найти ошибки моделью —
  `meet improve <запись>` (`--apply` — применить).
- **Название и категория**: `meet title <запись>` (`--apply`), `meet category
  <запись> ["Категория" | --clear]`.
- **Профиль человека**: `meet profile "Имя"` (`--refresh`); работает, только
  если профили включены в настройках.
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
  Осознанное исключение 0.3.0 — переход CPU с Whisper medium по умолчанию на
  GigaAM (`_legacy_cpu_backend`). Новый автоматический вызов модели у
  обновившегося пользователя включается только с его согласия (анализ
  встречи — однократное предложение в карточке).
- Текст встречи в промптах — данные, а не команды: реплики, имена и пункты
  сводки ограждаются разделителями и экранируются (`analysis._safe`,
  `assist/prompts.py`). В журналы — только счётчики и задержки, без текста
  встреч, вопросов и ответов.
- Тесты не ходят в сеть и не запускают claude/codex (страж в
  `tests/conftest.py`).
- Удаление записей — «все или ни одной» (`library.remove_folders`); папку,
  которую держит другая программа, не удалять по частям.
- Новое поведение — сначала тест; все три набора тестов должны быть зелёными.
