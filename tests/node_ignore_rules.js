// Проверка правил запрета Claude Code на настоящей node-ignore (ей CLI и
// сопоставляет Read-правила). Путь — как в коде прав claude 2.1.292:
//   Nfo: `\(`→`(`, `\)`→`)`, затем `\\`→`\`  (разбор текста правила);
//   `//d/rest` → корень `D:\`, шаблон `/rest` (Windows) или `/…` от `/`;
//   `X/**` в запрете → `X` (Gn); ignore().add(шаблон).test(путь от корня).
// Вход — JSON в argv[3]: [{rule, inside: [...], outside: [...]}], пути от
// корня через `/`. Выход — JSON: [{rule, pattern, inside: [bool], outside: [bool]}].
const ignore = require(process.argv[2]);

function nfo(e) {
  return e.replaceAll("\\(", "(").replaceAll("\\)", ")").replaceAll("\\\\", "\\");
}

function pattern(content) {
  let s = content.slice(1);                 // `//d/x` → `/d/x`
  if (/^\/[a-z]\//i.test(s)) s = s.slice(2); // корень диска → `/x`
  if (s.endsWith("/**")) s = s.slice(0, -3);
  return s.replace(/\/{2,}/g, "/");
}

const cases = JSON.parse(process.argv[3]);
const out = cases.map((c) => {
  const p = pattern(nfo(c.rule));
  const ig = ignore().add(p);
  return {
    rule: c.rule,
    pattern: p,
    inside: c.inside.map((x) => ig.test(x).ignored),
    outside: c.outside.map((x) => ig.test(x).ignored),
  };
});
process.stdout.write(JSON.stringify(out));
