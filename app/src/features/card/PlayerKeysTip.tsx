/** «?» с клавишами плеера: в строке плеера и в настройках «Подсветка и разметка». */

import { HelpTip, TipLine } from "../../ui/HelpTip";
import { IS_MAC } from "../../lib/platform";

export function PlayerKeysTip() {
  return (
    <HelpTip label="Клавиши плеера" title="Клавиши плеера">
      <TipLine>Пробел или K — пуск и пауза, J и L — на 10 секунд назад и вперёд, ← и → — на 5 секунд.</TipLine>
      <TipLine>Shift+← и Shift+→ — к предыдущей и следующей главе, {IS_MAC ? "⌥←" : "Ctrl+←"} и {IS_MAC ? "⌥→" : "Ctrl+→"} — к соседней реплике.</TipLine>
      <TipLine>M — выключить или включить звук, цифры 0–9 — перейти к 0–90 % записи.</TipLine>
      <TipLine>Клавиши не работают, пока курсор в поле ввода или в терминале агента.</TipLine>
    </HelpTip>
  );
}
