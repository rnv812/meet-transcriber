/**
 * Окно приложения: настройки и диагностика. Вызывается из панели и из трея.
 *
 * Настройки правятся здесь, а не в блокноте — это и было причиной всей затеи.
 * Поля собираются по секциям схемы (`meet.settings`), а не свободным JSON:
 * смысл в том, чтобы человек не знал про формат файла.
 *
 * Честность про применение: секция `auto_record` читается резидентом один раз
 * при старте, и API это возвращает в `restart_required` — окно показывает это
 * словами, а не делает вид, что применило.
 *
 * Раскладка: слева разделы, справа строки «подпись — значение». Так устроены
 * настройки в интерфейсах, на которые мы равняемся: одна колонка контента,
 * волосяные разделители, ни одной лишней рамки.
 */

import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  NoResidentError,
  type Devices,
  type EngineState,
  type Model,
  type ModelsState,
  type Processes,
  getDevices,
  getDiagnostics,
  downloadModel,
  getEngine,
  getJobs,
  getModels,
  getProcesses,
  getSettings,
  installEngine,
  patchSettings,
} from "./api";
import type { Job } from "./types";
import { useEndpoint } from "./useEndpoint";
import "./window.css";

type Raw = Record<string, any>;

type Diagnostics = {
  watch_log?: string[];
  record_log?: string[];
  folder?: string | null;
  paths?: Record<string, string>;
  dev_mode?: boolean;
};

type SectionId =
  | "auto"
  | "recording"
  | "asr"
  | "llm"
  | "hooks"
  | "engine"
  | "models"
  | "integrations"
  | "diagnostics";

const NAV: { id: SectionId; title: string; note: string }[] = [
  { id: "auto", title: "Автозапись", note: "когда поднимать запись самой" },
  { id: "recording", title: "Записи", note: "куда писать и где голоса" },
  { id: "asr", title: "Расшифровка", note: "движок, модель, точность" },
  { id: "llm", title: "Вопросы по встрече", note: "кто отвечает" },
  { id: "hooks", title: "После записи", note: "что запускать" },
  { id: "engine", title: "Движок расшифровки", note: "что считает встречу" },
  { id: "models", title: "Модели", note: "распознавание, спикеры, выравнивание" },
  { id: "integrations", title: "Интеграции", note: "связи с чужими программами" },
  { id: "diagnostics", title: "Диагностика", note: "журналы и пути" },
];

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="row">
      <div className="row__text">
        <span className="row__label">{label}</span>
        {hint && <span className="row__hint">{hint}</span>}
      </div>
      <div className="row__control">{children}</div>
    </div>
  );
}

function Switch({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="row">
      <div className="row__text">
        <span className="row__label">{label}</span>
        {hint && <span className="row__hint">{hint}</span>}
      </div>
      <div className="row__control">
        <button
          type="button"
          role="switch"
          aria-checked={value}
          aria-label={label}
          className={`switch ${value ? "switch--on" : ""}`}
          onClick={() => onChange(!value)}
        >
          <span className="switch__knob" />
        </button>
      </div>
    </div>
  );
}

function SettingsPane({
  section,
  draft,
  set,
  processes,
  devices,
}: {
  section: SectionId;
  draft: Raw;
  set: (section: string, key: string, value: unknown) => void;
  processes: Processes | null;
  devices: Devices | null;
}) {
  const value = (group: string, key: string) => draft[group]?.[key];

  if (section === "auto") {
    return (
      <>
        <Switch
          label="Поднимать запись, когда начинается звонок"
          hint="кончился звонок — запись останавливается сама"
          value={Boolean(value("auto_record", "enabled"))}
          onChange={(next) => set("auto_record", "enabled", next)}
        />
        <Row
          label="Процессы конференций"
          hint="через запятую; подсказки — из запущенных сейчас программ. Мессенджеры добавлять осторожно: звук уведомления детектор читает как звонок"
        >
          <>
            <input
              type="text"
              list="running-processes"
              value={(value("auto_record", "processes") ?? []).join(", ")}
              onChange={(event) =>
                set(
                  "auto_record",
                  "processes",
                  event.target.value
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                )
              }
            />
            <datalist id="running-processes">
              {(processes?.running ?? []).map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </>
        </Row>
        <Row
          label="Хвост после звонка"
          hint="обрыв связи и перезаход в комнату не рвут файл надвое; в этот хвост попадает и сказанное рядом с микрофоном после встречи"
        >
          <span className="with-unit">
            <input
              type="number"
              min={0}
              className="num"
              value={Number(value("auto_record", "grace_seconds") ?? 0)}
              onChange={(event) =>
                set("auto_record", "grace_seconds", Number(event.target.value))
              }
            />
            <span className="unit">секунд</span>
          </span>
        </Row>
        <Row
          label="Короткий звонок"
          hint="запись короче этого считается ложной тревогой: папка остаётся, но хук после записи не запускается"
        >
          <span className="with-unit">
            <input
              type="number"
              min={0}
              className="num"
              value={Number(value("auto_record", "min_call_seconds") ?? 0)}
              onChange={(event) =>
                set("auto_record", "min_call_seconds", Number(event.target.value))
              }
            />
            <span className="unit">секунд</span>
          </span>
        </Row>
      </>
    );
  }

  if (section === "recording") {
    return (
      <>
        <Row label="Папка записей" hint="пусто — папка по умолчанию">
          <input
            type="text"
            placeholder="по умолчанию"
            value={value("recording", "out_dir") ?? ""}
            onChange={(event) =>
              set("recording", "out_dir", event.target.value || null)
            }
          />
        </Row>
        <Row label="База голосов" hint="имена спикеров подставляются из неё">
          <input
            type="text"
            placeholder="по умолчанию"
            value={value("recording", "voices_dir") ?? ""}
            onChange={(event) =>
              set("recording", "voices_dir", event.target.value || null)
            }
          />
        </Row>
        <Row
          label="Как подписывать вас"
          hint="микрофонная дорожка — это всегда владелец машины; в транскрипте она подписана так"
        >
          <input
            type="text"
            className="input--short"
            value={value("recording", "speaker_name") ?? "Вы"}
            onChange={(event) => set("recording", "speaker_name", event.target.value)}
          />
        </Row>
      </>
    );
  }

  if (section === "asr") {
    return (
      <>
        <Row label="Движок распознавания" hint="без CUDA работает whisper.cpp">
          <select
            value={value("asr", "backend") ?? "faster-whisper"}
            onChange={(event) => set("asr", "backend", event.target.value)}
          >
            <option value="faster-whisper">faster-whisper (CUDA)</option>
            <option value="whisper.cpp">whisper.cpp (без CUDA)</option>
          </select>
        </Row>
        <Row label="Модель">
          <input
            type="text"
            value={value("asr", "model") ?? ""}
            onChange={(event) => set("asr", "model", event.target.value)}
          />
        </Row>
        <Row label="Язык">
          <input
            type="text"
            className="input--short"
            value={value("asr", "language") ?? "ru"}
            onChange={(event) => set("asr", "language", event.target.value)}
          />
        </Row>
        <Switch
          label="Уточнять пословные таймкоды"
          hint="forced alignment: точнее стыки спикеров, чуть дольше"
          value={Boolean(value("asr", "align"))}
          onChange={(next) => set("asr", "align", next)}
        />
        <Switch
          label="Учитывать перебивания"
          hint="блоки в зонах нахлёста получают пометку — атрибуция там ненадёжна"
          value={Boolean(value("asr", "overlap"))}
          onChange={(next) => set("asr", "overlap", next)}
        />
      </>
    );
  }

  if (section === "llm") {
    const local = value("llm", "provider") === "openai-compatible";
    return (
      <>
        <Row
          label="Кто отвечает"
          hint="локальная модель работает без сети и без Claude Code, но не умеет читать заметки"
        >
          <select
            value={value("llm", "provider") ?? "claude-code"}
            onChange={(event) => set("llm", "provider", event.target.value)}
          >
            <option value="claude-code">Claude Code</option>
            <option value="openai-compatible">Локальная модель</option>
          </select>
        </Row>
        {local ? (
          <>
            <Row label="Адрес эндпоинта" hint="LM Studio — 1234, Ollama — 11434">
              <input
                type="text"
                value={value("llm", "base_url") ?? ""}
                onChange={(event) => set("llm", "base_url", event.target.value)}
              />
            </Row>
            <Row label="Имя модели">
              <input
                type="text"
                placeholder="как в LM Studio"
                value={value("llm", "local_model") ?? ""}
                onChange={(event) =>
                  set("llm", "local_model", event.target.value || null)
                }
              />
            </Row>
          </>
        ) : (
          <Row label="Модель Claude">
            <input
              type="text"
              className="input--short"
              value={value("llm", "model") ?? "sonnet"}
              onChange={(event) => set("llm", "model", event.target.value)}
            />
          </Row>
        )}
        <Row label="Папка заметок" hint="контекст задачи для ответов; пусто — без него">
          <input
            type="text"
            placeholder="не задана"
            value={value("assist", "vault") ?? ""}
            onChange={(event) => set("assist", "vault", event.target.value || null)}
          />
        </Row>
      </>
    );
  }

  if (section === "hooks") {
    const window_ = value("hooks", "recurring_window") as string[] | null;
    const setWindow = (index: 0 | 1, time: string) => {
      const pair = [window_?.[0] ?? "", window_?.[1] ?? ""];
      pair[index] = time;
      set("hooks", "recurring_window", pair[0] && pair[1] ? pair : null);
    };
    return (
      <>
        <Switch
          label="Запускать команду после остановки записи"
          value={Boolean(value("hooks", "post_record"))}
          onChange={(next) => set("hooks", "post_record", next)}
        />
        <Row
          label="Команда"
          hint="аргументы через пробел; плейсхолдеры {folder}, {date}, {project}, {prompt}. Пример: explorer {folder}"
        >
          <input
            type="text"
            placeholder="ничего не запускать"
            value={(value("hooks", "command") ?? []).join(" ")}
            onChange={(event) =>
              set(
                "hooks",
                "command",
                event.target.value.split(" ").filter(Boolean),
              )
            }
          />
        </Row>
        <Row label="Текст-подсказка" hint="подставляется в {prompt}">
          <input
            type="text"
            value={value("hooks", "prompt") ?? ""}
            onChange={(event) => set("hooks", "prompt", event.target.value)}
          />
        </Row>
        <Row
          label="Окно регулярной встречи"
          hint="запись, начатая в этом окне, похожа на регулярную встречу — к подсказке добавится уточнение. Пусто — про регулярность не говорим"
        >
          <span className="with-unit">
            <input
              type="text"
              className="input--short"
              placeholder="11:00"
              value={window_?.[0] ?? ""}
              onChange={(event) => setWindow(0, event.target.value)}
            />
            <span className="unit">—</span>
            <input
              type="text"
              className="input--short"
              placeholder="12:00"
              value={window_?.[1] ?? ""}
              onChange={(event) => setWindow(1, event.target.value)}
            />
          </span>
        </Row>
      </>
    );
  }

  if (section === "integrations") {
    return (
      <>
        <Switch
          label="Сообщать другим программам, что GPU занят"
          hint="файл-маркер на время расшифровки: по нему сосед может выгрузить свою модель из видеопамяти. Некому читать — выключите"
          value={Boolean(value("integrations", "gpu_marker"))}
          onChange={(next) => set("integrations", "gpu_marker", next)}
        />
        <Row label="Путь маркера" hint="пусто — рядом с остальным состоянием">
          <input
            type="text"
            placeholder="по умолчанию"
            value={value("integrations", "gpu_marker_path") ?? ""}
            onChange={(event) =>
              set("integrations", "gpu_marker_path", event.target.value || null)
            }
          />
        </Row>
        <Row
          label="Устройства записи"
          hint="закрепить конкретное устройство нельзя: запись следит за системными по умолчанию и переживает их смену (наушники ушли и вернулись)"
        >
          <span className="devices">
            {devices?.available ? (
              <>
                <code className="path">звук: {devices.system?.name}</code>
                <code className="path">микрофон: {devices.mic?.name}</code>
              </>
            ) : (
              <span className="muted">{devices?.error ?? "неизвестно"}</span>
            )}
          </span>
        </Row>
      </>
    );
  }

  return null;
}

function EnginePane({
  engine,
  loading,
  job,
  onInstall,
}: {
  engine: EngineState | null;
  loading: boolean;
  job: Job | null;
  onInstall: () => void;
}) {
  if (!engine) return <p className="muted">{loading ? "Загружаю…" : "Нет данных."}</p>;
  const busy = job?.state === "queued" || job?.state === "running";
  return (
    <>
      <div className="rows">
        <Row
          label="Состояние"
          hint="без движка приложение пишет встречи, но не расшифровывает их — так и задумано: писать можно на ноутбуке, считать на машине с картой"
        >
          <span className={engine.installed ? "tag tag--live" : "tag"}>
            {engine.installed ? "установлен" : "не установлен"}
          </span>
        </Row>
        <Row label="Видеокарта" hint="без неё расшифровка идёт на процессоре и заметно дольше">
          <span className="tag">
            {engine.gpu.available ? (engine.gpu.name ?? "есть") : "не найдена"}
          </span>
        </Row>
        <Row label="ffmpeg" hint="нужен и для записи, и для конвертации дорожек">
          <span className={engine.ffmpeg ? "tag tag--live" : "tag"}>
            {engine.ffmpeg ? "есть" : "нет"}
          </span>
        </Row>
        <Row
          label="Что будет установлено"
          hint={`вариант ${engine.flavor === "cuda" ? "для видеокарты" : "для процессора"}, около ${engine.download_gb} ГБ загрузки`}
        >
          <span className="devices">
            {engine.components.map((component) => (
              <span
                key={component.module}
                className={component.installed ? "tag tag--live" : "tag"}
              >
                {component.title}
              </span>
            ))}
          </span>
        </Row>
        <Row label="Куда" hint="окружение, которым резидент запускает задачи">
          <code className="path">{engine.target}</code>
        </Row>
      </div>

      <div className="bar">
        <button className="btn btn--primary" onClick={onInstall} disabled={busy}>
          {busy
            ? "Ставлю…"
            : engine.installed
              ? "Переустановить"
              : "Установить движок"}
        </button>
        {busy && <span className="muted">это надолго: гигабайты и минуты</span>}
        {job?.state === "failed" && <span className="error">{job.error}</span>}
        {job?.state === "done" && <span className="notice">Готово</span>}
      </div>

      {job?.note && <p className="muted">{job.note}</p>}
    </>
  );
}

const MODEL_KIND: Record<string, string> = {
  asr: "распознавание",
  diarization: "спикеры",
  align: "выравнивание",
};

function ModelRow({
  model,
  busy,
  canDownload,
  onDownload,
  onSelect,
}: {
  model: Model;
  busy: boolean;
  canDownload: boolean;
  onDownload: () => void;
  onSelect: () => void;
}) {
  return (
    <div className="row">
      <div className="row__text">
        <span className="row__label">
          {model.title}
          {model.recommended && <span className="tag tag--live">рекомендуется</span>}
        </span>
        <span className="row__hint">{model.note}</span>
        <span className="row__hint">
          {MODEL_KIND[model.kind] ?? model.kind} · {model.size_gb} ГБ · {model.id}
          {model.downloaded ? " · скачана" : ""}
          {model.blocked ? " · нужен токен" : ""}
        </span>
      </div>
      <div className="row__control model-actions">
        {model.kind === "asr" && (
          <button
            className={model.selected ? "btn btn--primary" : "btn"}
            onClick={onSelect}
            disabled={model.selected}
          >
            {model.selected ? "выбрана" : "выбрать"}
          </button>
        )}
        <button
          className="btn"
          onClick={onDownload}
          disabled={busy || model.blocked || !canDownload}
        >
          {model.downloaded ? "обновить" : "скачать"}
        </button>
      </div>
    </div>
  );
}

function ModelsPane({
  models,
  loading,
  job,
  onDownload,
  onSelect,
  token,
  onToken,
}: {
  models: ModelsState | null;
  loading: boolean;
  job: Job | null;
  onDownload: (id: string) => void;
  onSelect: (id: string) => void;
  token: string;
  onToken: (value: string) => void;
}) {
  // Пусто и не грузим — значит запрос упал (ошибка показана выше по странице);
  // вечное «Загружаю…» это как раз и скрывало.
  if (!models) return <p className="muted">{loading ? "Загружаю…" : "Нет данных."}</p>;
  const busy = job?.state === "queued" || job?.state === "running";
  return (
    <>
      {!models.can_download && (
        <p className="notice">
          Загрузчик моделей приходит вместе с движком. Установите его в разделе
          «Движок расшифровки» — после этого модели можно скачивать; выбранная
          модель распознавания и так скачается при первой расшифровке.
        </p>
      )}
      <div className="rows">
        <Row
          label="Токен Hugging Face"
          hint="нужен только модели спикеров: она за принятием условий на huggingface.co. Пусто — берётся переменная среды HF_TOKEN"
        >
          <input
            type="text"
            placeholder={models.token ? "задан" : "не задан"}
            value={token}
            onChange={(event) => onToken(event.target.value)}
          />
        </Row>
        {models.items.map((model) => (
          <ModelRow
            key={model.id}
            model={model}
            busy={busy}
            canDownload={models.can_download}
            onDownload={() => onDownload(model.id)}
            onSelect={() => onSelect(model.id)}
          />
        ))}
        <Row
          label="Кэш моделей"
          hint="общий с библиотеками движка: уже скачанное не качается заново"
        >
          <code className="path">{models.cache}</code>
        </Row>
      </div>
      {busy && (
        <p className="muted">
          Качаю {job?.folder}: это гигабайты. Окно можно закрыть — задача идёт в
          резиденте.
        </p>
      )}
      {job?.state === "failed" && <p className="error">{job.error}</p>}
      {job?.state === "done" && <p className="notice">Скачано: {job.result}</p>}
    </>
  );
}

function DiagnosticsPane({ data }: { data: Diagnostics | null }) {
  if (!data) return <p className="muted">Нет данных.</p>;
  return (
    <>
      <div className="rows">
        {Object.entries(data.paths ?? {}).map(([name, path]) => (
          <div className="row" key={name}>
            <div className="row__text">
              <span className="row__label">{name}</span>
            </div>
            <div className="row__control">
              <code className="path">{path}</code>
            </div>
          </div>
        ))}
        <div className="row">
          <div className="row__text">
            <span className="row__label">Режим</span>
          </div>
          <div className="row__control">
            <span className="tag">
              {data.dev_mode ? "из репозитория" : "установленное приложение"}
            </span>
          </div>
        </div>
      </div>

      <h3 className="eyebrow logs__title">Журнал дежурного</h3>
      <pre className="log">{(data.watch_log ?? []).slice(-120).join("\n") || "пусто"}</pre>

      <h3 className="eyebrow logs__title">
        Журнал записи{data.folder ? ` — ${data.folder}` : ""}
      </h3>
      <pre className="log">{(data.record_log ?? []).join("\n") || "записи нет"}</pre>
    </>
  );
}

function App() {
  const { endpoint, reresolve, missing } = useEndpoint();
  const [settings, setSettings] = useState<Raw | null>(null);
  const [draft, setDraft] = useState<Raw>({});
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [processes, setProcesses] = useState<Processes | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [engineJob, setEngineJob] = useState<Job | null>(null);
  const [models, setModels] = useState<ModelsState | null>(null);
  const [modelsTried, setModelsTried] = useState(false);
  const [modelJob, setModelJob] = useState<Job | null>(null);
  const [section, setSection] = useState<SectionId>("auto");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (missing) setError("Дежурный не запущен: настройки читает и пишет он.");
  }, [missing]);

  const reload = useCallback(async () => {
    if (!endpoint) return;
    try {
      const next = await getSettings(endpoint);
      setSettings(next);
      setDraft(next);
      setDiagnostics((await getDiagnostics(endpoint, 200)) as Diagnostics);
      // Список процессов и устройств — справочные данные: их отсутствие не
      // должно мешать править настройки.
      setProcesses(await getProcesses(endpoint).catch(() => null));
      setDevices(await getDevices(endpoint).catch(() => null));
      try {
        setEngine(await getEngine(endpoint));
      } catch (cause) {
        setEngine(null);
        setError(`Движок не загрузился: ${String(cause)}`);
      }
      try {
        setModels(await getModels(endpoint));
      } catch (cause) {
        setModels(null);
        setError(`Каталог моделей не загрузился: ${String(cause)}`);
      } finally {
        setModelsTried(true);
      }
      setError(null);
    } catch (cause) {
      // Резидент мог перезапуститься на новом порту — перечитываем адрес,
      // тогда эффект перезагрузит настройки с живым endpoint.
      if (cause instanceof NoResidentError) void reresolve();
      else setError(String(cause));
    }
  }, [endpoint, reresolve]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Установка движка идёт минутами: следим за задачей опросом. SSE тут не
  // нужен — состояние меняется редко, а опрос переживает любые обрывы.
  useEffect(() => {
    if (!endpoint || !engineJob) return;
    if (engineJob.state !== "queued" && engineJob.state !== "running") return;
    const timer = window.setInterval(async () => {
      const jobs = await getJobs(endpoint).catch(() => null);
      const mine = jobs?.items.find((item) => item.id === engineJob.id);
      if (mine) setEngineJob(mine);
      if (mine && (mine.state === "done" || mine.state === "failed")) {
        try {
        setEngine(await getEngine(endpoint));
      } catch (cause) {
        setEngine(null);
        setError(`Движок не загрузился: ${String(cause)}`);
      }
      try {
        setModels(await getModels(endpoint));
      } catch (cause) {
        setModels(null);
        setError(`Каталог моделей не загрузился: ${String(cause)}`);
      } finally {
        setModelsTried(true);
      }
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [endpoint, engineJob]);

  // Загрузка модели идёт минутами — следим тем же опросом, что и за движком.
  useEffect(() => {
    if (!endpoint || !modelJob) return;
    if (modelJob.state !== "queued" && modelJob.state !== "running") return;
    const timer = window.setInterval(async () => {
      const list = await getJobs(endpoint).catch(() => null);
      const mine = list?.items.find((item) => item.id === modelJob.id);
      if (mine) setModelJob(mine);
      if (mine && (mine.state === "done" || mine.state === "failed")) {
        setModels(await getModels(endpoint).catch(() => null));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [endpoint, modelJob]);

  const onDownloadModel = async (id: string) => {
    if (!endpoint) return;
    try {
      setModelJob(await downloadModel(endpoint, id));
    } catch (cause) {
      setError(String(cause));
    }
  };

  const onSelectModel = (id: string) => {
    set("asr", "model", id);
    setNotice("Модель выбрана — не забудьте сохранить");
  };

  const onInstallEngine = async () => {
    if (!endpoint) return;
    try {
      setEngineJob(await installEngine(endpoint));
    } catch (cause) {
      setError(String(cause));
    }
  };

  const set = (group: string, key: string, value: unknown) => {
    setNotice(null);
    setDraft((current) => ({
      ...current,
      [group]: { ...(current[group] ?? {}), [key]: value },
    }));
  };

  // Считаем изменённым весь раздел: PATCH принимает секции целиком, а сравнение
  // по полям только запутало бы — пользователь мыслит разделами.
  const dirty = settings
    ? Object.keys(draft).filter(
        (group) =>
          typeof draft[group] === "object" &&
          JSON.stringify(draft[group]) !== JSON.stringify(settings[group]),
      )
    : [];

  const save = async () => {
    if (!endpoint || dirty.length === 0) return;
    setPending(true);
    try {
      const updates: Raw = {};
      for (const group of dirty) updates[group] = draft[group];
      const result = await patchSettings(endpoint, updates);
      setSettings(result.settings as Raw);
      setDraft(result.settings as Raw);
      setNotice(
        result.restart_required.length > 0
          ? `Сохранено. Автозапись дежурный перечитает при следующем запуске.`
          : "Сохранено.",
      );
    } catch (cause) {
      setError(String(cause));
    } finally {
      setPending(false);
    }
  };

  const current = NAV.find((item) => item.id === section);

  return (
    <div className="app">
      <aside className="nav">
        <div className="nav__brand">
          <span className="nav__mark" />
          <span>meet</span>
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.id}
              className={`nav__item ${section === item.id ? "nav__item--on" : ""}`}
              onClick={() => setSection(item.id)}
            >
              <span className="nav__title">{item.title}</span>
              <span className="nav__note">{item.note}</span>
            </button>
          ))}
        </nav>
        <div className="nav__foot">
          {endpoint ? (
            <span className="tag tag--live">дежурный на связи</span>
          ) : (
            <span className="tag">дежурного нет</span>
          )}
        </div>
      </aside>

      <main className="main">
        <header className="head">
          <div>
            <h1>{current?.title}</h1>
            <p className="head__note">{current?.note}</p>
          </div>
          <div className="head__actions">
            {notice && <span className="notice">{notice}</span>}
            {dirty.length > 0 && <span className="dirty">есть несохранённое</span>}
            <button className="btn" onClick={() => void reload()} disabled={pending}>
              Сбросить
            </button>
            <button
              className="btn btn--primary"
              onClick={() => void save()}
              disabled={pending || dirty.length === 0}
            >
              {pending ? "Сохраняю…" : "Сохранить"}
            </button>
          </div>
        </header>

        {error && <p className="error">{error}</p>}

        <div className="content">
          {section === "diagnostics" ? (
            <DiagnosticsPane data={diagnostics} />
          ) : section === "models" ? (
            <ModelsPane
              models={models}
              loading={!modelsTried}
              job={modelJob}
              onDownload={(id) => void onDownloadModel(id)}
              onSelect={onSelectModel}
              token={String(draft.integrations?.hf_token ?? "")}
              onToken={(value) => set("integrations", "hf_token", value)}
            />
          ) : section === "engine" ? (
            <EnginePane
              engine={engine}
              loading={!modelsTried}
              job={engineJob}
              onInstall={() => void onInstallEngine()}
            />
          ) : settings ? (
            <div className="rows">
              <SettingsPane
                section={section}
                draft={draft}
                set={set}
                processes={processes}
                devices={devices}
              />
            </div>
          ) : (
            <p className="muted">Загружаю…</p>
          )}
        </div>
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
