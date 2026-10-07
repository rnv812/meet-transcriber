/** Обёртки над командами оболочки Tauri; в браузере (dev) — мягкие запасные пути. */

export const inTauri = (): boolean =>
  typeof window !== "undefined" &&
  (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== undefined;

export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: call } = await import("@tauri-apps/api/core");
  return call<T>(cmd, args);
}

export async function openFolder(path: string): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_folder", { path });
}

/** Сохранить текст файлом. Возвращает путь, null — отмена. */
export async function saveText(name: string, content: string): Promise<string | null> {
  if (inTauri()) return invoke<string | null>("save_text", { defaultName: name, content });
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.style.display = "none";
  // Firefox качает только по ссылке из документа; URL отзываем после того,
  // как браузер начал загрузку, — синхронный revoke её обрывает.
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return name;
}

export async function pickMedia(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string | null>("pick_media");
}

/** «📎» строки ввода чата ассистента: файлы для вложения (оболочка пропускает только документы и картинки). */
export async function pickChatFiles(): Promise<string[]> {
  if (!inTauri()) return [];
  return invoke<string[]>("pick_chat_files");
}

/**
 * Чип-источник в чате ассистента: открыть документ или картинку приложением по умолчанию.
 * Оболочка пускает только файлы внутри базы знаний и библиотеки встреч, не исполняемые
 * (`open_material`); иначе — ошибка. В браузере (dev) — ничего.
 */
export async function openMaterial(path: string): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_material", { path });
}

/** Перетаскивание файлов на окно (Tauri: HTML5-перетаскивание WebView2 перехватывает сам). */
export type FileDrop =
  | { type: "enter" | "over"; x: number; y: number }
  | { type: "drop"; x: number; y: number; paths: string[] }
  | { type: "leave" };

/**
 * Подписка на перетаскивание файлов на это окно; `x`, `y` — точка в CSS-пикселях
 * окна (оболочка даёт физические). Вне приложения — ничего. → отписка.
 */
export async function onFileDrop(cb: (e: FileDrop) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  return getCurrentWebview().onDragDropEvent((e) => {
    const p = e.payload as { type: string; paths?: string[]; position?: { x: number; y: number } };
    const scale = window.devicePixelRatio || 1;
    const x = (p.position?.x ?? 0) / scale;
    const y = (p.position?.y ?? 0) / scale;
    if (p.type === "enter" || p.type === "over") cb({ type: p.type, x, y });
    else if (p.type === "drop") cb({ type: "drop", x, y, paths: p.paths ?? [] });
    else if (p.type === "leave") cb({ type: "leave" });
  });
}

/** Точка (x, y) окна — над зоной вложений чата (элемент с `data-chat-drop`). */
export function overChatDrop(x: number, y: number): boolean {
  const el = typeof document.elementFromPoint === "function" ? document.elementFromPoint(x, y) : null;
  return !!el?.closest("[data-chat-drop]");
}

/**
 * Диалог выбора папки (`start` — откуда начать). null — отказ. В браузере (dev)
 * диалога нет — путь вводится руками.
 */
export async function pickFolder(start?: string | null): Promise<string | null> {
  if (!inTauri()) return window.prompt("Путь к папке", start ?? "")?.trim() || null;
  return invoke<string | null>("pick_folder", { start: start ?? null });
}

/**
 * Крестик главного окна при несохранённых настройках: решает оболочка
 * (close_guard.rs) — без несохранённого окно закрывается само, со страницей
 * не советуясь. Иначе приходит это событие: страница подтверждает вопрос
 * (`settingsCloseAck`, иначе через 2 с окно закроется само) и потом отвечает
 * `settingsCloseGo` (закрыть) или `settingsCloseStay` (остаться).
 */
export async function onSettingsCloseGuard(cb: () => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("settings-close-guard", () => cb());
}

export async function settingsCloseAck(): Promise<void> {
  if (inTauri()) await invoke<void>("settings_close_ack").catch(() => {});
}

export async function settingsCloseStay(): Promise<void> {
  if (inTauri()) await invoke<void>("settings_close_stay").catch(() => {});
}

export async function settingsCloseGo(): Promise<void> {
  if (inTauri()) await invoke<void>("settings_close_go").catch(() => {});
}

/** Есть ли несохранённое в настройках: «Выход» из трея тогда спрашивает. */
export async function setSettingsDirty(dirty: boolean): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("set_settings_dirty", { dirty }).catch(() => {});
}

/** Оболочка просит открыть запись (клик по уведомлению). */
export async function onOpenRecording(cb: (id: string) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("open-recording", (e) => cb(e.payload));
}

/** Оболочка сообщает новый вид панели ассистента (`live-window`, `LiveView`). */
export async function onLiveWindow(cb: (view: unknown) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<unknown>("live-window", (e) => cb(e.payload));
}

export function initialRecording(): string | null {
  return new URLSearchParams(window.location.search).get("recording");
}

/** Оболочка просит открыть раздел настроек (`assistant` — «Ассистент»). */
export async function onOpenSection(cb: (section: string) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("open-section", (e) => cb(e.payload));
}

/** Раздел настроек из адреса окна (`?section=assistant`). */
export function initialSection(): string | null {
  return new URLSearchParams(window.location.search).get("section");
}

// --- мастер первого запуска: движок, ссылки, автозапуск ---------------------

/** `engine_status` оболочки: движок расшифровки и место под него. */
export type EngineStatus = {
  installed: boolean;
  version: string;
  env_dir: string;
  profile: "cuda" | "cpu" | "mac" | null;
  gpu: string | null;
  /** Свободно на диске с данными, ГБ; null — узнать нельзя (не блокируем). */
  free_gb: number | null;
  /** Место под профиль по видеокарте; меньше, когда пакеты уже в кэше uv. */
  needs_gb: number;
  /** То же для CPU-версии; старая оболочка не присылает. */
  needs_cpu_gb?: number;
  /** Установка идёт прямо сейчас (начата в закрытом окне или фоновое обновление). */
  installing?: boolean;
};
/** Событие `engine-progress`: первая строка шага — его название. */
export type EngineProgress = { step: number; of: number; line: string };
/** Событие `engine-failed`: хвост лога упавшего шага (до 30 строк). */
export type EngineFailed = { step: number; tail: string };

/** Состояние движка (до 5 с: nvidia-smi). Вне приложения — null: оболочки нет. */
export async function engineStatus(): Promise<EngineStatus | null> {
  if (!inTauri()) return null;
  return invoke<EngineStatus>("engine_status");
}

/** Поставить движок (`fresh` — удалить окружение и поставить заново). Минуты. */
/** Повторить необязательную установку GigaAM в установленный движок (только в приложении). */
export async function retryGigaamInstall(): Promise<void> {
  await invoke<void>("retry_gigaam_install");
}

export async function installEngine(profile: "cuda" | "cpu" | "mac", fresh: boolean): Promise<void> {
  await invoke<void>(fresh ? "reinstall_engine" : "install_engine", { profile });
}

async function listenShell<T>(event: string, cb: (payload: T) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, (e) => cb(e.payload));
}

export const onEngineProgress = (cb: (p: EngineProgress) => void) => listenShell("engine-progress", cb);
export const onEngineFailed = (cb: (f: EngineFailed) => void) => listenShell("engine-failed", cb);

// --- где хранить движок и модели (storage.rs) --------------------------------

/** `storage_status`: где сейчас движок и модели, идёт ли перенос. */
export type StorageStatus = {
  /** Выбранная папка; null — по умолчанию. */
  root: string | null;
  home: string;
  /** Папка по умолчанию на системном диске (папка данных Meet). */
  default_home: string;
  /** Выбранной папки нет (внешний диск отключён). */
  missing: string | null;
  /** Файл выбора папки не прочитан (повреждён). */
  unreadable?: boolean;
  moving: boolean;
  /** «Отменить» ещё работает (до переключения на новую папку). */
  cancellable: boolean;
  /** Перенос в эту папку прерван: «Продолжить» / «Отменить». */
  interrupted?: string | null;
  /** Недоделанное ещё удаляется (файлы были заняты). */
  discarding?: boolean;
};
/** `storage_check`: куда на самом деле, сколько места и можно ли сейчас. */
export type StorageCheck = {
  target: string;
  free_gb: number | null;
  needs_gb: number;
  engine_gb: number;
  models_gb: number;
  busy: string | null;
  error: string | null;
  /** Продолжение прерванного переноса в ту же папку. */
  resume?: boolean;
};
export type StoragePhase = "engine" | "models" | "waiting" | "switching" | "cleanup" | "rollback";
/** Событие `storage-progress`: шаг переноса, текст и байты (у копирования моделей). */
export type StorageProgress = { phase: StoragePhase; text: string; done: number; total: number };

export async function storageStatus(): Promise<StorageStatus | null> {
  if (!inTauri()) return null;
  return invoke<StorageStatus>("storage_status");
}
export const storageCheck = (target: string) => invoke<StorageCheck>("storage_check", { target });
/** Перенести движок и модели (минуты). Ответ — папка, куда перенесено. */
export const storageMove = (target: string) => invoke<string>("storage_move", { target });
export const storageCancel = () => invoke<void>("storage_cancel");
/** «Отменить» прерванный перенос: новая папка очищается. */
export const storageAbandon = () => invoke<void>("storage_abandon");
/** Папки нет или выбор не прочитан: указать папку, куда переносили движок. */
export const storageRepoint = (folder: string) => invoke<void>("storage_repoint", { folder });
/** Выбранной папки нет: вернуть движок и модели на системный диск. */
export const storageReset = () => invoke<void>("storage_reset");
/** Диск подключили: проверить папку и запустить службу снова. */
export const storageRetry = () => invoke<void>("storage_retry");
export const onStorageProgress = (cb: (p: StorageProgress) => void) => listenShell("storage-progress", cb);

// --- обновление по кнопке («О программе») ------------------------------------

/** `check_update` оболочки: последний выпуск на GitHub против своей версии. */
export type UpdateCheck = {
  current: string;
  /** null — выпусков ещё нет. */
  latest: string | null;
  newer: boolean;
  notes_url: string | null;
  /** Установщик в выпуске (с контрольной суммой); null — скачать нечего. */
  asset_name: string | null;
  size: number | null;
};
/** Событие `update-progress`: байты скачанного установщика (`total` 0 — неизвестно). */
export type UpdateProgress = { done: number; total: number };

export const NOT_IN_APP = "Обновление работает только в приложении";

/** Проверить обновления (до 10 с). Ошибка — текст для человека. */
export async function checkUpdate(): Promise<UpdateCheck> {
  if (!inTauri()) throw new Error(NOT_IN_APP);
  return invoke<UpdateCheck>("check_update");
}

/**
 * Чем закончилась установка (`updater::Outcome`): `installer` — установщик
 * Windows запущен; `in-place` — macOS заменит Meet.app на месте и запустит
 * новую версию; `in-place-admin` — то же, но macOS сначала спросит пароль
 * администратора; `manual` — macOS открыла образ в Finder, Meet переносят в
 * «Программы» вручную (`reason` — почему не на месте).
 */
export type InstallOutcome = "installer" | "in-place" | "in-place-admin" | "manual";
export type InstallResult = { outcome: InstallOutcome; reason: string | null };

/** Ответ оболочки: объект `{outcome, reason}` или (старая оболочка) строка. */
export function toInstallResult(reply: unknown): InstallResult {
  if (typeof reply === "string") return { outcome: reply as InstallOutcome, reason: null };
  const value = (reply ?? {}) as Partial<InstallResult>;
  return { outcome: value.outcome ?? "installer", reason: value.reason ?? null };
}

/**
 * Скачать, сверить и запустить установщик; после этого приложение выходит.
 * `confirmed` — человек согласился прервать идущую расшифровку (без него
 * оболочка в этом случае отвечает вопросом `UPDATE_CONFIRM_WORK` из About).
 */
export async function installUpdate(confirmed = false): Promise<InstallResult> {
  if (!inTauri()) throw new Error(NOT_IN_APP);
  return toInstallResult(await invoke<unknown>("install_update", { confirmed }));
}

/** Где запущен Meet (macOS, `mac_install::Location`). */
export type AppLocation = "applications" | "user-applications" | "translocated" | "disk-image" | "elsewhere";
/** Итог прошлой попытки обновления (`logs/update-last.json`). */
export type LastAttempt = { at: string; finish: string; reason: string | null };
/** `update_status` оболочки: место приложения и прошлая неудача. */
export type UpdateStatus = {
  location: AppLocation | null;
  bundle: string | null;
  offer_move: boolean;
  move_hint: string | null;
  last_failure: LastAttempt | null;
};

export async function updateStatus(): Promise<UpdateStatus | null> {
  if (!inTauri()) return null;
  return invoke<UpdateStatus>("update_status").catch(() => null);
}

/**
 * «Переместить Meet в Программы» (macOS): приложение перезапустится оттуда.
 * Идёт расшифровка — оболочка отвечает вопросом `UPDATE_CONFIRM_WORK` (как у
 * обновления), `confirmed` — человек согласился её прервать.
 */
export async function moveToApplications(confirmed = false): Promise<void> {
  if (!inTauri()) throw new Error(NOT_IN_APP);
  await invoke<void>("move_to_applications", { confirmed });
}

export const onUpdateProgress = (cb: (p: UpdateProgress) => void) => listenShell("update-progress", cb);

/** Ответ `install_update`, когда загрузку прервали «Отменить» (`updater::CANCELLED`). */
export const UPDATE_CANCELLED = "Загрузка обновления отменена";

/** Прервать идущую загрузку обновления: `install_update` вернёт UPDATE_CANCELLED. */
export async function cancelUpdate(): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("cancel_update");
}

/**
 * Страница выпусков («Скачать новую версию»). Имя репозитория знает только
 * оболочка (`UPDATE_REPO`); вне приложения — null.
 */
export async function releasesPage(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string>("releases_page").catch(() => null);
}

/** Что оболочка знает о резиденте ("running", "engine-missing", …); вне приложения — null. */
export async function residentStatus(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string>("resident_status");
}

/**
 * Открыть страницу в браузере. Оболочка открывает только свои адреса
 * (huggingface.co, claude.ai, github.com/openai/codex, opencode.ai, Releases форка) — см.
 * `open_url`.
 * Не открылась — только в журнал консоли: это ссылка, а не действие.
 */
export async function openUrl(url: string): Promise<void> {
  if (!inTauri()) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  await invoke<void>("open_url", { url }).catch((cause) => console.warn("open_url:", cause));
}

/** Отметка «мастер пройден» для оболочки: при старте она не откроет окно с мастером. */
export async function markWizardDone(): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("mark_wizard_done");
}

/**
 * Есть ли команда в оболочке: вызов без аргументов. Нет команды — Tauri
 * отвечает «Command … not found»; есть — ругается на аргументы (или
 * выполняется). Так окно работает и со старой оболочкой.
 */
export async function probeCommand(call: () => Promise<unknown>): Promise<boolean> {
  try {
    await call();
    return true;
  } catch (cause) {
    return !/^Command \S+ not found$/.test(String(cause));
  }
}

/**
 * Умеет ли оболочка автозапуск. IMPORTANT — контракт с оболочкой:
 * * `set_autostart` обязан принимать `enabled: bool` как ОБЯЗАТЕЛЬНЫЙ аргумент:
 *   проба зовёт команду без аргументов, и с необязательным `enabled` она бы
 *   выполнилась (переключила автозапуск) вместо отказа;
 * * проба опирается на то, что у команд приложения нет манифеста прав (ACL в
 *   build.rs): с ним неразрешённая команда отвечала бы «… not allowed», и
 *   проба сочла бы её существующей.
 */
export async function autostartAvailable(): Promise<boolean> {
  if (!inTauri()) return false;
  return probeCommand(() => invoke("set_autostart"));
}

/** Запускать ли приложение вместе с Windows. */
export async function setAutostart(enabled: boolean): Promise<void> {
  await invoke<void>("set_autostart", { enabled });
}

/**
 * Состояние автозапуска: сохранённый выбор, а без него — включён ли он в
 * реестре. null — не выбирали (или старая оболочка, или вне приложения).
 */
export async function getAutostart(): Promise<boolean | null> {
  if (!inTauri()) return null;
  return invoke<boolean | null>("get_autostart").catch(() => null);
}

/** Открыть папку журналов (сервис не запустился). */
export async function openLogs(): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_logs");
}

/** macOS: «Системные настройки → Конфиденциальность и безопасность → Запись экрана». */
export async function openScreenRecordingSettings(): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_screen_recording_settings");
}

// --- вкладка «Агент»: Claude Code / Codex / OpenCode во встроенном терминале ---

/** Событие `agent-data`: кусок вывода терминала сессии `id` (UTF-8, целые символы). */
export type AgentData = { id: string; data: string };
/** Событие `agent-exit`: сессия `id` закончилась; `code` — код выхода (null — неизвестен). */
export type AgentExit = { id: string; code: number | null };

/**
 * Запустить агента (`claude-code`, `codex` или `opencode`) в папке записи. Оболочка
 * сначала просит резидента обновить transcript.md, затем запускает CLI в
 * псевдоконсоли. Возвращает id сессии; прежняя сессия этой записи гасится.
 */
/** `resume` — «Продолжить прошлую»: Claude Code `--resume`, Codex `resume --last`, OpenCode `--continue`. */
export const agentSpawn = (recordingId: string, provider: string, cols: number, rows: number, resume = false) =>
  invoke<string>("agent_spawn", { recordingId, provider, cols, rows, resume });
export const agentWrite = (id: string, data: string) => invoke<void>("agent_write", { id, data });
export const agentResize = (id: string, cols: number, rows: number) =>
  invoke<void>("agent_resize", { id, cols, rows });
export const agentKill = (id: string) => invoke<void>("agent_kill", { id });
/**
 * Остановить агентов этой записи и дождаться (до 2 с), пока они выйдут: агент
 * работает в папке встречи, и Windows не даёт удалить папку, пока она чья-то
 * рабочая. Вне приложения агентов нет — сразу готово. Ошибка не мешает
 * удалению: резидент сам подождёт и скажет, если папка занята.
 */
export async function agentKillRecording(recordingId: string): Promise<void> {
  if (!inTauri()) return;
  try {
    await invoke<void>("agent_kill_recording", { recordingId });
  } catch (cause) {
    console.warn("agent_kill_recording:", cause);
  }
}
export const onAgentData = (cb: (d: AgentData) => void) => listenShell("agent-data", cb);
export const onAgentExit = (cb: (e: AgentExit) => void) => listenShell("agent-exit", cb);

// --- панель записи под значком в строке меню macOS (tray_panel.rs) ----------

/** Оболочка показала или спрятала панель (`tray-panel`: `{ visible }`). */
export async function onTrayPanel(cb: (visible: boolean) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<{ visible?: unknown }>("tray-panel", (e) => cb(e.payload?.visible === true));
}

/** Видно ли окно панели сейчас; вне оболочки — null. */
export async function trayPanelVisible(): Promise<boolean | null> {
  if (!inTauri()) return null;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  return getCurrentWindow().isVisible();
}

/** Высота содержимого панели: оболочка подгоняет под неё окно. */
export async function trayPanelFit(height: number): Promise<void> {
  if (inTauri()) await invoke<void>("tray_panel_fit", { height }).catch(() => {});
}

export async function trayPanelHide(): Promise<void> {
  if (inTauri()) await invoke<void>("tray_panel_hide").catch(() => {});
}

/** Спрятать панель и открыть окно Meet — на записи или разделе настроек. */
export async function trayPanelOpen(target: { recording?: string; section?: string } = {}): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("tray_panel_open", { recording: target.recording ?? null, section: target.section ?? null });
}
