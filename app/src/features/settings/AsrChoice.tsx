/**
 * «Распознавание»: чем распознаётся речь — устройство, а для видеокарты и для
 * процессора — свой движок (Whisper или GigaAM) и своя модель. Единственное
 * место выбора; установка движка и загрузка моделей — «Движок и модели».
 *
 * Ключи настроек прежние: `asr.device`, `asr.backend` + `asr.model`
 * (видеокарта), `asr.cpu_backend` + `asr.cpu_model` (процессор),
 * `asr.gigaam_model` (одна на оба устройства), `asr.language`.
 *
 * Какая строка работает сейчас — по черновику устройства и по тому, годится
 * ли видеокарта (`/engine`: `cuda_ok`, `cuda_reason` — то же правило, что у
 * `asr.resolve_device`, включая движок профиля CPU).
 */

import { useEffect, useId, useState, type ReactNode } from "react";
import {
  type Endpoint, type EngineState, type Model, type ModelsState, GIGAAM_PREFIX, getEngine, getModels, isGigaam,
} from "../../lib/api";
import { Button } from "../../ui/Button";
import { Select, type SelectOption } from "../../ui/Select";
import { fieldClass } from "./fields";
import { Row, Segmented, type Raw, type SetFn } from "./Section";

export type AsrDevice = "cuda" | "cpu";
type Backend = "faster-whisper" | "gigaam";

/** Модели GigaAM, если каталог не загрузился (имя — без префикса «gigaam/»). */
const GIGAAM_FALLBACK = [
  { name: "v3_e2e_rnnt", title: "GigaAM v3 — русский" },
  { name: "v3_e2e_ctc", title: "GigaAM v3 CTC — русский" },
];
const DEFAULT_GIGAAM = "v3_e2e_rnnt";
const OTHER = "\u0000other";
/** Ширина списка модели GigaAM в строке: под самое длинное название. */
const SELECT_WIDTH = 260;

const KEYS: Record<AsrDevice, { backend: string; model: string; fallback: Backend }> = {
  cuda: { backend: "backend", model: "model", fallback: "faster-whisper" },
  cpu: { backend: "cpu_backend", model: "cpu_model", fallback: "gigaam" },
};
const TITLE: Record<AsrDevice, string> = { cuda: "Видеокарта", cpu: "Процессор" };

/** Движок устройства из черновика; «whisper.cpp» (задел, не реализован) — как Whisper. */
export function backendOf(asr: Raw[string] | undefined, device: AsrDevice): Backend {
  const raw = asr?.[KEYS[device].backend];
  return raw === "gigaam" ? "gigaam" : raw ? "faster-whisper" : KEYS[device].fallback;
}

/** Годится ли видеокарта: null — неизвестно (состояние движка не загрузилось). */
export type GpuState = { ok: boolean; reason: string | null; name: string | null } | null;

export function gpuState(engine: EngineState | null): GpuState {
  if (!engine) return null;
  // Старый резидент без cuda_ok: судим по тому, видна ли карта.
  const ok = engine.cuda_ok ?? engine.gpu.available;
  return { ok, reason: ok ? null : engine.cuda_reason ?? "видеокарта NVIDIA не найдена", name: engine.gpu.name };
}

/** Какая строка распознаёт при этом выборе устройства; null — не знаем («Авто» без состояния движка). */
export function deviceInEffect(device: string, gpu: GpuState): AsrDevice | null {
  if (device === "cpu") return "cpu";
  if (gpu === null) return device === "cuda" ? "cuda" : null;
  return gpu.ok ? "cuda" : "cpu";
}

function nowLine(device: string, gpu: GpuState, saved: boolean): string {
  const when = saved ? "Сейчас" : "После сохранения";
  const effect = deviceInEffect(device, gpu);
  if (effect === null) return `${when}: видеокарта, если она доступна, иначе процессор — состояние движка не загрузилось`;
  if (effect === "cuda") return `${when}: видеокарта${gpu?.name ? ` (${gpu.name})` : ""}`;
  if (device === "cpu") return `${when}: процессор`;
  return `${when}: процессор — видеокарта недоступна (${gpu?.reason})`;
}

/** Пояснение у строки, которая сейчас не распознаёт. */
function secondaryNote(row: AsrDevice, device: string, gpu: GpuState): string {
  if (row === "cuda") {
    if (gpu === null && device !== "cpu") return "Используется, если видеокарта доступна";
    return "Используется, если выбрать устройство «Видеокарта» или «Авто»";
  }
  if (device === "cuda") return "Используется, если выбрать устройство «Процессор»";
  return "Запасной вариант, если видеокарта недоступна";
}

const whisperModels = (models: ModelsState | null) =>
  (models?.items ?? []).filter((m) => m.kind === "asr" && !isGigaam(m));

const modelTitle = (models: ModelsState | null, id: string) =>
  models?.items.find((m) => m.id === id)?.title ?? id;

/** Пункт списка моделей: название и справа серым — скачана ли. */
function modelOption(m: Model): SelectOption {
  return { value: m.id, label: m.title, detail: m.downloaded ? "скачана" : `${m.size_gb} ГБ, не скачана` };
}

/**
 * Модель Whisper: список из каталога («скачана» / размер) и «Другая…» — id
 * модели Hugging Face или путь к папке. Каталог не загрузился — только поле.
 */
function WhisperPicker({ id, label, hint, value, models, disabled, onChange }: {
  id: string; label: string; hint?: string; value: string; models: ModelsState | null; disabled?: boolean;
  onChange: (v: string) => void;
}) {
  const options = whisperModels(models);
  const known = options.some((m) => m.id === value);
  const [other, setOther] = useState(false);
  const showOther = options.length === 0 || other || !known;
  return (
    <Row label={label} hint={hint} htmlFor={id} stack disabled={disabled}>
      <div className="asr-model">
        {options.length > 0 && (
          <Select id={id} size="sm" width="100%" value={showOther ? OTHER : value} disabled={disabled}
            options={[...options.map(modelOption), { value: OTHER, label: "Другая…", detail: "id или папка" }]}
            onChange={(v) => {
              if (v === OTHER) { setOther(true); return; }
              setOther(false);
              onChange(v);
            }} />
        )}
        {showOther && (
          <input type="text" className={fieldClass({ wide: true, mono: true })} spellCheck={false} disabled={disabled}
            id={options.length === 0 ? id : undefined}
            aria-label={options.length === 0 ? undefined : `${label}: id модели или папка`}
            placeholder="id модели на Hugging Face или путь к папке" value={value}
            onChange={(e) => onChange(e.target.value)} />
        )}
      </div>
    </Row>
  );
}

function DeviceRow({ device, draft, set, models, gpu, active, otherUsesGigaam }: {
  device: AsrDevice; draft: Raw; set: SetFn; models: ModelsState | null; gpu: GpuState;
  /** Эта строка распознаёт при выбранном устройстве. */
  active: boolean;
  otherUsesGigaam: boolean;
}) {
  const uid = useId();
  const [showFallback, setShowFallback] = useState(false);
  const asr = draft.asr ?? {};
  const backend = backendOf(asr, device);
  const whisper = String(asr[KEYS[device].model] ?? "");
  const gigaam = String(asr.gigaam_model ?? DEFAULT_GIGAAM);
  const language = String(asr.language ?? "ru").trim().toLowerCase() || "ru";
  const unavailable = device === "cuda" && gpu !== null && !gpu.ok;
  const setWhisper = (v: string) => set("asr", KEYS[device].model, v);

  const gigaamOptions = models?.items.filter(isGigaam).map((m) => ({
    name: m.id.slice(GIGAAM_PREFIX.length), title: m.title,
  })) ?? [];
  const gigaamList = gigaamOptions.length > 0 ? gigaamOptions : GIGAAM_FALLBACK;
  const fallbackId = `${uid}-fallback`;
  const state = unavailable ? "disabled" : active ? "active" : "secondary";

  return (
    <section className={`asr-device asr-device--${state}`} role="group" aria-labelledby={`${uid}-title`}
      data-state={state}>
      <div className="asr-device__head">
        <span id={`${uid}-title`} className="asr-device__title">{TITLE[device]}</span>
        {active && !unavailable && <span className="badge badge--fresh">используется</span>}
      </div>
      {(unavailable || !active) && (
        <p className="asr-device__note">
          {unavailable ? `Недоступна: ${gpu?.reason}` : secondaryNote(device, String(asr.device ?? "auto"), gpu)}
        </p>
      )}
      <Segmented label="Движок" value={backend} disabled={unavailable}
        options={[
          { value: "faster-whisper", label: "Whisper" },
          { value: "gigaam", label: "GigaAM" },
        ]}
        onChange={(x) => set("asr", KEYS[device].backend, x)} />
      {backend === "faster-whisper" ? (
        <WhisperPicker id={`asr-model-${device}`} label="Модель Whisper" value={whisper} models={models}
          disabled={unavailable} onChange={setWhisper} />
      ) : (
        <>
          <Row label="Модель GigaAM" htmlFor={`asr-gigaam-${device}`} disabled={unavailable}
            hint={otherUsesGigaam ? "Одна модель GigaAM для видеокарты и процессора" : undefined}>
            <Select id={`asr-gigaam-${device}`} size="sm" width={SELECT_WIDTH} value={gigaam} disabled={unavailable}
              options={gigaamList.map((m) => ({ value: m.name, label: m.title }))}
              onChange={(v) => set("asr", "gigaam_model", v)} />
          </Row>
          <p className="asr-fallback">
            {language === "ru" || language === "auto"
              ? "Записи не на русском распознаёт Whisper: "
              : `Язык речи «${language}»: GigaAM его не понимает, распознаёт Whisper: `}
            <span className="asr-fallback__model">{modelTitle(models, whisper)}</span>{" "}
            <Button variant="link" disabled={unavailable} aria-expanded={showFallback} aria-controls={fallbackId}
              onClick={() => setShowFallback((v) => !v)}>
              {showFallback ? "скрыть" : "изменить"}
            </Button>
          </p>
          {showFallback && (
            <div id={fallbackId}>
              <WhisperPicker id={`asr-model-${device}`} label="Модель Whisper для записей не на русском"
                value={whisper} models={models} disabled={unavailable} onChange={setWhisper} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** Устройство, строки видеокарты и процессора. Состояние движка и каталог моделей — свои запросы. */
export function AsrChoice({ draft, saved, set, endpoint, help, onOpenEngine }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint; help?: ReactNode;
  /** Перейти в «Движок и модели» (скачать модели). */
  onOpenEngine?: () => void;
}) {
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [engineTried, setEngineTried] = useState(false);
  const [models, setModels] = useState<ModelsState | null>(null);
  useEffect(() => {
    let alive = true;
    void getEngine(endpoint).then((e) => { if (alive) setEngine(e); }, () => {})
      .finally(() => { if (alive) setEngineTried(true); });
    void getModels(endpoint).then((m) => { if (alive) setModels(m); }, () => {});
    return () => { alive = false; };
  }, [endpoint]);

  const asr = draft.asr ?? {};
  const device = String(asr.device ?? "auto");
  const gpu = gpuState(engine);
  const effect = deviceInEffect(device, gpu);
  const isSaved = device === String(saved.asr?.device ?? "auto");
  return (
    <>
      <Segmented label="Устройство" help={help} value={device}
        hint={<>
          Авто — видеокарта, если она доступна, иначе процессор
          {(engineTried || device !== "auto") && <span className="asr-now" role="status">{nowLine(device, gpu, isSaved)}</span>}
        </>}
        options={[
          { value: "auto", label: "Авто" },
          { value: "cuda", label: "Видеокарта" },
          { value: "cpu", label: "Процессор" },
        ]}
        onChange={(v) => set("asr", "device", v)} />
      <p className="muted sdesc asr-intro">
        GigaAM понимает только русский, зато распознаёт в несколько раз быстрее. Whisper понимает и другие языки,
        точнее пишет английские термины и учитывает список терминов распознавания.
        {onOpenEngine && <>{" "}Скачать и удалить модели можно в разделе{" "}
          <Button variant="link" onClick={onOpenEngine}>«Движок и модели»</Button>.</>}
      </p>
      {(["cuda", "cpu"] as const).map((d) => (
        <DeviceRow key={d} device={d} draft={draft} set={set} models={models} gpu={gpu} active={effect === d}
          otherUsesGigaam={backendOf(asr, d === "cuda" ? "cpu" : "cuda") === "gigaam"} />
      ))}
    </>
  );
}
