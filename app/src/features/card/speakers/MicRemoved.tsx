/**
 * «Убрано с микрофона: 6 дублей соседа, 3 эха · Показать» в панели «Спикеры»
 * (`mic_removed`, meet.mic_split): фразы, прозвучавшие дважды — сосед с
 * ноутбуком в том же звонке, эхо колонок, ваш голос через чужой ноутбук, —
 * оставлены один раз. Каждую убранную копию можно послушать (▶).
 */
import { useEffect, useId, useRef, useState } from "react";
import { Play } from "lucide-react";
import { clock } from "../../../lib/format";
import type { MicRemovedItem } from "../../../lib/types";
import { HelpTip, TipLine } from "../../../ui/HelpTip";
import { Icon } from "../../../ui/Icon";
import { removedSummary } from "../micSplit";

const REASON: Record<MicRemovedItem["reason"], string> = {
  neighbour: "дубль соседа",
  echo: "эхо",
  owner_leak: "ваш голос через звонок",
};

export function MicRemoved({ items, playable, onPlay, ask = 0 }: {
  items: MicRemovedItem[];
  playable: boolean;
  onPlay: (start: number, until: number) => void;
  /** Растёт с каждым «Показать» из карточки: список раскрыт и виден. */
  ask?: number;
}) {
  const [open, setOpen] = useState(ask > 0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ask) return;
    setOpen(true);
    box.current?.scrollIntoView?.({ block: "nearest" });
  }, [ask]);
  const counts: Record<string, number> = {};
  for (const it of items) counts[it.reason] = (counts[it.reason] ?? 0) + 1;
  const summary = removedSummary(counts);
  if (!items.length || !summary) return null;
  const fromMic = items.every((it) => it.track === "mic");
  return (
    <div className="spk-removed" role="group" aria-label="Убрано с микрофона" ref={box}>
      <div className="spk-removed__head">
        <span>{fromMic ? "Убрано с микрофона" : "Убраны повторы"}: {summary}</span>
        <HelpTip label="Что значит «Убрано с микрофона»" title="Повторы убраны">
          <TipLine>Эти фразы прозвучали в записи дважды: в микрофон и в звук собеседников.</TipLine>
          <TipLine>Так бывает, когда сосед в том же звонке сидит рядом с вами или звук звонка слышен в микрофоне.</TipLine>
          <TipLine>В расшифровке осталась одна копия — тому, кто говорил. Убранное можно послушать.</TipLine>
        </HelpTip>
        <span aria-hidden="true">·</span>
        <button type="button" className="spk-link" aria-expanded={open} aria-controls={listId}
          onClick={() => setOpen((v) => !v)}>{open ? "Скрыть" : "Показать"}</button>
      </div>
      {open && (
        <ul className="spk-row__phrases spk-removed__list" id={listId}>
          {items.map((it) => (
            <li key={`${it.track}:${it.start}`} className="spk-phrase">
              <button type="button" className="spk-phrase__play" disabled={!playable}
                aria-label={`Прослушать убранное с ${clock(it.start)}`}
                onClick={() => onPlay(it.start, Math.max(it.end, it.start + 1))}><Icon as={Play} size="sm" /></button>
              <span className="spk-phrase__time num muted">{clock(it.start)}</span>
              <span className="spk-phrase__text"><span className="muted spk-removed__why">{REASON[it.reason]}</span>{it.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
