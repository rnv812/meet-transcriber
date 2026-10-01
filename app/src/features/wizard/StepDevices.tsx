/**
 * Шаг 5 «Запись»: какие устройства слушаем (показ), автозапись звонков и
 * программы, по которым её поднимать. Всё сохраняется сразу — тем же путём,
 * что и в настройках.
 */

import { useEffect, useState } from "react";
import {
  type Devices, type Endpoint, getDevices, getProcesses, getSettings, patchSettings, setAutoRecord,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Switch } from "../settings/Section";

export function StepDevices({ endpoint, onNext }: { endpoint: Endpoint; onNext: () => void }) {
  const [devices, setDevices] = useState<Devices | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [running, setRunning] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getDevices(endpoint).then((d) => { if (live) setDevices(d); }).catch(() => {});
    getProcesses(endpoint).then((p) => { if (live) setRunning(p.running ?? []); }).catch(() => {});
    getSettings(endpoint).then((s) => {
      const auto = s.auto_record as { enabled?: unknown; processes?: unknown } | undefined;
      if (!live) return;
      setEnabled(auto?.enabled === true);
      setSelected(Array.isArray(auto?.processes) ? auto.processes.map(String) : []);
    }).catch((cause) => { if (live) setError(errorText(cause)); });
    return () => { live = false; };
  }, [endpoint]);

  const toggleAuto = async (value: boolean) => {
    try {
      await setAutoRecord(endpoint, value);
      setEnabled(value);
      setError(null);
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  const toggleProcess = async (name: string) => {
    const next = selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name];
    try {
      await patchSettings(endpoint, { auto_record: { processes: next } });
      setSelected(next);
      setError(null);
    } catch (cause) {
      setError(errorText(cause));
    }
  };

  const names = [...new Set([...running, ...selected])].sort();
  return (
    <>
      <p className="muted">
        Пишем две дорожки: звук компьютера (собеседники) и микрофон (вы). Следим за устройствами по
        умолчанию — сменили наушники, запись продолжится.
      </p>
      <div className="wizard__devices">
        {devices?.available ? (
          <>
            <code className="path">звук: {devices.system?.name}</code>
            <code className="path">микрофон: {devices.mic?.name}</code>
          </>
        ) : <span className="muted">{devices?.error ?? "устройства не определены"}</span>}
      </div>
      <Switch label="Поднимать запись, когда начинается звонок" hint="кончился звонок — запись останавливается сама"
        value={enabled} onChange={(v) => void toggleAuto(v)} />
      <div className="wizard__apps">
        <span>Программы звонков</span>
        <span className="wizard__hint">
          отметьте те, в которых идут звонки; применится после перезапуска приложения
        </span>
        <div className="checks wizard__checks">
          {names.length === 0 && <span className="muted">нет запущенных программ</span>}
          {names.map((n) => (
            <label key={n} className="checks__item">
              <input type="checkbox" checked={selected.includes(n)} onChange={() => void toggleProcess(n)} />
              {n}
            </label>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="wizard__bar">
        <Button variant="primary" onClick={onNext}>Далее</Button>
      </div>
    </>
  );
}
