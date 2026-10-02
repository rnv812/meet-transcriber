import {
  AGENT_INTENTS, REF_CAP, REF_TEXT_MAX, agentPrompt, cleanRefText, flatPrompt, joinPrompts, pasteLine, plainMarkdown,
} from "./agentRef";

test("ссылка на одну реплику: время, спикер, текст в кавычках; в конце — новая строка для вопроса", () => {
  expect(agentPrompt({ refs: [{ t: 65, speaker: "Анна", text: "Сдаём отчёт в пятницу." }] }))
    .toBe("Про реплику:\n[01:05] Анна: «Сдаём отчёт в пятницу.»\n");
});

test("несколько реплик — «Про реплики:», больше десяти — «…и ещё N»", () => {
  const refs = Array.from({ length: REF_CAP + 3 }, (_, i) => ({ t: i * 60, speaker: "Олег", text: `пункт ${i + 1}` }));
  const text = agentPrompt({ refs });
  const lines = text.trimEnd().split("\n");
  expect(lines[0]).toBe("Про реплики:");
  expect(lines).toHaveLength(1 + REF_CAP + 1);
  expect(lines[1]).toBe("[00:00] Олег: «пункт 1»");
  expect(lines.at(-1)).toBe("…и ещё 3");
});

test("намерение — первой строкой, с точкой, если её нет; без новой строки в конце", () => {
  const ref = { t: 3725, speaker: "Демьян", text: "Бюджет согласован." };
  expect(agentPrompt({ refs: [ref], intent: "Объясни" }))
    .toBe("Объясни.\nПро реплику:\n[1:02:05] Демьян: «Бюджет согласован.»");
  expect(agentPrompt({ refs: [ref], intent: "Что из этого следует?" }).split("\n")[0]).toBe("Что из этого следует?");
  expect(AGENT_INTENTS).toEqual(["Объясни", "Что из этого следует?", "Сформулируй задачу", "Проверь по базе знаний"]);
});

test("пункты итогов и подсказки — свои заголовки; без времени — только текст", () => {
  expect(agentPrompt({ kind: "summary", refs: [{ text: "Перенести релиз", section: "Решения" }] }))
    .toBe("Про пункт итогов:\n«Перенести релиз» (раздел «Решения»)\n");
  expect(agentPrompt({ kind: "summary", refs: [{ text: "а" }, { text: "б" }] }).split("\n")[0]).toBe("Про пункты итогов:");
  expect(agentPrompt({ kind: "hint", refs: [{ t: 30, speaker: "Риск", text: "Нет ответственного" }] }))
    .toBe("Про подсказку ассистента:\n[00:30] Риск: «Нет ответственного»\n");
});

test("текст ссылки очищен: без управляющих символов и escape-последовательностей, переводы строк — пробелы", () => {
  const dirty = "начало\x1b[31mкрасный\x1b[0m\r\nвторая\tстрока\x07\x00\u009b2J\u202eконец\x1b]0;title\x07 хвост";
  const clean = cleanRefText(dirty);
  expect(clean).toBe("началокрасный вторая строка2Jконец хвост");
  // eslint-disable-next-line no-control-regex
  expect(/[\x00-\x1f\x7f-\x9f]/.test(clean)).toBe(false);
  // Конец «вставки» (ESC[201~) внутри текста не закрывает вставку раньше времени.
  const prompt = agentPrompt({ refs: [{ t: 0, speaker: "А\x1b[201~\rБ", text: "x\x1b[201~\ry" }] });
  expect(prompt).toBe("Про реплику:\n[00:00] А Б: «x y»\n");
});

test("длинный текст обрезается до предела с многоточием", () => {
  const long = "слово ".repeat(200);
  const line = agentPrompt({ refs: [{ t: 0, speaker: "Анна", text: long }] }).split("\n")[1]!;
  const quoted = line.slice(line.indexOf("«") + 1, line.lastIndexOf("»"));
  expect(quoted.length).toBeLessThanOrEqual(REF_TEXT_MAX);
  expect(quoted.endsWith("…")).toBe(true);
});

test("пустые ссылки пропускаются; совсем пусто — пустая строка", () => {
  expect(agentPrompt({ refs: [{ text: "  \x1b[0m " }] })).toBe("");
  expect(agentPrompt({ refs: [{ text: "" }, { t: 1, speaker: "Анна", text: "да" }] }))
    .toBe("Про реплику:\n[00:01] Анна: «да»\n");
});

test("flatPrompt — одной строкой (когда вставка не в режиме bracketed paste)", () => {
  const text = agentPrompt({ refs: [{ t: 1, speaker: "Анна", text: "да" }, { t: 2, speaker: "Олег", text: "нет" }] });
  expect(flatPrompt(text)).toBe("Про реплики: [00:01] Анна: «да» · [00:02] Олег: «нет» ");
  expect(flatPrompt("Объясни.\nПро реплику:\n[00:01] Анна: «да»")).toBe("Объясни. Про реплику: [00:01] Анна: «да»");
});

test("эмодзи на границе обрезки не разрезается пополам (одинокий суррогат сломал бы agent_write)", () => {
  const text = "а".repeat(REF_TEXT_MAX - 2) + "🙂🙂🙂";
  const line = agentPrompt({ refs: [{ text }] }).split("\n")[1]!;
  const quoted = line.slice(1, line.lastIndexOf("»"));
  expect(Array.from(quoted)).toHaveLength(REF_TEXT_MAX);
  expect(quoted.endsWith("🙂…")).toBe(true);
  expect(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(quoted)).toBe(false);
});

test("pasteLine: одна строка без \\r, \\n и ESC — и с намерением, и для чужого текста", () => {
  const withIntent = agentPrompt({ refs: [{ t: 1, speaker: "Анна", text: "да" }, { t: 2, speaker: "Олег", text: "нет" }],
    intent: "Объясни" });
  const line = pasteLine(withIntent);
  expect(line).toBe("Объясни. Про реплики: [00:01] Анна: «да» · [00:02] Олег: «нет»");
  // eslint-disable-next-line no-control-regex
  const controls = /[\x00-\x1f\x7f-\x9f]/;
  expect(controls.test(line)).toBe(false);
  // Текст не через agentPrompt (будущие вызовы) — всё равно без Enter и escape-последовательностей.
  const raw = pasteLine("строка\r\nвторая\x1b[201~\rтретья\x07");
  expect(controls.test(raw)).toBe(false);
  expect(raw).toBe("строка вторая третья");
});

test("joinPrompts: несколько просьб — не больше REF_CAP ссылок в сумме, последние целиком", () => {
  const block = (n: number, who: string) =>
    agentPrompt({ refs: Array.from({ length: n }, (_, i) => ({ t: i, speaker: who, text: `т${i}` })) });
  expect(joinPrompts([block(1, "А"), block(1, "Б")])).toBe(`${block(1, "А")}${block(1, "Б")}`);
  const joined = joinPrompts([block(6, "А"), block(3, "Б"), block(5, "В")]);
  const lines = joined.trimEnd().split("\n");
  expect(lines[0]).toBe("…и ещё 6 раньше");
  expect(lines.filter((l) => l.startsWith("[")).length).toBe(8);
  expect(joined).not.toContain("А: ");
  expect(pasteLine(joined)).not.toMatch(/\s{2,}/);
});

test("plainMarkdown: текст пункта без разметки", () => {
  expect(plainMarkdown("**Анна** — подготовить `отчёт` к [пятнице](x) ~~давно~~ \\*")).toBe("Анна — подготовить отчёт к пятнице давно *");
});

test("глава и наблюдение: заголовок, строка «о чём», ссылки с ограничением", () => {
  const refs = [0, 1, 2, 3].map((k) => ({ t: 60 * k, speaker: "Анна", text: `Реплика ${k}` }));
  expect(agentPrompt({ kind: "chapter", about: "Глава 2 «Бюджет», 01:00–04:00", refs, cap: 2 })).toBe([
    "Про главу встречи:", "Глава 2 «Бюджет», 01:00–04:00",
    "[00:00] Анна: «Реплика 0»", "[01:00] Анна: «Реплика 1»", "…и ещё 2", "",
  ].join("\n"));
  // Строка «о чём» очищается, как и ссылки; наблюдение без реплик — тоже просьба.
  expect(agentPrompt({ kind: "insight", about: "Риск:\x1b[2J срок\nсорван", refs: [] }))
    .toBe("Про наблюдение анализа встречи:\nРиск: срок сорван\n");
  expect(agentPrompt({ kind: "insight", about: "  ", refs: [] })).toBe("");
});
