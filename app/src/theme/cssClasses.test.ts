import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Все стили окна попадают в одну таблицу: блок, объявленный в двух файлах
 * (`.split` у кнопки «Начать запись» и у «Разделить спикера»), молча ломает
 * один из них — смотря какой файл загрузился позже. Корневой класс блока
 * объявляется ровно в одном файле.
 */
function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : [],
  );
}

test("a CSS block class is declared in one file only", () => {
  const src = join(process.cwd(), "src");
  const owners = new Map<string, Set<string>>();
  for (const file of cssFiles(src)) {
    const text = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of text.matchAll(/(?:^|})\s*([^{}@]+)\{/g)) {
      for (const part of (m[1] ?? "").split(",")) {
        const name = /^\s*\.([A-Za-z][\w-]*)\s*$/.exec(part)?.[1];
        if (!name) continue;
        const set = owners.get(name) ?? new Set<string>();
        set.add(relative(src, file));
        owners.set(name, set);
      }
    }
  }
  const clashes = [...owners].filter(([, files]) => files.size > 1).map(([name, files]) => `${name}: ${[...files].join(", ")}`);
  expect(clashes).toEqual([]);
});
