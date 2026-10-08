/**
 * «Дополнительно»: команда после записи (`hooks`), маркер занятости
 * видеокарты (`integrations.gpu_marker*`) и, с 0.4, запуск агента во вкладке
 * «Агент» (`agent.launch`, прежде — в «Ассистенте»).
 */

import { AgentLaunchSection } from "./AgentLaunchSection";
import { TextRow } from "./fields";
import { Row, SettingsCard, Switch, type Raw, type SetFn } from "./Section";
import { GpuMarkerTip, HookCommandTip, RecurringWindowTip } from "./tips";

export function AdvancedSection({ draft, set }: { draft: Raw; set: SetFn }) {
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
      <SettingsCard title="Команда после записи">
        <Switch label="Запускать команду после записи"
          hint="Когда запись остановлена и сохранена; у записи с ассистентом ассистент дописывает ленту и сводку после неё"
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
      </SettingsCard>
      <SettingsCard title="Интеграции">
        <Switch label="Сообщать другим программам о занятости видеокарты" help={<GpuMarkerTip />}
          hint="На время расшифровки создаётся файл-маркер"
          value={markerOn} onChange={(x) => set("integrations", "gpu_marker", x)} />
        <TextRow id="gpu-marker-path" label="Путь к файлу-маркеру" placeholder="По умолчанию" wide disabled={!markerOn}
          hint={markerOn ? "Если не задан — gpu.lock в папке данных приложения"
            : "Включите сообщение о занятости видеокарты, чтобы задать путь"}
          value={String(draft.integrations?.gpu_marker_path ?? "")}
          onChange={(x) => set("integrations", "gpu_marker_path", x || null)} />
      </SettingsCard>
      <SettingsCard title="Запуск агента (вкладка «Агент»)">
        <AgentLaunchSection draft={draft} set={set} />
      </SettingsCard>
    </>
  );
}
