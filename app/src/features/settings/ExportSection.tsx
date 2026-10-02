/**
 * Настройки «Экспорт встреч»: куда и как встреча выгружается в базу знаний
 * (Obsidian и т. п.). На встречу — своя папка по шаблону имени внутри папки
 * для встреч, в ней — выбранные файлы.
 *
 * Пример имени папки считает резидент (`GET /export/preview`) по несохранённым
 * значениям — на последней записи библиотеки. Запрос — с паузой после ввода и
 * только пока раздел открыт: окно не опрашивает резидент без дела.
 *
 * Проверка шаблона здесь повторяет `meet.kb_export.check_folder_template`
 * (тексты те же): «Сохранить» недоступно сразу, не дожидаясь ответа резидента.
 */

import { useEffect, useState } from "react";
import { type Endpoint, getExportPreview } from "../../lib/api";
import { errorText } from "../../lib/format";
import type { ExportPreview } from "../../lib/types";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { FolderRow, Row, Switch, type Raw, type SetFn } from "./Section";

export const PREVIEW_DELAY_MS = 400;

export const TOKENS: { token: string; text: string }[] = [
  { token: "{date}", text: "дата встречи: 2026-09-30" },
  { token: "{time}", text: "время начала: 10-15" },
  { token: "{year}", text: "год: 2026" },
  { token: "{month}", text: "месяц: 09" },
  { token: "{day}", text: "день: 30" },
  { token: "{title}", text: "название встречи" },
];
const KNOWN = new Set(TOKENS.map((t) => t.token.slice(1, -1)));
const TEXT_KEYS = ["folder_template", "transcript_name", "summary_name"];
const FLAGS: { key: string; label: string }[] = [
  { key: "include_transcript", label: "Расшифровка" },
  { key: "include_summary", label: "Итоги встречи" },
  { key: "include_srt", label: "Субтитры (SRT)" },
  { key: "include_audio", label: "Аудиозапись" },
];

function tokenError(text: string): string | null {
  for (const match of text.matchAll(/\{([^{}]*)\}/g)) {
    const name = match[1] ?? "";
    if (!KNOWN.has(name)) {
      return `Неизвестная подстановка {${name}}. Доступны: ${TOKENS.map((t) => t.token).join(", ")}`;
    }
  }
  return null;
}

/** Почему шаблон папки не подходит, или null. */
export function folderTemplateError(template: string): string | null {
  const text = template.trim();
  if (!text) return "Шаблон папки не может быть пустым";
  if (text.startsWith("/") || text.startsWith("\\")) return "Шаблон папки должен быть относительным — без «/» в начале";
  if (/^[A-Za-z]:/.test(text)) return "Шаблон папки не может содержать букву диска";
  const parts = text.split(/[\\/]/);
  if (parts.some((p) => !p.trim())) return "В шаблоне папки есть пустая часть пути — уберите лишнюю «/»";
  if (parts.some((p) => p.trim() === ".." || p.trim() === ".")) return "В шаблоне папки нельзя использовать «..» и «.»";
  return tokenError(text);
}

/** Почему имя файла не подходит, или null. */
export function fileNameError(name: string): string | null {
  const text = name.trim();
  if (!text) return "Имя файла не может быть пустым";
  if (/[\\/]/.test(text)) return "Имя файла не может содержать «/» или «\\»";
  return tokenError(text);
}

const mdName = (name: string) => {
  const text = name.trim();
  return (text.toLowerCase().endsWith(".md") ? text : `${text}.md`).toLowerCase();
};

/** Имена расшифровки и итогов совпадают друг с другом или с субтитрами/записью — текст, иначе null. */
export function fileNamesClash(transcriptName: string, summaryName: string): string | null {
  const transcript = mdName(transcriptName);
  const summary = mdName(summaryName);
  if (transcript === summary) return "Имена файлов расшифровки и итогов совпадают — задайте разные";
  const names: [string, string][] = [["расшифровки", transcript], ["итогов", summary]];
  for (const [which, name] of names) {
    const stem = name.slice(0, -3);
    if (stem === "запись") return `Имя файла ${which} совпадает с именем записи «Запись» — выберите другое`;
    if (stem === "субтитры" || stem === "субтитры.srt") {
      return `Имя файла ${which} совпадает с именем субтитров «Субтитры.srt» — выберите другое`;
    }
  }
  return null;
}

/**
 * Правки раздела нельзя сохранить: шаблон или имя файла вне правил. Смотрим
 * только изменённое; имена — в паре с сохранённым (`saved`).
 */
export function exportChangesInvalid(changes: Raw, saved: Raw = {}): boolean {
  const e = changes.export ?? {};
  const names = "transcript_name" in e || "summary_name" in e;
  const pair = (k: string) => String(e[k] ?? saved.export?.[k] ?? "");
  return (typeof e.folder_template === "string" && folderTemplateError(e.folder_template) !== null)
    || (typeof e.transcript_name === "string" && fileNameError(e.transcript_name) !== null)
    || (typeof e.summary_name === "string" && fileNameError(e.summary_name) !== null)
    || (names && fileNamesClash(pair("transcript_name"), pair("summary_name")) !== null);
}

/** Значение настройки, каким оно уйдёт в PATCH: шаблон и имена файлов — без пробелов по краям. */
export function cleanSetting(group: string, key: string, value: unknown): unknown {
  return group === "export" && TEXT_KEYS.includes(key) && typeof value === "string" ? value.trim() : value;
}

/** «?» со списком подстановок. */
function TokenHelp() {
  return (
    <HelpTip label="Подстановки в шаблоне" title="Подстановки в имени папки и файлов">
      {TOKENS.map((t) => (
        <TipLine key={t.token}><code>{t.token}</code> — {t.text}</TipLine>
      ))}
      <TipLine>«/» в шаблоне создаёт вложенные папки: <code>{"{year}/{date} - {title}"}</code></TipLine>
    </HelpTip>
  );
}

export function ExportSection({ draft, set, endpoint }: { draft: Raw; set: SetFn; endpoint: Endpoint }) {
  const v = (k: string) => draft.export?.[k];
  const template = String(v("folder_template") ?? "");
  const transcriptName = String(v("transcript_name") ?? "");
  const summaryName = String(v("summary_name") ?? "");
  const flags = Object.fromEntries(FLAGS.map((f) => [f.key, Boolean(v(f.key))]));
  const local = folderTemplateError(template) ?? fileNameError(transcriptName) ?? fileNameError(summaryName)
    ?? fileNamesClash(transcriptName, summaryName);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Пример — с паузой после ввода; негодный шаблон резиденту не отправляем.
  const query = JSON.stringify({ folder_template: template, transcript_name: transcriptName, summary_name: summaryName, ...flags });
  useEffect(() => {
    if (local) return;
    let live = true;
    const timer = setTimeout(() => {
      getExportPreview(endpoint, JSON.parse(query) as Record<string, string | boolean>)
        .then((data) => { if (live) { setPreview(data); setPreviewError(null); } })
        .catch((e) => { if (live) setPreviewError(errorText(e)); });
    }, PREVIEW_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [endpoint, query, local]);

  const problem = local ?? preview?.error ?? null;

  return (
    <>
      <p className="muted sdesc">
        Для каждой встречи в базе знаний создаётся отдельная папка с расшифровкой и итогами.
      </p>
      <FolderRow label="Папка для встреч" hint="Папка в вашей базе знаний (например, в хранилище Obsidian), куда выгружаются встречи"
        value={(v("meetings_dir") as string | null | undefined) ?? null}
        onChange={(x) => set("export", "meetings_dir", x)} />
      <Row label="Шаблон имени папки" htmlFor="export-template" hint="Имя папки встречи; «/» создаёт вложенные папки"
        help={<TokenHelp />} stack>
        <input id="export-template" type="text" value={template}
          onChange={(e) => set("export", "folder_template", e.target.value)} />
        <span className="export__preview" aria-live="polite">
          {problem ? <span className="error">{problem}</span>
            : previewError ? <span className="muted">Пример недоступен: {previewError}</span>
              : preview?.folder ? (
                <>
                  <span className="muted">Пример: {preview.folder}</span>
                  {preview.files.length > 0 && <span className="muted">В папке: {preview.files.join(", ")}</span>}
                </>
              ) : null}
        </span>
      </Row>
      <Row label="Имя файла расшифровки" htmlFor="export-transcript-name" hint="Те же подстановки; расширение .md добавляется автоматически">
        <input id="export-transcript-name" type="text" value={transcriptName}
          onChange={(e) => set("export", "transcript_name", e.target.value)} />
      </Row>
      <Row label="Имя файла итогов" htmlFor="export-summary-name" hint="Те же подстановки; расширение .md добавляется автоматически">
        <input id="export-summary-name" type="text" value={summaryName}
          onChange={(e) => set("export", "summary_name", e.target.value)} />
      </Row>
      <Row label="Что выгружать" hint="Итоги — если они уже подготовлены; две дорожки записи сводятся в одну">
        <div className="checks">
          {FLAGS.map((f) => (
            <label key={f.key} className="checks__item">
              <input type="checkbox" checked={flags[f.key]} onChange={() => set("export", f.key, !flags[f.key])} />
              {f.label}
            </label>
          ))}
        </div>
      </Row>
      <Switch label="Выгружать автоматически после расшифровки"
        hint="Выгрузка обновляется, когда готовы итоги. Действует, если задана папка для встреч. Изменённые вами файлы не перезаписываются"
        value={Boolean(v("auto_export"))} onChange={(x) => set("export", "auto_export", x)} />
    </>
  );
}
