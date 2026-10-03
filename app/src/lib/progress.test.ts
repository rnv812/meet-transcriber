import {
  clockText, downloadDetail, easeToward, elapsedText, etaSeconds, etaText, EXTRAPOLATE_TOP, extrapolate, jobEta,
  jobFraction, MAX_LEAD, maxAheadMs, stageText, velocity,
} from "./progress";

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
  expect(etaText(30)).toBe("осталось ~30 с");
  expect(etaText(41)).toBe("осталось ~40 с");
  expect(etaText(5)).toBe("осталось несколько секунд");
  expect(etaText(70)).toBe("осталось ~1 мин");
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

test("elapsed clock", () => {
  expect(clockText(7)).toBe("0:07");
  expect(clockText(72.9)).toBe("1:12");
  expect(clockText(3723)).toBe("1:02:03");
});

test("model job: own eta when confident, nothing when slow or unsure", () => {
  expect(jobEta({ phase: "generating", eta_s: 41 }, 0.4, 30)).toBe(41);
  expect(jobEta({ phase: "generating", eta_s: 41, slow: true }, 0.4, 30)).toBeNull();
  expect(jobEta({ phase: "request" }, 0.4, 30)).toBeNull();
  // Расшифровка — прежняя смесь оценки и замера.
  expect(jobEta({ estimate_s: 600 }, 0.1, 5)).toBe(540);
});

test("velocity: recent pace, never negative, capped", () => {
  expect(velocity([])).toBe(0);
  expect(velocity([{ v: 0.1, t: 0 }])).toBe(0);
  expect(velocity([{ v: 0.1, t: 0 }, { v: 0.2, t: 10_000 }])).toBeCloseTo(0.01 / 1000, 10);
  // Старое событие вне окна не в счёт.
  expect(velocity([{ v: 0, t: 0 }, { v: 0.5, t: 60_000 }, { v: 0.6, t: 70_000 }])).toBeCloseTo(0.01 / 1000, 10);
  // Рывок в одном событии — не темп: скорость ограничена.
  expect(velocity([{ v: 0, t: 0 }, { v: 0.9, t: 100 }])).toBeLessThanOrEqual(0.2 / 1000);
});

test("extrapolation moves with the pace, stops at the next checkpoint and after a pause", () => {
  const s = [{ v: 0.1, t: 0 }, { v: 0.2, t: 10_000 }]; // 1 % в секунду
  expect(extrapolate(s, 12_000, 0.5)).toBeCloseTo(0.22, 5);
  // Не дальше конца шага.
  expect(extrapolate(s, 12_000, 0.21)).toBe(0.21);
  // Новостей нет дольше 2,5 обычных промежутков — встаём.
  const ahead = maxAheadMs(s);
  expect(ahead).toBe(15_000);
  expect(extrapolate(s, 10_000 + ahead + 60_000, 0.9)).toBeCloseTo(0.2 + 0.01 * (ahead / 1000), 5);
  // Без отметки резидента (старый резидент) — не дальше MAX_LEAD.
  expect(extrapolate(s, 60_000, null)).toBeCloseTo(0.2 + MAX_LEAD, 5);
  // Никогда до полной: «готово» говорит только резидент.
  expect(extrapolate([{ v: 0.9, t: 0 }, { v: 0.99, t: 1000 }], 20_000, 1)).toBeLessThanOrEqual(EXTRAPOLATE_TOP);
  expect(extrapolate([{ v: 0.5, t: 0 }, { v: 1, t: 1000 }], 2000, 1)).toBe(1);
  // Одно событие — темпа нет.
  expect(extrapolate([{ v: 0.3, t: 0 }], 5000, 0.9)).toBe(0.3);
});
