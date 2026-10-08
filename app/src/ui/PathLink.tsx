/**
 * Путь в ответе ассистента (0.5): щелчок — открыть файл его программой, значок
 * папки — показать в Проводнике (Finder). Ошибка оболочки («этот файл не
 * открывается») — строкой рядом, а не тихо.
 */

import { FolderOpen } from "lucide-react";
import { useState } from "react";

import { errorText } from "../lib/format";
import { IconButton } from "./IconButton";
import "./path-link.css";

/** Что делать с путём: открыть и показать в папке (оболочка, `open_user_path`). */
export type PathActions = { open: (path: string) => Promise<void>; reveal: (path: string) => Promise<void> };

/** Похоже на путь к файлу на этом компьютере: `C:\…`, `~/…`, `/Users/…`. */
export const looksLikePath = (text: string) =>
  /^(?:[A-Za-z]:[\\/]|~[\\/]|\/(?:Users|home|Volumes|tmp|opt|var)\/)[^\n<>"|?*]+$/.test(text.trim());

/** Пути в простом тексте — без пробелов; точка, запятая и скобка в конце — знаки препинания. */
const PATH_IN_TEXT = /(?:[A-Za-z]:\\|~\/|\/(?:Users|home|Volumes)\/)[^\s"'<>|*?,;]+/g;

/** Кусочки текста: строки и пути (по порядку). */
export function splitPaths(text: string): (string | { path: string })[] {
  const out: (string | { path: string })[] = [];
  let at = 0;
  for (const m of text.matchAll(PATH_IN_TEXT)) {
    let path = m[0];
    while (/[.)\]:]$/.test(path)) path = path.slice(0, -1);
    const start = m.index ?? 0;
    if (start > at) out.push(text.slice(at, start));
    out.push({ path });
    at = start + path.length;
  }
  if (at < text.length) out.push(text.slice(at));
  return out;
}

export function PathLink({ path, actions }: { path: string; actions: PathActions }) {
  const [error, setError] = useState<string | null>(null);
  const run = (fn: (p: string) => Promise<void>) => {
    setError(null);
    fn(path).catch((e) => setError(errorText(e)));
  };
  return (
    <span className="path-link">
      <button type="button" className="path-link__open" aria-label={`Открыть ${path}`} onClick={() => run(actions.open)}>
        {path}
      </button>
      <IconButton icon={FolderOpen} size="xs" label={`Показать в папке ${path}`} tooltip="Показать в папке"
        className="path-link__reveal" onClick={() => run(actions.reveal)} />
      {error && <span className="path-link__error" role="alert">{error}</span>}
    </span>
  );
}
