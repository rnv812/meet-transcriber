/**
 * «Автозапись»: переключатель (применяется сразу, мимо черновика — `POST
 * /auto-record`), программы и браузеры звонков, ожидание повторного
 * подключения (ползунок 1–60 минут) и минимальная длительность. Всё, кроме
 * переключателя, резидент читает при старте — честно говорим о перезапуске.
 */

import type { Processes } from "../../lib/api";
import { BrowserCalls } from "./BrowserCalls";
import { CallPrograms } from "./CallPrograms";
import { MinutesSlider, SecondsRow } from "./fields";
import { SettingsCard, Switch, type Raw, type SetFn } from "./Section";
import { AutoRecordTip, GraceTip } from "./tips";

export function AutoSection({ draft, set, processes, loadProcesses, onToggle }: {
  draft: Raw; set: SetFn; processes: Processes | null; loadProcesses: () => Promise<Processes>;
  onToggle: (v: boolean) => void;
}) {
  const v = (k: string) => draft.auto_record?.[k];
  const selected = (v("processes") as string[] | undefined) ?? [];
  return (
    <>
      <SettingsCard title="Когда записывать">
        <Switch label="Записывать звонки автоматически"
          hint="Запись начинается со звонком и останавливается после его окончания. Применяется сразу, без «Сохранить»"
          help={<AutoRecordTip />}
          value={Boolean(v("enabled"))} onChange={onToggle} />
        <p className="muted sdesc">Параметры ниже применяются после перезапуска приложения.</p>
      </SettingsCard>
      <SettingsCard title="Программы и браузеры">
        <CallPrograms value={selected} processes={processes} loadProcesses={loadProcesses}
          onChange={(x) => set("auto_record", "processes", x)} />
        <BrowserCalls browsers={(v("browsers") as string[] | undefined) ?? []}
          requireSite={Boolean(v("browser_require_site"))} sites={(v("call_sites") as string[] | undefined) ?? []}
          onBrowsers={(x) => set("auto_record", "browsers", x)}
          onRequireSite={(x) => set("auto_record", "browser_require_site", x)}
          onSites={(x) => set("auto_record", "call_sites", x)} />
      </SettingsCard>
      <SettingsCard title="Конец звонка">
        <MinutesSlider id="grace" label="Ждать повторного подключения" min={1} max={60}
          hint="Запись остановится, если за это время вы не вернётесь в звонок" help={<GraceTip />}
          value={Number(v("grace_minutes") ?? 10)} onChange={(x) => set("auto_record", "grace_minutes", x)} />
        <SecondsRow id="min-call" label="Минимальная длительность звонка"
          hint="Более короткие записи сохраняются, но не расшифровываются автоматически"
          value={Number(v("min_call_seconds") ?? 0)} onChange={(x) => set("auto_record", "min_call_seconds", x)} />
      </SettingsCard>
    </>
  );
}
