/**
 * Микрофон звонка по голосам (`mic_split`, с 0.3.3): строка в карточке.
 *
 * Разделение не вышло — почему микрофон подписан целиком вами и что с этим
 * сделать. Нет образца голоса (или он не узнал вас) — заметная строка
 * `OwnerVoiceNudge`: «Записать образец» сразу открывает окно записи с текстом
 * для чтения (v037: на встрече за одним ноутбуком сосед иначе тоже «Вы»).
 * Прочее (мало речи, ошибка, Hugging Face) — тихой строкой. Вышло и убрало
 * повторы — сколько и где их посмотреть: реплики, видные в тексте до
 * спикеров, исчезают с окончательной расшифровкой не молча.
 */
import type { Endpoint } from "../../lib/api";
import { plural } from "../../lib/format";
import type { MicSplitInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import { NUDGE_TEXT, OwnerVoiceNudge } from "../settings/OwnerVoiceDialog";
import { Callout } from "./Callout";

/** Раздел настроек: образец голоса и токен Hugging Face — оба в «Спикерах» (0.4). */
type Hint = { text: string; action?: "speakers"; button?: string; record?: boolean };

const RECORD = { action: "speakers" as const, button: "Записать образец", record: true };

/** Подсказка по статусу; `diarization` — пометка пропущенной диаризации (её баннер уже про HF). */
export function micSplitHint(info: Pick<MicSplitInfo, "status"> | null | undefined,
  diarization?: string | null): Hint | null {
  switch (info?.status) {
    case "no_profile":
      // Без доступа к HF образец не записать (та же модель) — баннер уже про Hugging Face.
      if (diarization?.startsWith("skipped_")) return null;
      return { text: "Микрофон не разделён на голоса: без образца вашего голоса он весь подписан вами, "
        + "и люди рядом с вами — тоже", ...RECORD };
    case "owner_not_found":
      return { text: "Ваш голос на микрофоне не найден: образец записан с другим микрофоном или в шуме, "
        + "поэтому весь микрофон подписан вами", ...RECORD };
    case "no_voice":
      return { text: "На микрофоне слишком мало речи, чтобы различать голоса: он подписан вами целиком" };
    case "skipped_error":
      return { text: "Голоса на микрофоне не разобраны из-за ошибки (подробности — в журнале): "
        + "он подписан вами целиком" };
    case "skipped_no_token":
      if (diarization?.startsWith("skipped_")) return null;
      return { text: "Голоса на микрофоне не разобраны: нет доступа к модели Hugging Face", action: "speakers",
        button: "Настроить" };
    default:
      return null;
  }
}

/** «6 дублей соседа, 3 эха» — что убрано, по причинам; ничего — пустая строка. */
export function removedSummary(dropped: MicSplitInfo["dropped"] | null | undefined): string {
  const n = (k: string) => {
    const v = dropped?.[k];
    return typeof v === "number" && v > 0 ? v : 0;
  };
  const parts: string[] = [];
  const neighbour = n("neighbour"), echo = n("echo"), leak = n("owner_leak");
  if (neighbour) parts.push(`${neighbour} ${plural(neighbour, "дубль", "дубля", "дублей")} соседа`);
  if (echo) parts.push(`${echo} ${plural(echo, "эхо", "эха", "эха")}`);
  if (leak) parts.push(`${leak} ${plural(leak, "ваша фраза", "ваши фразы", "ваших фраз")} через звонок`);
  return parts.join(", ");
}

export function MicSplitNote({ info, diarization, endpoint, onOpenSettings, onShowRemoved }: {
  info: MicSplitInfo | null | undefined;
  diarization?: string | null;
  /** Резидент: «Записать образец» открывает окно записи сразу (без него — настройки). */
  endpoint?: Endpoint;
  onOpenSettings?: (section: string) => void;
  /** «Показать»: панель «Спикеры» со списком убранного. */
  onShowRemoved?: () => void;
}) {
  const hint = micSplitHint(info, diarization);
  const removed = removedSummary(info?.dropped);
  const nudge = hint?.record && endpoint
    ? <OwnerVoiceNudge endpoint={endpoint} lead={`${hint.text}.`} className="card__note" /> : null;
  const quiet = nudge ? null : hint;
  if (!quiet && !removed) return nudge;
  return (
    <>
      {nudge}
      <Callout tone="note" role="note">
        <span className="mic-note">
          {quiet && (
            <span className="mic-note__line">
              <span>{quiet.record ? `${quiet.text}. ${NUDGE_TEXT}` : quiet.text}</span>
              {quiet.action && quiet.button && onOpenSettings && (
                <Button variant="link" onClick={() => onOpenSettings(quiet.action!)}>{quiet.button}</Button>
              )}
            </span>
          )}
          {removed && (
            <span className="mic-note__line">
              <span>{(info?.dropped?.owner_leak ?? 0) > 0 ? "Убраны повторы" : "С микрофона убраны повторы"}: {removed}</span>
              {onShowRemoved && <Button variant="link" onClick={onShowRemoved}>Показать</Button>}
            </span>
          )}
        </span>
      </Callout>
    </>
  );
}
