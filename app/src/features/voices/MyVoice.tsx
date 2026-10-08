/**
 * «Мой голос» над таблицей «Голосов» — по макету MeetApp (VOICES): аватар «Вы»
 * 40 px · «Мой голос» и что записано · бейдж состояния · кнопки справа.
 *
 * Состояние и действия — те же, что у строки «Мой голос» в настройках «Звук»
 * (settings/OwnerVoice: `useOwnerVoice`, найденный голос, заметка о поиске);
 * запись образца — в окне «Мой голос» (settings/OwnerVoiceDialog), а не в
 * карточке: таблица под ней не прыгает.
 *
 * Недоступная кнопка объясняет причину подсказкой (ui/Tip), а если причина —
 * не установлено что-то нужное, рядом — «Открыть настройки» в нужный раздел.
 */

import { Ellipsis } from "lucide-react";
import { useRef, useState } from "react";
import type { Endpoint } from "../../lib/api";
import type { OwnerVoiceSample, OwnerVoiceStatus } from "../../lib/types";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { IconButton } from "../../ui/IconButton";
import { Tip } from "../../ui/Tip";
import { ItemMenu } from "../recordings/ItemMenu";
import { OwnerVoiceFound, deriveNote, sampleText, sentence, useOwnerVoice } from "../settings/OwnerVoice";
import { OwnerVoiceDialog } from "../settings/OwnerVoiceDialog";

/** Раздел настроек, где чинится причина «нельзя записать» (тексты резидента — owner_voice_control). */
export function settingsFor(reason: string | null | undefined): string | null {
  const r = (reason ?? "").toLowerCase();
  if (r.includes("движок")) return "engine";
  if (r.includes("токен")) return "speakers";
  if (r.includes("скачайте")) return "engine";
  return null;
}

/** Почему нельзя искать голос по прошлым встречам; null — можно. */
function deriveBlocked(status: OwnerVoiceStatus | null, error: string | null, busy: boolean): string | null {
  if (!status) return error ? "Служба записи не ответила" : "Проверяю образец голоса…";
  if (status.recording) return "Идёт запись встречи — поищите после неё.";
  if (!status.ready) return sentence(status.reason ?? "Сейчас искать нельзя");
  if (busy) return "Дождитесь конца записи образца.";
  return null;
}

/** Строка под «Мой голос»: что записано и зачем. */
function lead(samples: OwnerVoiceSample[]): string {
  if (!samples.length) {
    return "Запишите образец (~25 с) — Meet будет отличать вас от людей рядом за одним микрофоном. "
      + "Хранится только отпечаток голоса.";
  }
  const what = samples.map(sampleText).join("; ");
  return `Образец ${what}. Meet отличает вас от людей рядом за одним микрофоном. Хранится только отпечаток голоса.`;
}

export function MyVoice({ endpoint, onOpenSettings }: {
  endpoint: Endpoint;
  /** «Открыть настройки» рядом с причиной (нет — ссылки нет). */
  onOpenSettings?: (part: string) => void;
}) {
  const voice = useOwnerVoice(endpoint);
  const [recording, setRecording] = useState(false);
  const [menu, setMenu] = useState(false);
  const more = useRef<HTMLButtonElement>(null);
  const [confirmNode, confirm] = useConfirm();
  const status = voice.status;
  const samples = status?.samples ?? [];
  const enrolled = samples.some((s) => s.source === "enroll");
  const deriving = !!status?.derive?.running;
  const blocked = deriveBlocked(status, voice.error, voice.busy);
  const recordBlocked = !status ? (voice.error ? "Служба записи не ответила" : "Проверяю образец голоса…") : null;
  const fix = status && !status.ready ? settingsFor(status.reason) : null;
  const note = deriveNote(status);
  const failed = !deriving ? status?.derive?.error ?? null : null;

  const remove = async (s: OwnerVoiceSample) => {
    setMenu(false);
    if (s.source === "enroll" && !(await confirm({
      title: "Удалить записанный образец голоса?",
      message: "Расшифровка перестанет отличать ваш голос с этого микрофона от голосов людей рядом, "
        + "пока вы не запишете образец снова.",
      confirmLabel: "Удалить", cancelLabel: "Оставить",
    }))) return;
    await voice.remove(s.id);
  };

  return (
    <div className="card voices__mine" role="group" aria-label="Мой голос">
      <div className="voices__mine-row">
        <span className="voices__me" aria-hidden="true">Вы</span>
        <div className="voices__mine-text">
          <b className="voices__mine-title">Мой голос</b>
          <span className="voices__mine-lead">{lead(samples)}</span>
        </div>
        {status && (
          <span className={samples.length ? "badge badge--fresh" : "badge badge--plain"}>
            {samples.length ? "Записан" : "Не записан"}
          </span>
        )}
        <div className="voices__mine-actions">
          <Tip content={recordBlocked}>
            <Button disabled={!!recordBlocked} onClick={() => setRecording(true)}>
              {enrolled ? "Перезаписать" : "Записать"}
            </Button>
          </Tip>
          {deriving ? (
            status?.derive?.job && <Button variant="ghost" onClick={() => voice.stopDerive()}>Остановить поиск</Button>
          ) : (
            <Tip content={blocked ?? "Поискать ваш голос в последних звонках — без записи образца"}>
              <Button variant="ghost" disabled={!!blocked || voice.pending} onClick={() => voice.derive()}>
                Найти по прошлым встречам
              </Button>
            </Tip>
          )}
          {samples.length > 0 && (
            <IconButton ref={more} icon={Ellipsis} label="Ещё: мой голос" aria-haspopup="menu" aria-expanded={menu}
              onClick={() => setMenu((v) => !v)} />
          )}
        </div>
      </div>
      {deriving && <p className="voices__mine-note" aria-live="polite">Ищу ваш голос в последних встречах…</p>}
      {status && !status.ready && (
        <p className="voices__mine-note">
          {sentence(status.reason ?? "Записать образец сейчас нельзя")}
          {fix && onOpenSettings && (
            <>{" "}<Button variant="link" onClick={() => onOpenSettings(fix)}>Открыть настройки</Button></>
          )}
        </p>
      )}
      <OwnerVoiceFound endpoint={endpoint} voice={voice} />
      {note && <p className="voices__mine-note">{note}</p>}
      {failed && <p className="voices__mine-note voices__mine-note--err" role="alert">Поиск не удался: {failed}</p>}
      {voice.error && status && <p className="voices__mine-note voices__mine-note--err" role="alert">{voice.error}</p>}
      {menu && (
        <ItemMenu anchor={more} align="end" label="Мой голос"
          items={samples.map((s) => ({ label: `Удалить образец: ${sampleText(s)}`, danger: true,
            onSelect: () => void remove(s) }))}
          onClose={() => { setMenu(false); more.current?.focus(); }} />
      )}
      {recording && (
        <OwnerVoiceDialog endpoint={endpoint}
          onClose={() => { setRecording(false); void voice.reload(); }} />
      )}
      {confirmNode}
    </div>
  );
}
