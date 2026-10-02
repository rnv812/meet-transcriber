/**
 * Настройки: слева разделы, справа строки «подпись — значение».
 *
 * Черновик по группам (`auto_record`, `asr`, …) + «Сохранить/Сбросить»; PATCH
 * принимает секции целиком. Исключение — переключатель автозаписи: резидент
 * применяет его на лету (`POST /auto-record`). Всё остальное в `auto_record`
 * читается при старте — честно говорим, что нужен перезапуск.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  type Devices, type Endpoint, type Processes,
  NoResidentError, getDevices, getProcesses, getSettings, patchSettings, setAutoRecord,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, openFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { About } from "./About";
import { AnalysisSection } from "./AnalysisSection";
import { AssistantSection, assistantChangesInvalid } from "./AssistantSection";
import { AutostartRow } from "./AutostartRow";
import { BrowserCalls } from "./BrowserCalls";
import { CallPrograms } from "./CallPrograms";
import { DiagnosticsPane } from "./DiagnosticsPane";
import { EnginePane } from "./EnginePane";
import { ExportSection, cleanSetting, exportChangesInvalid } from "./ExportSection";
import { HotwordsEditor } from "./HotwordsEditor";
import { MarkupSection, markupChangesInvalid } from "./MarkupSection";
import { ReplacementsEditor } from "./ReplacementsEditor";
import { ModelsPane } from "./ModelsPane";
import { PathText, Radio, Row, Switch, type Raw, type SetFn } from "./Section";
import {
  AsrModelTip, AutoRecordTip, GpuMarkerTip, GraceTip, HookCommandTip, RecurringWindowTip, VoiceThresholdTip,
} from "./tips";
import { SoundSection } from "./SoundSection";
import "./settings.css";

type SectionId =
  | "recording" | "sound" | "auto" | "asr" | "engine" | "export" | "assistant" | "analysis" | "markup" | "diagnostics"
  | "about" | "advanced";

const MENU: { id: SectionId; title: string }[] = [
  { id: "recording", title: "Запись" },
  { id: "sound", title: "Звук" },
  { id: "auto", title: "Автозапись" },
  { id: "asr", title: "Распознавание" },
  { id: "engine", title: "Движок и модели" },
  { id: "export", title: "Экспорт встреч" },
  { id: "assistant", title: "Ассистент" },
  { id: "analysis", title: "Анализ встречи" },
  { id: "markup", title: "Расшифровка: подсветка и разметка" },
  { id: "diagnostics", title: "Диагностика" },
  { id: "about", title: "О программе" },
  { id: "advanced", title: "Дополнительно" },
];

const NO_DRAFT: SectionId[] = ["diagnostics", "about"];

function TextRow({ id, label, hint, help, value, placeholder, short, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: string; placeholder?: string; short?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id}>
      <input id={id} type="text" className={short ? "input--short" : undefined} placeholder={placeholder}
        value={value} onChange={(e) => onChange(e.target.value)} />
    </Row>
  );
}

function SecondsRow({ id, label, hint, value, onChange }: {
  id: string; label: string; hint: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <span className="with-unit">
        <input id={id} type="number" min={0} className="num" value={value}
          onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) onChange(Math.max(0, n)); }} />
        <span className="unit">секунд</span>
      </span>
    </Row>
  );
}

/**
 * Целое число минут в пределах [min, max]. Пока человек печатает, в черновик
 * уходят только допустимые значения (стёртое поле не превращается в 1, а «15»
 * не проходит через «1»); на выходе из поля текст приводится к краю диапазона.
 */
function MinutesRow({ id, label, hint, help, value, min, max, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: number; min: number; max: number;
  onChange: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText((cur) => (Number(cur) === value ? cur : String(value))); }, [value]);
  const parse = (t: string) => (t.trim() === "" ? NaN : Math.round(Number(t)));
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id}>
      <span className="with-unit">
        <input id={id} type="number" min={min} max={max} step={1} className="num" value={text}
          onChange={(e) => {
            setText(e.target.value);
            const n = parse(e.target.value);
            if (Number.isFinite(n) && n >= min && n <= max) onChange(n);
          }}
          onBlur={() => {
            const n = parse(text);
            const next = Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : value;
            setText(String(next));
            if (next !== value) onChange(next);
          }} />
        <span className="unit">минут</span>
      </span>
    </Row>
  );
}

function RecordingSection({ draft, set, recordingsDir }: {
  draft: Raw; set: SetFn; recordingsDir: string | null;
}) {
  const v = (k: string) => draft.recording?.[k];
  return (
    <>
      <Row label="Папка записей" hint="Здесь хранятся записи встреч. Путь задаётся в файле настроек">
        {recordingsDir ? (
          <span className="folder">
            <PathText path={recordingsDir} />
            {inTauri() && <Button onClick={() => void openFolder(recordingsDir)}>Открыть</Button>}
          </span>
        ) : <span className="muted">Неизвестно</span>}
      </Row>
      <TextRow id="speaker-name" label="Ваше имя в расшифровке" short
        hint="Так подписываются реплики, записанные с вашего микрофона"
        value={String(v("speaker_name") ?? "Вы")} onChange={(x) => set("recording", "speaker_name", x)} />
      <Switch label="Расшифровывать сразу после записи" value={Boolean(v("auto_transcribe"))}
        onChange={(x) => set("recording", "auto_transcribe", x)} />
      <AutostartRow />
      <Radio label="Уведомления" value={(draft.ui?.notifications as "all" | "important" | "off") ?? "all"}
        options={[
          { value: "all", label: "Все" },
          { value: "important", label: "Только важные" },
          { value: "off", label: "Выключены" },
        ]}
        onChange={(x) => set("ui", "notifications", x)} />
    </>
  );
}

function AutoSection({ draft, set, processes, loadProcesses, onToggle }: {
  draft: Raw; set: SetFn; processes: Processes | null; loadProcesses: () => Promise<Processes>;
  onToggle: (v: boolean) => void;
}) {
  const v = (k: string) => draft.auto_record?.[k];
  const selected = (v("processes") as string[] | undefined) ?? [];
  return (
    <>
      <Switch label="Записывать звонки автоматически"
        hint="Запись начинается со звонком и останавливается после его окончания" help={<AutoRecordTip />}
        value={Boolean(v("enabled"))} onChange={onToggle} />
      <p className="muted sdesc">Параметры ниже применяются после перезапуска приложения.</p>
      <CallPrograms value={selected} processes={processes} loadProcesses={loadProcesses}
        onChange={(x) => set("auto_record", "processes", x)} />
      <BrowserCalls browsers={(v("browsers") as string[] | undefined) ?? []}
        requireSite={Boolean(v("browser_require_site"))} sites={(v("call_sites") as string[] | undefined) ?? []}
        onBrowsers={(x) => set("auto_record", "browsers", x)}
        onRequireSite={(x) => set("auto_record", "browser_require_site", x)}
        onSites={(x) => set("auto_record", "call_sites", x)} />
      <MinutesRow id="grace" label="Ждать повторного подключения, мин" min={1} max={60}
        hint="Запись остановится, если за это время вы не вернётесь в звонок" help={<GraceTip />}
        value={Number(v("grace_minutes") ?? 10)} onChange={(x) => set("auto_record", "grace_minutes", x)} />
      <SecondsRow id="min-call" label="Минимальная длительность звонка"
        hint="Более короткие записи сохраняются, но не расшифровываются автоматически"
        value={Number(v("min_call_seconds") ?? 0)} onChange={(x) => set("auto_record", "min_call_seconds", x)} />
    </>
  );
}

function AsrSection({ draft, set, endpoint }: { draft: Raw; set: SetFn; endpoint: Endpoint }) {
  const v = (k: string) => draft.asr?.[k];
  return (
    <>
      <Row label="Устройство для распознавания" htmlFor="asr-device" help={<AsrModelTip />}
        hint="Авто: видеокарта, если она доступна, иначе процессор">
        <select id="asr-device" value={String(v("device") ?? "auto")} onChange={(e) => set("asr", "device", e.target.value)}>
          <option value="auto">Авто</option>
          <option value="cuda">Видеокарта</option>
          <option value="cpu">Процессор</option>
        </select>
      </Row>
      <TextRow id="asr-model" label="Модель Whisper для видеокарты (CUDA)" value={String(v("model") ?? "")}
        hint="Скачать и выбрать модель можно в разделе «Движок и модели»"
        onChange={(x) => set("asr", "model", x)} />
      <TextRow id="asr-cpu-model" label="Модель Whisper для процессора (CPU)" value={String(v("cpu_model") ?? "")}
        hint="Если на процессоре выбран Whisper; движок выбирается в разделе «Движок и модели»"
        onChange={(x) => set("asr", "cpu_model", x)} />
      <TextRow id="asr-language" label="Язык речи" short hint="Код языка, например ru или en; auto — определить по записи" value={String(v("language") ?? "ru")}
        onChange={(x) => set("asr", "language", x)} />
      <Switch label="Уточнять время каждого слова" hint="Точнее границы реплик; расшифровка занимает немного больше времени. После GigaAM не нужно: время слов у него своё"
        value={Boolean(v("align"))} onChange={(x) => set("asr", "align", x)} />
      <Switch label="Отмечать одновременную речь" hint="Реплики, где говорят одновременно, помечаются «нахлёст»: спикер в них может быть определён неточно"
        value={Boolean(v("overlap"))} onChange={(x) => set("asr", "overlap", x)} />
      <Row label="Порог узнавания голоса" htmlFor="asr-voice-threshold" help={<VoiceThresholdTip />}
        hint="Насколько голос должен быть похож на образец из базы голосов, чтобы спикер получил имя">
        <span className="with-unit">
          <input id="asr-voice-threshold" type="range" min={50} max={95} step={1}
            value={Math.round(Number(v("voice_threshold") ?? 0.75) * 100)}
            aria-valuetext={`${Math.round(Number(v("voice_threshold") ?? 0.75) * 100)}%`}
            onChange={(e) => set("asr", "voice_threshold", Number(e.target.value) / 100)} />
          <span className="unit num">{Math.round(Number(v("voice_threshold") ?? 0.75) * 100)}%</span>
        </span>
      </Row>
      <HotwordsEditor endpoint={endpoint} />
      <ReplacementsEditor value={v("replacements")} onChange={(x) => set("asr", "replacements", x)} />
    </>
  );
}

/** "whisper.cpp" (задел, не реализован) показывается как Whisper. */
type AsrBackend = "faster-whisper" | "gigaam";

/**
 * Движок распознавания — свой для процессора и для видеокарты: GigaAM быстрее
 * и точнее на русском, Whisper многоязычный и учитывает список терминов.
 * Запись не на русском GigaAM всё равно отдаёт Whisper.
 */
function AsrEngineRows({ draft, set }: { draft: Raw; set: SetFn }) {
  const backend = (key: string, fallback: AsrBackend): AsrBackend =>
    draft.asr?.[key] === "gigaam" ? "gigaam" : draft.asr?.[key] ? "faster-whisper" : fallback;
  return (
    <>
      <Radio label="Распознавание на процессоре" value={backend("cpu_backend", "faster-whisper")}
        hint="GigaAM расшифровывает в 15–20 раз быстрее Whisper; запись не на русском всё равно распознаёт Whisper"
        options={[
          { value: "gigaam", label: "GigaAM (русский, быстро)" },
          { value: "faster-whisper", label: "Whisper (многоязычный)" },
        ]}
        onChange={(x) => set("asr", "cpu_backend", x)} />
      <Radio label="Распознавание на видеокарте" value={backend("backend", "faster-whisper")}
        hint="Whisper точнее на английских терминах и учитывает список терминов распознавания"
        options={[
          { value: "faster-whisper", label: "Whisper" },
          { value: "gigaam", label: "GigaAM (быстрее, только русский)" },
        ]}
        onChange={(x) => set("asr", "backend", x)} />
    </>
  );
}

function AdvancedSection({ draft, set }: { draft: Raw; set: SetFn }) {
  const hooks = (k: string) => draft.hooks?.[k];
  const win = hooks("recurring_window") as string[] | null | undefined;
  const setWin = (i: 0 | 1, t: string) => {
    const pair = [win?.[0] ?? "", win?.[1] ?? ""];
    pair[i] = t;
    set("hooks", "recurring_window", pair[0] && pair[1] ? pair : null);
  };
  return (
    <>
      <details className="sdetails">
        <summary>Команда после записи</summary>
        <Switch label="Запускать команду после записи" hint="Когда запись остановлена и сохранена"
          value={Boolean(hooks("post_record"))} onChange={(x) => set("hooks", "post_record", x)} />
        <TextRow id="hook-command" label="Команда" placeholder="Не задана" help={<HookCommandTip />}
          hint="Программа и аргументы через пробел; подстановки — в подсказке «?»"
          value={((hooks("command") as string[] | undefined) ?? []).join(" ")}
          onChange={(x) => set("hooks", "command", x.split(" ").filter(Boolean))} />
        <TextRow id="hook-prompt" label="Текст для {prompt}" hint="Подставляется в команду вместо {prompt}"
          value={String(hooks("prompt") ?? "")} onChange={(x) => set("hooks", "prompt", x)} />
        <Row label="Окно регулярной встречи" help={<RecurringWindowTip />}
          hint="Запись, начатая в этот промежуток, считается регулярной встречей">
          <span className="with-unit">
            <input type="text" aria-label="Начало окна" className="input--time" placeholder="11:00"
              value={win?.[0] ?? ""} onChange={(e) => setWin(0, e.target.value)} />
            <span className="unit">—</span>
            <input type="text" aria-label="Конец окна" className="input--time" placeholder="12:00"
              value={win?.[1] ?? ""} onChange={(e) => setWin(1, e.target.value)} />
          </span>
        </Row>
      </details>
      <details className="sdetails">
        <summary>Интеграции</summary>
        <Switch label="Сообщать другим программам о занятости видеокарты" help={<GpuMarkerTip />}
          hint="На время расшифровки создаётся файл-маркер"
          value={Boolean(draft.integrations?.gpu_marker)} onChange={(x) => set("integrations", "gpu_marker", x)} />
        <TextRow id="gpu-marker-path" label="Путь к файлу-маркеру" placeholder="По умолчанию"
          hint="Если не задан — gpu.lock в папке данных приложения"
          value={String(draft.integrations?.gpu_marker_path ?? "")}
          onChange={(x) => set("integrations", "gpu_marker_path", x || null)} />
      </details>
    </>
  );
}

export function SettingsPane({ endpoint, recordingsDir, initial, initialTick, onRunWizard }: {
  endpoint: Endpoint;
  recordingsDir: string | null;
  /** Открыть сразу этот раздел (id из MENU); неизвестный — с первого. */
  initial?: string;
  /** Новый номер — снова перейти в `initial` (повторная просьба оболочки или карточки). */
  initialTick?: number;
  /** «Запустить мастер» в «Движок и модели» (сам мастер после «Пропустить» не открывается);
   *  "engine" — сразу на шаг движка (переустановка). */
  onRunWizard?: (step?: "hardware" | "engine") => void;
}) {
  const [section, setSection] = useState<SectionId>(
    () => MENU.find((m) => m.id === initial)?.id ?? "recording");
  const [settings, setSettings] = useState<Raw | null>(null);
  const [draft, setDraft] = useState<Raw>({});
  const [processes, setProcesses] = useState<Processes | null>(null);
  const [devices, setDevices] = useState<Devices | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const reload = useCallback(async () => {
    try {
      const next = (await getSettings(endpoint)) as Raw;
      setSettings(next);
      setDraft(next);
      setError(null);
      setNotice(null);
    } catch (e) {
      setError(e instanceof NoResidentError ? "Служба записи не отвечает" : errorText(e));
    }
    // Справочные данные: их отсутствие не мешает править настройки.
    setProcesses(await getProcesses(endpoint).catch(() => null));
    setDevices(await getDevices(endpoint).catch(() => null));
  }, [endpoint]);

  useEffect(() => { void reload(); }, [reload]);
  // Поиск программы звонков перечитывает запущенные при каждом открытии.
  const loadProcesses = useCallback(() => getProcesses(endpoint), [endpoint]);

  // Повторная просьба открыть раздел (новый `initialTick`) — даже если он уже был запрошен.
  useEffect(() => {
    const asked = MENU.find((m) => m.id === initial)?.id;
    if (asked) setSection(asked);
  }, [initial, initialTick]);

  const set: SetFn = (group, key, value) => {
    setNotice(null);
    setDraft((cur) => ({ ...cur, [group]: { ...(cur[group] ?? {}), [key]: value } }));
  };

  // Only changed keys are sent, so edits made elsewhere (e.g. the tray) are not clobbered.
  // auto_record.enabled lives outside the draft: it goes through /auto-record.
  const changes: Record<string, Record<string, unknown>> = {};
  if (settings) {
    for (const g of Object.keys(draft)) {
      for (const [k, raw] of Object.entries(draft[g] ?? {})) {
        if (g === "auto_record" && k === "enabled") continue;
        const val = cleanSetting(g, k, raw);
        if (JSON.stringify(val) !== JSON.stringify(settings[g]?.[k])) (changes[g] ??= {})[k] = val;
      }
    }
  }
  const dirty = Object.keys(changes);
  const invalid = assistantChangesInvalid(changes) || exportChangesInvalid(changes, settings ?? {})
    || markupChangesInvalid(changes);

  const save = async () => {
    if (dirty.length === 0 || invalid) return;
    setPending(true);
    try {
      const result = await patchSettings(endpoint, changes);
      setSettings(result.settings as Raw);
      setDraft(result.settings as Raw);
      setError(null);
      setNotice(result.restart_required.length > 0
        ? "Сохранено. Часть параметров применится после перезапуска приложения."
        : "Сохранено.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPending(false);
    }
  };

  /** Переключатель живёт вне черновика: применяется и сохраняется сразу. */
  const toggleAuto = async (enabled: boolean) => {
    try {
      await setAutoRecord(endpoint, enabled);
      const patch = (cur: Raw): Raw => ({ ...cur, auto_record: { ...cur.auto_record, enabled } });
      setSettings((cur) => (cur ? patch(cur) : cur));
      setDraft(patch);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const showBar = !NO_DRAFT.includes(section);

  return (
    <div className="settings">
      <nav className="settings__menu" aria-label="Разделы настроек">
        {MENU.map((m) => (
          <button key={m.id} type="button" className="settings__item"
            aria-current={m.id === section ? "page" : undefined} onClick={() => setSection(m.id)}>
            {m.title}
          </button>
        ))}
      </nav>
      <div className="settings__body">
        <header className="settings__head">
          <h2>{MENU.find((m) => m.id === section)?.title}</h2>
          {showBar && (
            <div className="settings__actions">
              {notice && <span className="notice">{notice}</span>}
              {dirty.length > 0 && <span className="muted">Есть несохранённые изменения</span>}
              <Button onClick={() => void reload()} disabled={pending}>Сбросить</Button>
              <Button variant="primary" onClick={() => void save()} disabled={pending || dirty.length === 0 || invalid}>
                {pending ? "Сохраняю…" : "Сохранить"}
              </Button>
            </div>
          )}
        </header>
        {error && <p className="error">{error}</p>}
        <div className="settings__content">
          {!settings && section !== "about" && section !== "diagnostics" ? (
            error ? <EmptyState title="Настройки недоступны" /> : <p className="muted">Загружаю…</p>
          ) : section === "recording" ? (
            <RecordingSection draft={draft} set={set} recordingsDir={recordingsDir} />
          ) : section === "sound" ? (
            <SoundSection draft={draft} set={set} devices={devices} endpoint={endpoint} />
          ) : section === "auto" ? (
            <AutoSection draft={draft} set={set} processes={processes} loadProcesses={loadProcesses}
              onToggle={(v) => void toggleAuto(v)} />
          ) : section === "asr" ? (
            <AsrSection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "engine" ? (
            <>
              {onRunWizard && (
                <Row label="Мастер первого запуска" hint="Пошаговая настройка: движок, токен Hugging Face, модели и запись">
                  <Button onClick={() => onRunWizard("hardware")}>Запустить мастер</Button>
                </Row>
              )}
              <EnginePane endpoint={endpoint} onReinstall={onRunWizard && (() => onRunWizard("engine"))} />
              <h3 className="shead">Распознавание речи</h3>
              <AsrEngineRows draft={draft} set={set} />
              <h3 className="shead">Модели</h3>
              <ModelsPane endpoint={endpoint}
                selectedModel={(draft.asr?.model as string | undefined) ?? null}
                selectedGigaam={(draft.asr?.gigaam_model as string | undefined) ?? null}
                onSelect={(id) => { set("asr", "model", id); setNotice("Модель выбрана. Сохраните изменения"); }}
                onSelectGigaam={(name) => { set("asr", "gigaam_model", name); setNotice("Модель выбрана. Сохраните изменения"); }} />
            </>
          ) : section === "export" ? (
            <ExportSection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "assistant" ? (
            <AssistantSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint} />
          ) : section === "analysis" ? (
            <AnalysisSection draft={draft} set={set} />
          ) : section === "markup" ? (
            <MarkupSection draft={draft} set={set} />
          ) : section === "diagnostics" ? (
            <DiagnosticsPane endpoint={endpoint} />
          ) : section === "about" ? (
            <About endpoint={endpoint} />
          ) : (
            <AdvancedSection draft={draft} set={set} />
          )}
        </div>
      </div>
    </div>
  );
}
