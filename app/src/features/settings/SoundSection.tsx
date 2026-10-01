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
 */

import { useState, type ReactNode } from "react";
import { type DeviceCheck, type DeviceItem, type DeviceKind, type Devices, type Endpoint, testDevice } from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Row, type Raw, type SetFn } from "./Section";

/** Ниже этого пика считаем, что звука не было (шум тишины, а не речь). */
const SILENCE = 0.02;

function pickedName(value: unknown): string | null {
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
  return (
    <div role="group" aria-label={label}>
      <Row label={label} hint={hint} help={help} htmlFor={id}>
        <span className="sound__pick">
          <select id={id} className="sound__select" title={value ?? (system ? `Как в системе (сейчас: ${system})` : undefined)}
            value={value ?? ""}
            onChange={(e) => { setCheck(null); onChange(e.target.value || null); }}>
            <option value="">{system ? `Как в системе (сейчас: ${system})` : "Как в системе"}</option>
            {missing && <option value={value}>{`${value} (не подключено)`}</option>}
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </span>
        <Button onClick={() => void run()} disabled={checking}>{checking ? "Проверяю…" : "Проверить"}</Button>
        {(check || error) && (
          <span className="sound__result" aria-live="polite">
            {check && (
              <span role="meter" aria-label={`Уровень: ${label}`} aria-valuemin={0} aria-valuemax={100}
                aria-valuenow={percent} className="sound__level">
                <span className="sound__fill" style={{ width: `${percent}%` }} />
              </span>
            )}
            {check && <span className={check.peak < SILENCE || check.fallback ? "muted" : "notice"}>{resultText(kind, check)}</span>}
            {error && <span className="error">{error}</span>}
          </span>
        )}
      </Row>
    </div>
  );
}

export function SoundSection({ draft, set, devices, endpoint }: {
  draft: Raw; set: SetFn; devices: Devices | null; endpoint: Endpoint;
}) {
  const v = (k: string) => pickedName(draft.recording?.[k]);
  const choose = (key: string) => (name: string | null) => set("recording", key, name ? { name } : null);
  return (
    <>
      <p className="muted sdesc">
        Запись идёт двумя дорожками: ваш микрофон и звук собеседников. Изменения применятся со следующей записи.
      </p>
      {devices && !devices.available && (
        <p className="muted sdesc">Список устройств недоступен: {devices.error ?? "причина неизвестна"}</p>
      )}
      <DeviceRow id="sound-mic" kind="mic" label="Микрофон" endpoint={endpoint}
        hint="Ваш голос. «Как в системе» — микрофон Windows по умолчанию"
        items={devices?.inputs ?? []} value={v("mic_device")} onChange={choose("mic_device")} />
      <DeviceRow id="sound-output" kind="output" label="Звук собеседников (вывод)" endpoint={endpoint}
        hint="Устройство, через которое вы слышите собеседников: наушники или колонки"
        help={(
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
    </>
  );
}
