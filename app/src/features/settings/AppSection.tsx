/**
 * «Приложение» (0.4): запуск вместе с системой, уведомления, имя в
 * расшифровке, расшифровка сразу после записи, папка записей (прежний раздел
 * «Запись») и мастер первого запуска (прежде — в «Движке и моделях»).
 */

import { inTauri, openFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { AutostartRow } from "./AutostartRow";
import { TextRow } from "./fields";
import { PathText, Row, Segmented, SettingsCard, Switch, type Raw, type SetFn } from "./Section";

export function AppSection({ draft, set, recordingsDir, onRunWizard }: {
  draft: Raw; set: SetFn; recordingsDir: string | null;
  /** «Запустить мастер» (без него строки мастера нет). */
  onRunWizard?: () => void;
}) {
  const v = (k: string) => draft.recording?.[k];
  return (
    <>
      <SettingsCard title="Запуск и уведомления">
        <AutostartRow />
        <Segmented label="Уведомления" value={(draft.ui?.notifications as "all" | "important" | "off") ?? "all"}
          hint="«Только важные» — начало и конец записи, готовая расшифровка и ошибки, без промежуточных шагов"
          options={[
            { value: "all", label: "Все" },
            { value: "important", label: "Только важные" },
            { value: "off", label: "Выключены" },
          ]}
          onChange={(x) => set("ui", "notifications", x)} />
      </SettingsCard>
      <SettingsCard title="Записи">
        <TextRow id="speaker-name" label="Ваше имя в расшифровке" short
          hint="Так подписываются реплики, записанные с вашего микрофона"
          value={String(v("speaker_name") ?? "Вы")} onChange={(x) => set("recording", "speaker_name", x)} />
        <Switch label="Расшифровывать сразу после записи" value={Boolean(v("auto_transcribe"))}
          hint="Включено — расшифровка встаёт в очередь, как только запись остановлена; выключено — по кнопке «Расшифровать» в карточке"
          onChange={(x) => set("recording", "auto_transcribe", x)} />
        <Row label="Папка записей" hint="Здесь хранятся записи встреч. Путь задаётся в файле настроек">
          {recordingsDir ? (
            <span className="folder">
              <PathText path={recordingsDir} />
              {inTauri() && <Button onClick={() => void openFolder(recordingsDir)}>Открыть</Button>}
            </span>
          ) : <span className="muted">Неизвестно</span>}
        </Row>
      </SettingsCard>
      {onRunWizard && (
        <SettingsCard title="Первая настройка">
          <Row label="Мастер первого запуска" hint="Пошаговая настройка: движок, токен Hugging Face, модели и запись">
            <Button onClick={onRunWizard}>Запустить мастер</Button>
          </Row>
        </SettingsCard>
      )}
    </>
  );
}
