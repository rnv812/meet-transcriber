/**
 * «Звук»: какой микрофон и какое устройство вывода пишет запись.
 *
 * «Как в системе» (null) — запись следует за устройствами Windows по умолчанию
 * и переживает их смену; конкретное устройство хранится по имени
 * (`recording.mic_device` / `output_device` = {"name"}). Нет его при старте —
 * запись идёт с системного, и резидент сообщает об этом.
 *
 * «Проверить» пишет ~2 с подпроцессом резидента (`POST /devices/test`) и
 * показывает пиковый уровень; проверяется выбор из черновика, ещё не сохранённый.
 *
 * «Мой голос» (образец голоса владельца) — в «Спикерах» (0.4); микрофон для него — отсюда.
 */

import { useState, type ReactNode } from "react";
import { type DeviceCheck, type DeviceItem, type DeviceKind, type Devices, type Endpoint, testDevice } from "../../lib/api";
import { errorText } from "../../lib/format";
import { IS_MAC, OS_TEXT } from "../../lib/platform";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Select, type SelectOption } from "../../ui/Select";
import { Row, SeeAlso, SettingsCard, type Raw, type SetFn } from "./Section";

/** Ниже этого пика считаем, что звука не было (шум тишины, а не речь). */
const SILENCE = 0.02;

/** Имя выбранного устройства (`{name}` или строка прежнего формата); null — «Как в системе». */
export function pickedName(value: unknown): string | null {
  if (value && typeof value === "object" && typeof (value as { name?: unknown }).name === "string") {
    return (value as { name: string }).name || null;
  }
  return typeof value === "string" && value ? value : null;
}

function resultText(kind: DeviceKind, check: DeviceCheck): string {
  if (check.fallback) return `Выбранное устройство не найдено — проверено системное: ${check.device}`;
  if (check.peak < SILENCE) {
    return kind === "mic"
      ? "Звука нет. Скажите что-нибудь во время проверки и повторите."
      : "Звука нет. Включите любой звук (например, видео) и повторите проверку.";
  }
  return `Звук есть: ${check.device}`;
}

function DeviceRow({ id, kind, label, hint, help, items, value, onChange, endpoint }: {
  id: string; kind: DeviceKind; label: string; hint: string; help?: ReactNode;
  items: DeviceItem[]; value: string | null; onChange: (name: string | null) => void; endpoint: Endpoint;
}) {
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<DeviceCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const system = items.find((d) => d.default)?.name;
  const names = items.map((d) => d.name);
  const missing = value !== null && !names.includes(value);

  const run = async () => {
    setChecking(true);
    setCheck(null);
    setError(null);
    try {
      setCheck(await testDevice(endpoint, kind, value));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setChecking(false);
    }
  };

  const percent = check ? Math.round(Math.min(1, Math.max(0, check.peak)) * 100) : 0;
  const options: SelectOption[] = [
    { value: "", label: system ? `Как в системе (сейчас: ${system})` : "Как в системе" },
    ...(missing && value !== null ? [{ value, label: `${value} (не подключено)` }] : []),
    ...names.map((n) => ({ value: n, label: n })),
  ];
  return (
    <div role="group" aria-label={label}>
      <Row label={label} hint={hint} help={help} htmlFor={id} stack>
        <span className="sound__pick">
          <Select id={id} size="sm" width="100%" value={value ?? ""} options={options}
            onChange={(v) => { setCheck(null); onChange(v || null); }} />
          {/* Уровень — между списком и кнопкой: до проверки полоски серые, после — до пика. */}
          <LevelBars label={label} percent={check ? percent : null} kind={kind} quiet={!!check && check.peak < SILENCE} />
          <Button onClick={() => void run()} busy={checking}>Проверить</Button>
        </span>
        {/* Итог проверки — в строке постоянной высоты: появление не сдвигает разделы ниже. */}
        <span className="sound__result" aria-live="polite">
          {check && <span className={check.peak < SILENCE || check.fallback ? "muted" : "notice"}>{resultText(kind, check)}</span>}
          {error && <span className="error">{error}</span>}
        </span>
      </Row>
    </div>
  );
}

/** Полосок в индикаторе уровня. */
const BARS = 16;
/** Высоты полосок (доля от 32 px): неровная «волна», чтобы индикатор читался как звук, а не как полоса загрузки. */
const BAR_SHAPE = Array.from({ length: BARS }, (_, i) => 0.3 + 0.6 * Math.abs(Math.sin((i + 1) * 0.9)) * (0.55 + 0.45 * ((i * 7) % 5) / 4));

/**
 * Пиковый уровень проверки — полосками, как уровни дорожек «Идёт запись»:
 * закрашены полоски до уровня (микрофон — `--data-4`, вывод — `--data-1`),
 * остальные — `--data-empty`. До проверки (`percent` null) — только серые
 * полоски, для диктора их нет.
 */
function LevelBars({ label, percent, kind, quiet }: {
  label: string; percent: number | null; kind: DeviceKind; quiet: boolean;
}) {
  const lit = percent === null ? 0 : Math.round((percent / 100) * BARS);
  const meter = percent === null ? { "aria-hidden": true } : {
    role: "meter", "aria-label": `Уровень: ${label}`, "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-valuenow": percent, "aria-valuetext": `${percent} %`,
  };
  return (
    <span {...meter} className={`sound__bars sound__bars--${kind}${quiet ? " sound__bars--quiet" : ""}`}>
      {BAR_SHAPE.map((h, i) => (
        <i key={i} className={i < lit ? "is-lit" : undefined} style={{ height: `${Math.round(h * 100)}%` }} />
      ))}
    </span>
  );
}

export function SoundSection({ draft, set, devices, endpoint, onOpenSpeakers }: {
  draft: Raw; set: SetFn; devices: Devices | null; endpoint: Endpoint;
  /** Перейти в «Спикеры» (образец голоса); без него ссылки нет. */
  onOpenSpeakers?: () => void;
}) {
  const v = (k: string) => pickedName(draft.recording?.[k]);
  const choose = (key: string) => (name: string | null) => set("recording", key, name ? { name } : null);
  return (
    <>
      {onOpenSpeakers && (
        <SeeAlso head>
          Образец вашего голоса — в разделе{" "}
          <Button variant="link" onClick={onOpenSpeakers}>«Спикеры»</Button>.
        </SeeAlso>
      )}
      <SettingsCard title="Устройства">
        <p className="muted sdesc">
          Запись идёт двумя дорожками: ваш микрофон и звук собеседников. Изменения применятся со следующей записи.
        </p>
        {devices && !devices.available && (
          <p className="muted sdesc">Список устройств недоступен: {devices.error ?? "причина неизвестна"}</p>
        )}
        <DeviceRow id="sound-mic" kind="mic" label="Микрофон" endpoint={endpoint}
          hint={`Ваш голос. «Как в системе» — микрофон ${OS_TEXT.systemName} по умолчанию`}
          items={devices?.inputs ?? []} value={v("mic_device")} onChange={choose("mic_device")} />
        <DeviceRow id="sound-output" kind="output" label="Звук собеседников (вывод)" endpoint={endpoint}
          hint={IS_MAC
            ? "Системный звук (ScreenCaptureKit) или виртуальное устройство, например BlackHole"
            : "Устройство, через которое вы слышите собеседников: наушники или колонки"}
          help={IS_MAC ? (
            <HelpTip label="Как записывается звук собеседников" title="Запись звука собеседников">
              <TipLine>
                «Системный звук» записывается через ScreenCaptureKit: нужно разрешение «Запись экрана» —
                Системные настройки → Конфиденциальность и безопасность → Запись экрана (в macOS 15 — «Запись
                экрана и системного звука»), включите Meet. Изображение экрана не сохраняется.
              </TipLine>
              <TipLine>
                Без этого разрешения выберите виртуальное устройство ввода, например BlackHole, и направьте в него
                звук звонка (через «Устройство с несколькими выходами» в «Настройке Audio-MIDI»).
              </TipLine>
            </HelpTip>
          ) : (
            <HelpTip label="Как записывается звук собеседников" title="Запись звука собеседников">
              <TipLine>
                Голоса собеседников записываются с выбранного устройства вывода через WASAPI loopback: программа
                получает копию звука, который Windows отправляет в наушники или колонки.
              </TipLine>
              <TipLine>Другие звуки компьютера (уведомления, музыка) тоже попадут в эту дорожку.</TipLine>
              <TipLine>
                «Как в системе» — запись перейдёт на новое устройство, если вы смените его во время звонка.
              </TipLine>
            </HelpTip>
          )}
          items={devices?.outputs ?? []} value={v("output_device")} onChange={choose("output_device")} />
      </SettingsCard>
    </>
  );
}
