import { downloadDetail, easeToward, elapsedText, etaSeconds, etaText, jobFraction, stageText } from "./progress";

test("fraction: overall value first, then done/total, else unknown", () => {
  expect(jobFraction({ done: 1, total: 2, fraction: 0.37 })).toBe(0.37);
  expect(jobFraction({ done: 1, total: 4 })).toBe(0.25);
  expect(jobFraction({ done: null, total: null })).toBeNull();
  expect(jobFraction({ done: 3, total: 0 })).toBeNull();
  expect(jobFraction({ done: 5, total: 4 })).toBe(1);
});

test("stage text names the step and the count", () => {
  expect(stageText("выравнивание", 2, 4)).toBe("Этап 2 из 4 · Выравнивание");
  expect(stageText("загрузка модели")).toBe("Загрузка модели");
  expect(stageText("копирование", 1, 1)).toBe("Копирование");
});

test("eta: estimate at the start, measured pace later", () => {
  // Только оценка: 600 с работы, сделано 10 % — остаётся 540.
  expect(etaSeconds(0.1, 5, 600)).toBe(540);
  // Треть сделана за 100 с — замер весит целиком: ещё 200 с.
  expect(etaSeconds(1 / 3, 100, 600)).toBeCloseTo(200, 5);
  // Без оценки и в самом начале — молчим.
  expect(etaSeconds(0.02, 30, null)).toBeNull();
  expect(etaSeconds(null, 30, 600)).toBeNull();
  expect(etaSeconds(1, 30, 600)).toBeNull();
});

test("eta text", () => {
  expect(etaText(30)).toBe("осталось меньше минуты");
  expect(etaText(240)).toBe("осталось ~4 мин");
  expect(etaText(3600 + 600)).toBe("осталось ~1 ч 10 мин");
  expect(etaText(7200)).toBe("осталось ~2 ч");
  expect(etaText(null)).toBeNull();
  expect(elapsedText(150)).toBe("прошло 2 мин");
});

test("easing approaches and lands exactly on the target", () => {
  let x = 0;
  const seen: number[] = [];
  for (let i = 0; i < 200; i++) { x = easeToward(x, 0.5, 16); seen.push(x); }
  expect(seen.every((v, i) => i === 0 || v >= seen[i - 1]!)).toBe(true);
  expect(x).toBe(0.5);
  expect(easeToward(0.2, 0.5, 0)).toBe(0.2);
});

test("download detail: bytes and percent; nothing when the total is unknown", () => {
  expect(downloadDetail({ done: 1.2 * 1024 ** 3, total: 3.1 * 1024 ** 3 })).toBe("1,2 из 3,1 ГБ · 38 %");
  expect(downloadDetail({ done: 340 * 1024 ** 2, total: 900 * 1024 ** 2 })).toBe("340 из 900 МБ · 37 %");
  expect(downloadDetail({ done: 0, total: null })).toBeNull();
});
