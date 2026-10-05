/**
 * Микрофон звонка по голосам (`mic_split`, с 0.3.3): тихая строка в карточке.
 *
 * Разделение не вышло — почему микрофон подписан целиком вами и что с этим
 * сделать (записать образец голоса, настроить Hugging Face). Вышло и убрало
 * повторы — сколько и где их посмотреть: реплики, видные в тексте до
 * спикеров, исчезают с окончательной расшифровкой не молча.
 */
import { plural } from "../../lib/format";
import type { MicSplitInfo } from "../../lib/types";
import { Button } from "../../ui/Button";

type Hint = { text: string; action?: "sound" | "engine"; button?: string };

const RECORD = { action: "sound" as const, button: "Записать образец" };

/** Подсказка по статусу; `diarization` — пометка пропущенной диаризации (её баннер уже про HF). */
export function micSplitHint(info: Pick<MicSplitInfo, "status"> | null | undefined,
  diarization?: string | null): Hint | null {
  switch (info?.status) {
    case "no_profile":
      // Без доступа к HF образец не записать (та же модель) — баннер уже про Hugging Face.
      if (diarization?.startsWith("skipped_")) return null;
      return { text: "Запишите образец голоса — тогда люди рядом с вами получат свои подписи. "
        + "Пока весь микрофон подписан вами", ...RECORD };
    case "owner_not_found":
      return { text: "Ваш голос на микрофоне не найден: образец записан с другим микрофоном или в шуме. "
        + "Весь микрофон подписан вами — запишите образец с этим микрофоном", ...RECORD };
    case "no_voice":
      return { text: "На микрофоне слишком мало речи, чтобы различать голоса: он подписан вами целиком" };
    case "skipped_error":
      return { text: "Голоса на микрофоне не разобраны из-за ошибки (подробности — в журнале): "
        + "он подписан вами целиком" };
    case "skipped_no_token":
      if (diarization?.startsWith("skipped_")) return null;
      return { text: "Голоса на микрофоне не разобраны: нет доступа к модели Hugging Face", action: "engine",
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

export function MicSplitNote({ info, diarization, onOpenSettings, onShowRemoved }: {
  info: MicSplitInfo | null | undefined;
  diarization?: string | null;
  onOpenSettings?: (section: string) => void;
  /** «Показать»: панель «Спикеры» со списком убранного. */
  onShowRemoved?: () => void;
}) {
  const hint = micSplitHint(info, diarization);
  const removed = removedSummary(info?.dropped);
  if (!hint && !removed) return null;
  return (
    <div className="muted card__note mic-note" role="note">
      {hint && (
        <span className="mic-note__line">
          <span>{hint.text}</span>
          {hint.action && hint.button && onOpenSettings && (
            <Button variant="link" size="sm" onClick={() => onOpenSettings(hint.action!)}>{hint.button}</Button>
          )}
        </span>
      )}
      {removed && (
        <span className="mic-note__line">
          <span>{(info?.dropped?.owner_leak ?? 0) > 0 ? "Убраны повторы" : "С микрофона убраны повторы"}: {removed}</span>
          {onShowRemoved && <Button variant="link" size="sm" onClick={onShowRemoved}>Показать</Button>}
        </span>
      )}
    </div>
  );
}
