/**
 * Готов ли агент во вкладке «Агент» принять вставку ссылки после запуска,
 * который начала сама просьба «Спросить агента» (agentSessions): поле ввода
 * на экране, нет диалога, экран затих. Проверено на настоящем выводе Claude
 * Code и Codex (AgentTab.coldstart.test.ts, fixtures/).
 */

import type { Terminal } from "@xterm/xterm";

/**
 * Срок от щелчка ✦ (не сдвигается): дальше в полосе — «Вставить ссылку» и
 * «Копировать», что бы ни было на экране. Готовность после срока всё равно
 * вставляет ссылку сама.
 */
export const PASTE_WAIT_MS = 15_000;
/** Вывод на экран агента затих на столько — экран дорисован, можно вставлять. */
export const QUIET_MS = 800;
/** Как часто смотреть на экран, пока ссылка ждёт вставки. */
export const POLL_MS = 100;

/**
 * Диалог агента, куда вставлять нельзя: первый запуск в папке, вход,
 * обновление. Проверяется только при запуске, который начала сама просьба, и
 * только пока сеанс ещё ни разу не был готов — по всему экрану: оба агента
 * рисуют диалог вверху (строки 2–19), и на высоком терминале внизу его не
 * видно. Фразы — точные, из самих программ (строки в claude.exe и codex.exe и
 * их вывод, 2026-10), без общих слов вроде «login to» или «(y/n)», что бывают
 * и в разговоре:
 * Claude Code — «Accessing workspace: … Quick safety check: Is this a project
 * you created or one you trust?», «Yes, I trust this folder», выбор темы и
 * способа входа; прежние версии — «Do you trust the files in this folder?».
 * Codex (0.159) — «Trust this folder? Codex can read, edit, and run files
 * here…», «Continue only if you trust these files», экран входа: «Sign in with
 * ChatGPT», «Provide your own API key», и вопрос об обновлении «1. Update now
 * (runs …) 2. Skip 3. Skip until next version» — цифра из вставленной ссылки
 * выбирает в нём пункт (проверено: вставка запустила обновление); прежние
 * версии — «Allow Codex to work in this folder».
 */
export const CONFIRM_SCREEN = new RegExp([
  "quick safety check", "is this a project you created or one you trust", "yes, i trust this folder",
  "do you trust the files in this folder", "trust this folder[?] codex", "continue only if you trust these files",
  "sign in with chatgpt", "provide your own api key",
  "allow codex to work in this folder", "choose the text style that looks best", "select login method",
  "update now [(]runs", "skip until next version",
].join("|"), "i");

/**
 * Поле ввода агента на экране — без него вставлять некуда. Оба агента
 * включают режим вставки (ESC[?2004h) сразу при старте, ещё до диалога и до
 * поля ввода, и потом молчат до полутора секунд (Claude Code — пока готовит
 * сеанс, Codex — пока решает, спрашивать ли о папке): вставка в эту паузу
 * пропадала. Признаки — из их вывода (claude.exe 2.1.288, codex.exe 0.159):
 * Claude Code — «❯» и неразрывный пробел (U+00A0) в рамке из «────»; в его
 * диалогах «❯» — указатель выбора, после него обычный пробел, рамки сверху нет.
 * Codex — «›» в начале строки, но не пункт выбора («› 1. Trust and continue»,
 * «› 1. Update now»). Прежний вид поля Claude Code — «│ >» в рамке «╭───╮».
 */
export function promptVisible(rows: string[]): boolean {
  return rows.some((row, i) => {
    if (/^\s*[❯›>]\u00a0/.test(row)) return true;
    if (/^\s*›(?!\s*\d+[.)])(?:\s|$)/.test(row)) return true;
    return /^\s*(?:│\s*)?[❯>](?:\s|$)/.test(row) && /^\s*[─╭]─{7,}/.test(rows[i - 1] ?? "");
  });
}

/**
 * Заголовок окна, который поставил сам агент, а не псевдоконсоль (та в начале
 * ставит путь к программе: «C:\…\codex.exe»). Codex ставит свой (имя папки
 * встречи, со спиннером, пока стартуют MCP), только когда сеанс начался, —
 * после вопроса о папке, а не в паузе перед ним, когда поле ввода уже видно.
 */
export function ownTitle(title: string): boolean {
  return title.trim() !== "" && !title.includes("\\");
}

/** Вывод, который меняет экран: один заголовок окна (спиннер Codex) — не в счёт. */
// eslint-disable-next-line no-control-regex
const TITLE_ONLY = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
export function screenOutput(data: string): boolean {
  return data.replace(TITLE_ONLY, "") !== "";
}

/** На экране диалог агента (CONFIRM_SCREEN). `text` — экран одной строкой (`screenText`). */
export function dialogShown(text: string): boolean {
  return CONFIRM_SCREEN.test(text.replace(/\s+/g, " "));
}

/**
 * Готов ли агент, который ещё ни разу не был готов, принять вставку (тишину
 * считает сеанс): «confirm» — на экране диалог (`dialogShown`); «ready» —
 * режим вставки включён и видно поле ввода (`promptVisible`), у Codex ещё и
 * свой заголовок окна (`ownTitle`); иначе — «waiting». `text` — экран одной
 * строкой с перенесёнными строками вместе (`screenText`); нет — строки `rows`.
 */
export function coldReadiness(
  rows: string[],
  { bracketed, provider, titled, text }: { bracketed: boolean; provider: string | null; titled: boolean; text?: string },
): "ready" | "confirm" | "waiting" {
  if (dialogShown(text ?? rows.join(" "))) return "confirm";
  if (!bracketed || !promptVisible(rows)) return "waiting";
  if (provider === "codex" && !titled) return "waiting";
  return "ready";
}

/** Строки экрана, который рисует агент (а не того места, куда прокрутил человек), — текстом. */
export function screenRows(t: Pick<Terminal, "rows" | "buffer">): string[] {
  try {
    const buf = t.buffer.active;
    const lines: string[] = [];
    for (let i = buf.baseY; i < buf.baseY + t.rows; i++) {
      lines.push(buf.getLine(i)?.translateToString(true) ?? "");
    }
    return lines;
  } catch {
    return [];
  }
}

/**
 * Экран одной строкой — для фраз диалогов: строка, перенесённая терминалом
 * (узкое окно), склеивается с предыдущей без пробела, остальные — через
 * пробел; пробелы схлопывает `dialogShown` (агенты сами переносят текст на
 * новую строку с отступом).
 */
export function screenText(t: Pick<Terminal, "rows" | "buffer">): string {
  try {
    const buf = t.buffer.active;
    let text = "";
    for (let i = buf.baseY; i < buf.baseY + t.rows; i++) {
      const line = buf.getLine(i);
      if (!line) continue;
      const wrappedNext = buf.getLine(i + 1)?.isWrapped === true && i + 1 < buf.baseY + t.rows;
      const part = line.translateToString(!wrappedNext);
      text += line.isWrapped ? part : ` ${part}`;
    }
    return text;
  } catch {
    return "";
  }
}
