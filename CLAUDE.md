# meet — локальная запись и расшифровка встреч

Записывает встречу двумя дорожками (системный звук через WASAPI loopback и
микрофон) и расшифровывает её локально: на процессоре по умолчанию GigaAM
(`gigaam_asr.py`), на видеокарте и для не русской речи — Whisper
(faster-whisper); разделение на спикеров (pyannote), узнавание голосов из базы.
Анализ встречи, итоги, названия, «Улучшить расшифровку» и живой ассистент —
через подключённую модель (Claude Code, Codex или OpenAI-совместимый сервер).
Платформа — Windows 10/11 x64, macOS — экспериментально.

## Устройство

- `src/meet/` — движок на Python: запись (`recorder.py`), расшифровка
  (`transcribe.py`, `asr.py`, `gigaam_asr.py`, `diarize.py`), библиотека
  записей (`library.py`), резидент с треем и автозаписью (`tray.py`,
  `tray_control.py`, `watch.py`), фоновые задачи (`jobs.py`, `job_worker.py`),
  локальный control API (`control.py`), настройки (`settings.py`), CLI
  (`cli.py`, `cli_library.py`).
- Работа с моделью: анализ встречи (`analysis.py`), названия (`titles.py`),
  категории (`categories.py`), улучшение расшифровки (`improve.py`); ручные
  исправления и правила замены — `textfix.py`, `replacements.py`. Живой ассистент —
  `src/meet/assist/` (см. «Ассистент-участник» ниже; сводка — `digester.py`,
  прежние подсказки и вопросы — `digester.py`, `qa.py`, промпты — `prompts.py`),
  живое распознавание — `live_asr.py`.
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

## Ассистент-участник (0.3.6)

Во время записи с ассистентом дочерний `meet assist` (`assist/app.py`) ведёт
одного агента-участника на встречу (`assist/participant.py`). Весь «интеллект»
— у агента; Meet только доставляет и показывает, без порогов и эвристик.

- **Вход агенту:** отрезки расшифровки в паузах (не реже ~25 с при речи;
  реплики владельца — «Вы (вслух)»), сообщения пользователя (вне очереди:
  прерывают ход по расшифровке), нажатые кнопки — текстом, реакции 👍/👎/❓,
  вложения. Системный промпт и затравка — `assist/participant_prompts.py`.
- **Ответ агента** — JSON-строки: `{"say", "buttons" (0–3), "pin"}`,
  `{"silent": true}`; `read`/`search`/`list` — только запасной путь локальной
  модели без инструментов (`kb_prep.kb_read/kb_search/kb_list`). Остальные
  читают базу знаний и библиотеку встреч сами (Claude Code — Read/Grep/Glob в
  режиме собеседника, Codex — read-only песочница). Страховка Meet одна: не
  больше одного нового сообщения агента за 15 с (`MERGE_WINDOW_S`) — только
  для ходов по расшифровке; лишнее дописывается к предыдущему, ответы на
  сообщение, кнопку или реакцию пользователя проходят всегда.
- **Одна сессия на встречу, нативное продолжение:** id сеанса провайдера —
  в `<запись>/assistant/sessions.json` (Claude Code `--resume`, Codex `exec
  resume`, OpenCode `--session`); не продолжился (`resume_failed`) или
  локальная модель — затравка из журнала (`ChatLog.context`).
- **Журнал** — `<запись>/assistant/chat.jsonl` (`assist/chatlog.py`: записи
  и патчи, `seq`, строгий замок, идемпотентный `client_id`); вложения —
  `assistant/files/` (картинки, `assist/attachments.py`) и
  `assistant/materials/` (разбор документов, `meet/materials.py`).
  `assistant_chat.md` в папке записи — журнал для вкладки «Агент».
  `merge.merge_chats` склеивает журналы частей.
- **База знаний:** карта без содержимого (`kb_prep.kb_map`; папка группы
  `groups.kb_folder` — целиком, плюс прошлые встречи группы) — в начале
  сессии. `assist.kb_map=false` убирает карту целиком: и структуру базы, и
  список прошлых встреч группы. Читать содержимое — только по просьбе или с
  согласия пользователя: это правило промпта, не барьер (доступ на чтение к
  базе и библиотеке у агента есть через `add_dirs`). `assist.kb_exclude` — у
  Claude Code deny-правила сеанса, у остальных — просьба в промпте
  (`deny_enforced`).
- **Изображения** уходят только моделям из `llm.VISION_PROVIDERS` (Claude
  Code, Codex); остальным — пометка, текст документов уходит всем.
- **API:** маршруты ребёнка `/chat*`, `PUT /agent/frequency`, SSE
  `chat_snapshot`/`chat`/`chat_partial`/`agent` (`assist/web.py`); резидент
  проксирует их как `/live/chat*` (`live_control.py`, `tray_control.py`).
  Ребёнок принимает только `Host` 127.0.0.1/localhost своего порта, записи —
  без Origin или со своего; путь к файлу (`/chat/attach`) — только с токеном
  резидента (`X-Meet-Token`, env `MEET_ASSIST_TOKEN`). `GET /` ребёнка —
  страница чата для `meet assist` из консоли (`web.CHAT_PAGE`, без токена:
  картинки — байтами через `/chat/paste`).
- **После встречи** — «Продолжить разговор»: `POST /recordings/{id}/chat` →
  `jobs.CHAT` → `job_worker._chat` (тот же `Participant` с resume, до 6 ходов —
  цепочки запросов локальной модели),
  событие `chat.updated`.
- **Окно:** `app/src/live/` (`useChat`, `LiveChat`, `ChatComposer`,
  `SessionBar`, `ChatWorkspace`); вкладка «Ассистент» карточки — после встречи.
- **Настройки:** `assist.participant` (по умолчанию вкл.; в файл не пишется,
  пока равен умолчанию), `assist.frequency` (`less`/`normal`/`more`, по
  умолчанию `more`), `assist.kb_map`, `assist.kb_exclude`. `activity:
  summary` («Только сводка») выключает и участника (`participant_on`).
- **Прежний режим** (`assist.participant=false`): подсказки и «Спросить»
  (`digester.py`, `qa.py`, `LiveHints`/`LiveAsk`/`LiveSummary`, `web.PAGE`)
  — запасной в 0.3.6, удаление запланировано на 0.3.7. Пока он есть, оба
  пути должны оставаться рабочими.

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
- **Что писал ассистент на встрече**: `<запись>/assistant_chat.md` (если
  открывали вкладку «Агент»), иначе журнал `<запись>/assistant/chat.jsonl`
  (строки JSON: записи и патчи по `id`).
- **Анализ встречи**: `meet analyze <запись>` (→ `analysis.json`: типы и
  важность реплик, главы, наблюдения, категория).
- **Исправить слово**: `meet fix <запись> "как распознано" "как правильно"`
  (`--all` — во всей встрече, `--hotword`, `--rule`); найти ошибки моделью —
  `meet improve <запись>` (`--apply` — применить).
- **Название и категория**: `meet title <запись>` (`--apply`), `meet category
  <запись> ["Категория" | --clear]`.
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
