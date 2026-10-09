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
  `tray_control.py`, `watch.py`), фоновые задачи (`jobs.py`, `job_worker.py`;
  тяжёлые задачи подряд — в одном процессе `job_worker --serve`, 0.5.1),
  локальный control API (`control.py`), настройки (`settings.py`), CLI
  (`cli.py`, `cli_library.py`).
- Работа с моделью: анализ встречи (`analysis.py`), названия (`titles.py`),
  категории (`categories.py`), улучшение расшифровки (`improve.py`); ручные
  исправления и правила замены — `textfix.py`, `replacements.py`. Живой ассистент —
  `src/meet/assist/` (см. «Ассистент-участник» ниже; сводка — `digester.py`,
  прежние подсказки и вопросы — `digester.py`, `qa.py`, промпты — `prompts.py`),
  живое распознавание — `live_asr.py`.
- Системный звук без звука самого Meet (0.5): Windows 10 2004+ —
  `process_loopback.py` (WASAPI process loopback, исключает дерево процессов
  оболочки; pid — `MEET_EXCLUDE_PID` от `meet-tray --headless --parent-pid`;
  сбой — прежний loopback), macOS — помощник `--exclude-pid`;
  `MEET_PROCESS_LOOPBACK=0` выключает. Копия настроек перед откатом версии —
  `backup.py` (`POST /backup`, «О программе» → «Другие версии»).
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

## Оформление (0.4, Atlas Aurora)

Спек: `docs/superpowers/specs/2026-10-08-0.4-atlas-aurora-design.md`.

- **Токены и компоненты дизайн-системы** — `app/src/theme/aurora/` (версия
  артефакта — в `SOURCE.md`). Файлы генерирует только `scripts/vendor_aurora.py`,
  руками их не править; отличия Meet — поверх, в `theme/aurora-fixes.css`.
  `theme/aurora-fallbacks.css` — запасные значения для WebKit без `color-mix()`
  (macOS 13).
- **Тема, палитра, сияние** — ключи `ui.theme` (`system`/`dark`/`light`),
  `ui.aurora` (5 палитр), `ui.aurora_style` (`glow`/`waves`), `ui.motion`;
  Python — `settings.Ui` (обновившийся без `ui.theme` получает `dark`). Окно:
  `theme/appearance.ts` + `theme/useAppearance.ts` (атрибуты `data-theme`,
  `data-aurora` на `<html>`), до React — `app/public/appearance-boot.js`;
  оболочка (тема окна, фон, рассылка окнам) — `src-tauri/src/appearance.rs`.
- **Стражи CSS** (`app/src/theme/*.test.ts`, `app/src/lib/webkit.test.ts`): корневой
  класс блока — ровно в одном файле (`cssClasses`); нет литералов цвета
  (`noColors`) и белого (`noWhite`) вне `theme/aurora/`; одна рамка фокуса
  (`focus`); каждая `var(--…)` объявлена (`tokens`); переименованные классы не
  возвращаются (`renamed`); удалённые имена переменных окна (`--bg`, `--text`,
  `--line`…) не используются и не объявляются (`noLegacyVars`); `color-mix()` вне файлов дизайн-системы и regex с
  lookbehind в коде окна запрещены (`webkit`). Исключения — списком с причиной;
  единственное у стража цветов — `theme/scrollbars.css` (ползунки прокрутки:
  токена с альфой нет, `color-mix()` в WebKit macOS 13 тоже).
- **Цвет**: только `var(--…)`; текст цвета акцента — `--accent-line` (не
  `--accent`: в светлой теме контраст ниже 4,5:1), на заливке акцента — `--on-accent`.
- **Условия использования** — разовая заслонка главного окна (`features/legal`,
  `TermsGate`; принято — `ui.terms_accepted` = версия текста из `lib/terms.ts`);
  панель трея и живая панель без принятия не начинают запись
  (`useTermsAccepted`), автозапись и меню значка трея не блокируются.

## Ассистент-участник (0.3.6; 0.4 — «как CLI»)

Во время записи с ассистентом дочерний `meet assist` (`assist/app.py`) ведёт
одного агента-участника на встречу (`assist/participant.py`). Весь «интеллект»
— у агента; Meet только доставляет и показывает, без порогов и эвристик.

- **Вход агенту:** отрезки расшифровки в паузах (не реже ~25 с при речи;
  реплики владельца — «Вы (вслух)»), сообщения пользователя (вне очереди:
  прерывают ход по расшифровке), нажатые кнопки — текстом, реакции 👍 «Полезно» /
  👎 «Не по теме» / ❓ «Поясни» (👎 — «мимо темы», частоту не меняет; ответ на ❓
  несёт `explains` = id поясняемого сообщения), вложения. Системный промпт и затравка — `assist/participant_prompts.py`.
- **Уровень хода и автомод (0.4):** Meet ставит уровень (`consent.py`): `NONE` —
  только реплики, 👍/👎, повтор после сбоя (чтение рабочих папок и вложений, что
  бы ни прозвучало); `READ` — ❓ (чтение без карточек); `USER` — сообщение,
  кнопка агента (кроме отказа), слэш-команда: Claude Code в автомоде
  (`--permission-mode auto`, хук PreToolUse отвечает `{}`), карточка Meet — только
  удаление, запись вне рабочих папок, отправка наружу, MCP-изменения.
  `assist.agent_mode`: `auto` (по умолчанию, в файл не пишется) | `confirm`
  (карточка на каждое действие); однократная строка об этом в первой сессии
  (`assist.agent_mode_noticed`). Codex/OpenCode в `USER` правят рабочие папки и
  выполняют команды сами; MCP запрещён; удаление и отправку у Codex удерживает
  только промпт и его `--approve-for-me`, у OpenCode — правила разрешений.
- **«Личный» — только промпт:** папки, MCP и ворота те же, что в «Рабочей встрече».
- **Ход работы в чате:** каждый вызов инструмента — запись `kind: "tool"`,
  `event: "call"` (`assist/tool_rows.py`, патчи по `tool_use_id`: вид, суть,
  состояние, вывод до 64 КБ, решение ворот, `reply`); Claude Code — по ходу
  (`send(on_event=…)`), Codex/OpenCode — после вызова (`AgentReply.tools`).
  Карточка согласия с тем же `tool_use_id` — в строке. В журнал процесса — ничего
  из вызова. Окно — `live/ToolRows.tsx`.
- **Инструменты Meet (0.5):** свой MCP-сервер `meet` для Claude Code
  (`assist/meet_mcp.py`, проверки — `assist/meet_tools.py`): открыть файл,
  показать в папке, открыть ссылку — сразу в ходе `USER`; запустить приложение —
  карточкой; `meet_settings` — настройки без секретов. Согласие «до конца
  встречи» — действие + объект (`consent.grant_for`), удаление и отправка
  наружу не запоминаются.
- **Кнопки голосом (0.5):** своя реплика почти дословно совпала с надписью
  кнопки под последним сообщением (30 с) — «Засчитано голосом» с «Отменить» 10 с,
  потом обычное нажатие (`participant.voice_match`, `assist.voice_buttons`).
- **Слэш-команды** (`assist/slash.py`, из `post_user_message` и `queue_existing`,
  `via: "command"`): Meet — `/help`, `/mcp [reconnect|enable|disable имя]`,
  `/clear` (новый сеанс, затравка без переписки), `/model [имя]`, `/compact`;
  команды CLI и навыки пользователя — дословно отдельным ходом-командой
  (`USER`); ответы — строки `card: "command"`. Окно — список на «/» и дополнение
  аргументов (`live/slash.ts`; `view` → `commands`, `mcp_servers`, `models`).
- **Участники:** узнанные люди с ролью из «Голосов» (`people.roles`) — блок
  «Участники встречи» в ограде данных в затравке и новой строкой хода (оба
  профиля; после встречи — спикеры записи); в лог — только счётчик.
- **Ответ агента** — JSON-строки: `{"say", "buttons" (0–3), "pin"}`,
  `{"silent": true}`; `read`/`search`/`list` — только запасной путь локальной
  модели без инструментов (`kb_prep.kb_read/kb_search/kb_list`). Остальные
  работают своими инструментами по уровню хода (ниже). Страховка Meet одна: не
  больше одного нового сообщения агента за 15 с (`MERGE_WINDOW_S`) — только
  для ходов по расшифровке; лишнее дописывается к предыдущему, ответы на
  сообщение, кнопку или реакцию пользователя проходят всегда. Склейка решается
  в начале хода (0.5): реплика `writing` несёт `merge_into`, окно пишет её
  текст продолжением под тем сообщением (`chatModel.feedItems`, `more`).
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
- **Временная встреча и «Остановить без сохранения»** (0.3.7) —
  `meet/temp_meeting.py`: временная встреча (`POST /live/start
  {"temporary": true}`) пишется в `<data_dir>/tmp-meetings/<сеанс>/`, вне
  библиотеки; агент её — `Participant(ephemeral=True)` (сеанс провайдера не
  сохраняется). Любой её «Стоп» удаляет папку сеанса и забывает сеансы агента
  (`llm.forget_session` по `assistant/sessions.json`, включая `past`; Codex —
  `codex delete --force`), без `_on_saved` и хука. `POST /recording/keep` —
  «Сохранить как обычную встречу»: отметка `keep`, после «Стоп» перенос в
  библиотеку в фоне (никогда внутрь существующей папки). `/recording/cancel`
  удаляет так же (`temp_meeting.wipe` + `claude.forget_project`); занятый
  остаток — `.deleting-` или отметка `library.DISCARDED_MARK`. Ссылки
  (symlink, junction) не проходить. Остатки после сбоя — `sweep` при старте
  резидента. Тексты окна — `app/src/lib/recordingStop.ts`.
- **Окно:** `app/src/live/` (`useChat`, `LiveChat`, `ChatComposer`,
  `SessionBar`, `ChatWorkspace`); вкладка «Ассистент» карточки — после встречи.
- **Настройки:** `assist.participant` (по умолчанию вкл.; в файл не пишется,
  пока равен умолчанию), `assist.frequency` (`less`/`normal`/`more`, по
  умолчанию `more`), `assist.kb_map`, `assist.kb_exclude`. `activity:
  summary` («Только сводка») выключает и участника (`participant_on`).
- **Профиль сессии (0.3.7):** `assist.profile` — `work` («Рабочая встреча»)
  или `personal` («Личный»: свой промпт без карты базы, контекста задачи и
  рабочей рамки; база и прошлые встречи — только по просьбе; доступ — как у
  `work`, 0.4). Прежнее `neutral` читается как `personal`. Выбирается при старте
  (`/live/start`, `/live/attach` `{"profile"}` → `meet assist --profile`),
  меняется по ходу (`PUT /live/profile` → ребёнку `/agent/profile`, пометка
  агенту и пересоздание сеанса: Claude Code — с продолжением, Codex и
  OpenCode — новый с затравкой), хранится в `assistant/sessions.json`
  (`ChatLog.profile`).
- **Прежний режим** (`assist.participant=false`): подсказки и «Спросить»
  (`digester.py`, `qa.py`, `LiveHints`/`LiveAsk`/`LiveSummary`, `web.PAGE`)
  — запасной в 0.3.6, удаление запланировано на 0.3.7. Пока он есть, оба
  пути должны оставаться рабочими.

## Тесты

Python (из корня, venv с установленным пакетом):

    PYTHONPATH=src PYTHONUTF8=1 .venv/Scripts/python -m pytest -q -n auto   # параллельно; без xdist — без -n

Окно (из `app/`):

    npm install
    npm run check && npm test && npm run build

Оболочка (из `app/src-tauri/`):

    cargo test && cargo clippy --all-targets -- -D warnings && cargo fmt --check

«Прокликивание» окна (0.5; резидент с временной папкой данных, Vite и
системный Edge — снимки всех экранов в двух темах, ошибки консоли и ответы
резидента в `.superpowers/clickthrough/<время>/report.md`, код 1 — есть
замечания; штатные «нет данных» — списком `EXPECTED` в
`app/scripts/clickthrough.mjs`):

    .venv/Scripts/python scripts/clickthrough.py

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
- **Голоса**: `meet enroll <папка> "Спикер 1=Имя"`, `meet voices list|rename|merge|delete`;
  «Кто это» (роль человека для ассистента) — `meet voices role <имя> "роль"`.
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
