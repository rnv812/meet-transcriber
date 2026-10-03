/**
 * Настройки: слева разделы, справа — колонка строк «подпись — значение»
 * (по центру, не шире 760 px; шапка с «Сохранить» — над той же колонкой).
 *
 * Черновик по группам (`auto_record`, `asr`, …) + «Сохранить/Сбросить»; PATCH
 * принимает секции целиком. Исключение — переключатель автозаписи: резидент
 * применяет его на лету (`POST /auto-record`). Всё остальное в `auto_record`
 * читается при старте — честно говорим, что нужен перезапуск.
 *
 * Черновик общий на все разделы, но видно, где именно правки: точка у раздела
 * в меню. Уйти из настроек с несохранённым — вопрос (`guardRef`, его задаёт
 * App); «Сбросить…» тоже спрашивает.
 */

import { useCallback, useEffect, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import {
  type Devices, type Endpoint, type Processes,
  GIGAAM_PREFIX, NoResidentError, getDevices, getProcesses, getSettings, patchSettings, setAutoRecord,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, openFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { Disclosure } from "../../ui/Disclosure";
import { EmptyState } from "../../ui/EmptyState";
import { Loading, StatusSlot } from "../../ui/Loading";
import { PaneResizer } from "../../ui/PaneResizer";
import { About } from "./About";
import { AnalysisSection } from "./AnalysisSection";
import { AsrChoice, backendOf } from "./AsrChoice";
import { AssistantSection, assistantChangesInvalid } from "./AssistantSection";
import { AutostartRow } from "./AutostartRow";
import {
  CategoriesSection, categoriesChanged, categoriesError, categoriesToSave, draftCategories, type DraftCategory,
} from "./CategoriesSection";
import { BrowserCalls } from "./BrowserCalls";
import { CallPrograms } from "./CallPrograms";
import { DiagnosticsPane } from "./DiagnosticsPane";
import { EnginePane } from "./EnginePane";
import { ExportSection, cleanSetting, exportChangesInvalid } from "./ExportSection";
import { HotwordsEditor } from "./HotwordsEditor";
import { MarkupSection, dropHiddenJira, markupChangesInvalid } from "./MarkupSection";
import { ReplacementsEditor } from "./ReplacementsEditor";
import { ModelsPane } from "./ModelsPane";
import { ProfilesSection } from "./ProfilesSection";
import { PathText, Radio, Row, Switch, type Raw, type SetFn } from "./Section";
import {
  AsrModelTip, AutoRecordTip, GpuMarkerTip, GraceTip, HookCommandTip, RecurringWindowTip, VoiceThresholdTip,
} from "./tips";
import { SoundSection } from "./SoundSection";
import "./settings.css";

type SectionId =
  | "recording" | "sound" | "auto" | "asr" | "engine" | "export" | "assistant" | "analysis" | "categories" | "markup"
  | "profiles"
  | "diagnostics" | "about" | "advanced";

const MENU: { id: SectionId; title: string }[] = [
  { id: "recording", title: "Запись" },
  { id: "sound", title: "Звук" },
  { id: "auto", title: "Автозапись" },
  { id: "asr", title: "Распознавание" },
  { id: "engine", title: "Движок и модели" },
  { id: "export", title: "Экспорт встреч" },
  { id: "assistant", title: "Ассистент" },
  { id: "analysis", title: "Анализ встречи" },
  { id: "categories", title: "Категории встреч" },
  { id: "markup", title: "Подсветка расшифровки" },
  { id: "profiles", title: "Профили людей" },
  { id: "diagnostics", title: "Диагностика" },
  { id: "about", title: "О программе" },
  { id: "advanced", title: "Дополнительно" },
];

const NO_DRAFT: SectionId[] = ["diagnostics", "about"];

/**
 * Разделы, в которых видна правка ключа `group.key`: точка у раздела в меню.
 * Движок и модели распознавания выбираются только в «Распознавании»; «Движок и
 * модели» — установка и загрузка.
 */
export function sectionsOf(group: string, key: string): SectionId[] {
  switch (group) {
    case "recording":
      return key === "mic_device" || key === "output_device" ? ["sound"] : ["recording"];
    case "ui": return ["recording"];
    case "auto_record": return ["auto"];
    case "asr": return ["asr"];
    case "export": return ["export"];
    case "llm": case "assist": case "agent": return ["assistant"];
    case "assistant": return key === "auto_title" ? ["analysis"] : ["assistant"];
    case "analysis": return ["analysis"];
    case "categories": return ["categories"];
    case "transcript_view": return ["markup"];
    case "integrations": return key.startsWith("jira") ? ["markup"] : ["advanced"];
    case "profiles": return ["profiles"];
    case "hooks": return ["advanced"];
    default: return [];
  }
}

/** Что App спрашивает у настроек, прежде чем уйти в другой раздел окна. */
export type SettingsGuard = {
  /** Названия разделов с несохранёнными правками; пусто — уходить можно молча. */
  dirty: string[];
  /** Можно ли сохранить прямо сейчас (нет ошибок в полях). */
  canSave: boolean;
  /** Сохранить; true — получилось. */
  save: () => Promise<boolean>;
};

/**
 * Ширина поля: `s` — короткое значение (имя, код языка), `m` — по умолчанию,
 * `l` — пути, адреса, команды, id моделей: под подписью во всю ширину колонки,
 * моноширинным шрифтом.
 */
function TextRow({ id, label, hint, help, value, placeholder, short, wide, disabled, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: string; placeholder?: string; short?: boolean;
  wide?: boolean; disabled?: boolean; onChange: (v: string) => void;
}) {
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id} stack={wide} disabled={disabled}>
      <input id={id} type="text" className={short ? "input--short" : wide ? "input--wide" : undefined}
        placeholder={placeholder} value={value} disabled={disabled} spellCheck={wide ? false : undefined}
        onChange={(e) => onChange(e.target.value)} />
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
        <span className="unit unit--slot">секунд</span>
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
        <span className="unit unit--slot">минут</span>
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
        hint="Включено — расшифровка встаёт в очередь, как только запись остановлена; выключено — по кнопке «Расшифровать» в карточке"
        onChange={(x) => set("recording", "auto_transcribe", x)} />
      <AutostartRow />
      <Radio label="Уведомления" value={(draft.ui?.notifications as "all" | "important" | "off") ?? "all"}
        hint="«Только важные» — начало и конец записи, готовая расшифровка и ошибки, без промежуточных шагов"
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
        hint="Запись начинается со звонком и останавливается после его окончания. Применяется сразу, без «Сохранить»"
        help={<AutoRecordTip />}
        value={Boolean(v("enabled"))} onChange={onToggle} />
      <p className="muted sdesc">Параметры ниже применяются после перезапуска приложения.</p>
      <CallPrograms value={selected} processes={processes} loadProcesses={loadProcesses}
        onChange={(x) => set("auto_record", "processes", x)} />
      <BrowserCalls browsers={(v("browsers") as string[] | undefined) ?? []}
        requireSite={Boolean(v("browser_require_site"))} sites={(v("call_sites") as string[] | undefined) ?? []}
        onBrowsers={(x) => set("auto_record", "browsers", x)}
        onRequireSite={(x) => set("auto_record", "browser_require_site", x)}
        onSites={(x) => set("auto_record", "call_sites", x)} />
      <MinutesRow id="grace" label="Ждать повторного подключения" min={1} max={60}
        hint="Запись остановится, если за это время вы не вернётесь в звонок" help={<GraceTip />}
        value={Number(v("grace_minutes") ?? 10)} onChange={(x) => set("auto_record", "grace_minutes", x)} />
      <SecondsRow id="min-call" label="Минимальная длительность звонка"
        hint="Более короткие записи сохраняются, но не расшифровываются автоматически"
        value={Number(v("min_call_seconds") ?? 0)} onChange={(x) => set("auto_record", "min_call_seconds", x)} />
    </>
  );
}

function AsrSection({ draft, saved, set, endpoint, onOpenEngine }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint; onOpenEngine: () => void;
}) {
  const v = (k: string) => draft.asr?.[k];
  return (
    <>
      <AsrChoice draft={draft} saved={saved} set={set} endpoint={endpoint} help={<AsrModelTip />}
        onOpenEngine={onOpenEngine} />
      <TextRow id="asr-language" label="Язык речи" short
        hint="Код языка, например ru или en; auto — определить по записи. GigaAM понимает только русский"
        value={String(v("language") ?? "ru")} onChange={(x) => set("asr", "language", x)} />
      <h3 className="shead">Расшифровка</h3>
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

/**
 * Где используется модель по черновику «Распознавания» — для строки модели в
 * «Движке и моделях»: id → «видеокарта», «процессор — записи не на русском», …
 */
export function modelUsage(draft: Raw): Record<string, string[]> {
  const asr = draft.asr ?? {};
  const out: Record<string, string[]> = {};
  const add = (id: unknown, text: string) => {
    if (typeof id === "string" && id) (out[id] ??= []).push(text);
  };
  for (const [device, title, key] of [["cuda", "видеокарта", "model"], ["cpu", "процессор", "cpu_model"]] as const) {
    if (backendOf(asr, device) === "gigaam") {
      add(GIGAAM_PREFIX + String(asr.gigaam_model ?? "v3_e2e_rnnt"), title);
      add(asr[key], `${title} — записи не на русском`);
    } else {
      add(asr[key], title);
    }
  }
  return out;
}

function AdvancedSection({ draft, set }: { draft: Raw; set: SetFn }) {
  const hooks = (k: string) => draft.hooks?.[k];
  const win = hooks("recurring_window") as string[] | null | undefined;
  const setWin = (i: 0 | 1, t: string) => {
    const pair = [win?.[0] ?? "", win?.[1] ?? ""];
    pair[i] = t;
    set("hooks", "recurring_window", pair[0] && pair[1] ? pair : null);
  };
  const hookOn = Boolean(hooks("post_record"));
  const markerOn = Boolean(draft.integrations?.gpu_marker);
  return (
    <>
      <Disclosure title="Команда после записи" className="sdetails">
        <Switch label="Запускать команду после записи" hint="Когда запись остановлена и сохранена"
          value={hookOn} onChange={(x) => set("hooks", "post_record", x)} />
        <TextRow id="hook-command" label="Команда" placeholder="Не задана" help={<HookCommandTip />} wide
          disabled={!hookOn}
          hint={hookOn ? "Программа и аргументы через пробел; подстановки — в подсказке «?»"
            : "Включите «Запускать команду после записи», чтобы задать команду"}
          value={((hooks("command") as string[] | undefined) ?? []).join(" ")}
          onChange={(x) => set("hooks", "command", x.split(" ").filter(Boolean))} />
        <TextRow id="hook-prompt" label="Текст для {prompt}" hint="Подставляется в команду вместо {prompt}" wide
          disabled={!hookOn} value={String(hooks("prompt") ?? "")} onChange={(x) => set("hooks", "prompt", x)} />
        <Row label="Окно регулярной встречи" help={<RecurringWindowTip />} disabled={!hookOn}
          hint="Запись, начатая в этот промежуток, считается регулярной встречей">
          <span className="with-unit">
            <input type="text" aria-label="Начало окна" className="input--time" placeholder="11:00" disabled={!hookOn}
              value={win?.[0] ?? ""} onChange={(e) => setWin(0, e.target.value)} />
            <span className="unit">—</span>
            <input type="text" aria-label="Конец окна" className="input--time" placeholder="12:00" disabled={!hookOn}
              value={win?.[1] ?? ""} onChange={(e) => setWin(1, e.target.value)} />
          </span>
        </Row>
      </Disclosure>
      <Disclosure title="Интеграции" className="sdetails">
        <Switch label="Сообщать другим программам о занятости видеокарты" help={<GpuMarkerTip />}
          hint="На время расшифровки создаётся файл-маркер"
          value={markerOn} onChange={(x) => set("integrations", "gpu_marker", x)} />
        <TextRow id="gpu-marker-path" label="Путь к файлу-маркеру" placeholder="По умолчанию" wide disabled={!markerOn}
          hint={markerOn ? "Если не задан — gpu.lock в папке данных приложения"
            : "Включите сообщение о занятости видеокарты, чтобы задать путь"}
          value={String(draft.integrations?.gpu_marker_path ?? "")}
          onChange={(x) => set("integrations", "gpu_marker_path", x || null)} />
      </Disclosure>
    </>
  );
}

/** Меню разделов: тексту настроек справа остаётся не меньше 420 px. */
const SETTINGS_MENU = { def: 200, min: 140, max: 360, reserve: 420 };

export function SettingsPane({ endpoint, recordingsDir, initial, initialTick, onRunWizard, guardRef, onDirtyChange }: {
  endpoint: Endpoint;
  /** Появились или пропали несохранённые правки (оболочке: «Выход» из трея спрашивает). */
  onDirtyChange?: (dirty: boolean) => void;
  /** Сюда настройки кладут, есть ли несохранённое и как его сохранить (вопрос при уходе — в App). */
  guardRef?: MutableRefObject<SettingsGuard | null>;
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
  const [askReset, setAskReset] = useState(false);

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
  /** Категории — не секция, а список целиком (`categories`). */
  const setCategories = (list: DraftCategory[]) => {
    setNotice(null);
    setDraft((cur) => ({ ...cur, categories: list as unknown as Raw[string] }));
  };

  // Only changed keys are sent, so edits made elsewhere (e.g. the tray) are not clobbered.
  // auto_record.enabled lives outside the draft: it goes through /auto-record.
  const changes: Record<string, Record<string, unknown>> = {};
  if (settings) {
    for (const g of Object.keys(draft)) {
      if (g === "categories") continue; // список, а не секция: ниже
      for (const [k, raw] of Object.entries(draft[g] ?? {})) {
        if (g === "auto_record" && k === "enabled") continue;
        const val = cleanSetting(g, k, raw);
        if (JSON.stringify(val) !== JSON.stringify(settings[g]?.[k])) (changes[g] ??= {})[k] = val;
      }
    }
  }
  dropHiddenJira(changes, draft);
  const categoriesDirty = settings !== null && categoriesChanged(draft.categories, settings.categories);
  const dirty = [...Object.keys(changes), ...(categoriesDirty ? ["categories"] : [])];
  const invalid = assistantChangesInvalid(changes) || exportChangesInvalid(changes, settings ?? {})
    || markupChangesInvalid(changes) || (categoriesDirty && categoriesError(draftCategories(draft.categories)) !== null);
  // Разделы с правками — точки в меню и список в вопросе при уходе.
  const dirtySections = new Set<SectionId>(categoriesDirty ? ["categories"] : []);
  for (const [g, keys] of Object.entries(changes)) {
    for (const k of Object.keys(keys)) for (const s of sectionsOf(g, k)) dirtySections.add(s);
  }
  const dirtyTitles = MENU.filter((m) => dirtySections.has(m.id)).map((m) => m.title);

  const save = async (): Promise<boolean> => {
    if (dirty.length === 0) return true;
    if (invalid) return false;
    setPending(true);
    try {
      const result = await patchSettings(endpoint, categoriesDirty
        ? { ...changes, categories: categoriesToSave(draftCategories(draft.categories)) } : changes);
      setSettings(result.settings as Raw);
      setDraft(result.settings as Raw);
      setError(null);
      setNotice(result.restart_required.length > 0
        ? "Сохранено. Часть параметров применится после перезапуска приложения."
        : "Сохранено.");
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setPending(false);
    }
  };

  // Вопрос при уходе из настроек задаёт App — ему нужны свежие правки и save.
  useEffect(() => {
    if (!guardRef) return;
    guardRef.current = { dirty: dirtyTitles, canSave: !invalid, save };
  });
  useEffect(() => () => { if (guardRef) guardRef.current = null; }, [guardRef]);
  const anyDirty = dirtyTitles.length > 0;
  const dirtyCb = useRef(onDirtyChange);
  dirtyCb.current = onDirtyChange;
  useEffect(() => { dirtyCb.current?.(anyDirty); }, [anyDirty]);
  useEffect(() => () => dirtyCb.current?.(false), []);

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
  const dirtyNoteId = "settings-dirty-note";
  const isDirty = dirty.length > 0;

  return (
    <div className="settings">
      <nav className="settings__menu" aria-label="Разделы настроек">
        <span id={dirtyNoteId} className="sr-only">Есть несохранённые изменения</span>
        {MENU.map((m) => (
          <button key={m.id} type="button" className="settings__item"
            title={dirtySections.has(m.id) ? "Есть несохранённые изменения" : undefined}
            aria-describedby={dirtySections.has(m.id) ? dirtyNoteId : undefined}
            aria-current={m.id === section ? "page" : undefined} onClick={() => setSection(m.id)}>
            <span className="settings__item-title">{m.title}</span>
            {dirtySections.has(m.id) && <span className="settings__dirty" data-dirty aria-hidden="true" />}
          </button>
        ))}
      </nav>
      <PaneResizer name="settings-menu" cssVar="--settings-menu-w" spec={SETTINGS_MENU}
        panel="before" label="Ширина меню настроек" />
      <div className="settings__body">
        <div className="settings__column">
          {/* Шапка постоянной высоты и над той же колонкой, что и строки. */}
          <header className="settings__head">
            <h2>{MENU.find((m) => m.id === section)?.title}</h2>
            <div className="settings__actions">
              {showBar && (
                <>
                  <StatusSlot className="settings__state" tone={isDirty ? "muted" : notice ? "ok" : undefined}>
                    {isDirty ? (dirtySections.has(section) ? "Есть несохранённые изменения" : `Не сохранено: ${dirtyTitles.join(", ")}`)
                      : notice}
                  </StatusSlot>
                  <Button onClick={() => setAskReset(true)} disabled={pending || !isDirty}>Сбросить…</Button>
                  <Button variant="primary" onClick={save} busy={pending} disabled={!isDirty || invalid}>
                    Сохранить
                  </Button>
                </>
              )}
            </div>
          </header>
          {askReset && (
            <ConfirmDialog title="Отменить несохранённые изменения?" confirmLabel="Сбросить" cancelLabel="Оставить правки"
              message={`Правки в ${dirtyTitles.length === 1 ? "разделе" : "разделах"} ${dirtyTitles.map((t) => `«${t}»`).join(", ")} будут отменены.`}
              onCancel={() => setAskReset(false)} onConfirm={() => { setAskReset(false); void reload(); }} />
          )}
          {error && <p className="error" role="alert">{error}</p>}
          <div className="settings__content">
          {!settings && section !== "about" && section !== "diagnostics" ? (
            error ? <EmptyState title="Настройки недоступны" /> : <Loading label="Загружаю настройки…" />
          ) : section === "recording" ? (
            <RecordingSection draft={draft} set={set} recordingsDir={recordingsDir} />
          ) : section === "sound" ? (
            <SoundSection draft={draft} set={set} devices={devices} endpoint={endpoint} />
          ) : section === "auto" ? (
            <AutoSection draft={draft} set={set} processes={processes} loadProcesses={loadProcesses}
              onToggle={(v) => void toggleAuto(v)} />
          ) : section === "asr" ? (
            <AsrSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint}
              onOpenEngine={() => setSection("engine")} />
          ) : section === "engine" ? (
            <>
              <p className="muted sdesc engine-choice-link">
                Выбор движка и моделей — в разделе{" "}
                <Button variant="link" onClick={() => setSection("asr")}>«Распознавание»</Button>.
                Здесь — установка движка и загрузка моделей.
              </p>
              {onRunWizard && (
                <Row label="Мастер первого запуска" hint="Пошаговая настройка: движок, токен Hugging Face, модели и запись">
                  <Button onClick={() => onRunWizard("hardware")}>Запустить мастер</Button>
                </Row>
              )}
              <EnginePane endpoint={endpoint} onReinstall={onRunWizard && (() => onRunWizard("engine"))} />
              <h3 className="shead">Модели</h3>
              <ModelsPane endpoint={endpoint} usage={modelUsage(draft)} />
            </>
          ) : section === "export" ? (
            <ExportSection draft={draft} set={set} endpoint={endpoint} />
          ) : section === "assistant" ? (
            <AssistantSection draft={draft} saved={settings ?? {}} set={set} endpoint={endpoint} />
          ) : section === "analysis" ? (
            <AnalysisSection draft={draft} set={set} />
          ) : section === "categories" ? (
            <CategoriesSection value={draft.categories} onChange={setCategories} endpoint={endpoint} />
          ) : section === "markup" ? (
            <MarkupSection draft={draft} set={set} />
          ) : section === "profiles" ? (
            <ProfilesSection draft={draft} set={set} endpoint={endpoint} />
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
    </div>
  );
}
