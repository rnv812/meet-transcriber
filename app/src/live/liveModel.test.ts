import type { LiveHint } from "../lib/types";
import { EMPTY_SUMMARY, summaryEntries, summaryIsEmpty, topHint } from "./liveModel";

const hint = (o: Partial<LiveHint>): LiveHint => ({
  id: "h1", kind: "question", text: "x", why: "", source_t: 0, ref: null, pinned: false, dismissed: false,
  created_at: 1, updated_at: 1, ...o,
});

test("самая важная подсказка: закреплённая, затем ценность вида, затем свежесть", () => {
  expect(topHint([])).toBeNull();
  const term = hint({ id: "h1", kind: "term", updated_at: 9 });
  const risk = hint({ id: "h2", kind: "risk", updated_at: 1 });
  const risk2 = hint({ id: "h3", kind: "risk", updated_at: 5 });
  expect(topHint([term, risk])).toBe(risk);
  expect(topHint([term, risk, risk2])).toBe(risk2);
  const pinned = hint({ id: "h4", kind: "followup", pinned: true });
  expect(topHint([risk, pinned, risk2])).toBe(pinned);
});

test("пункты сводки ключами; задача — со всеми полями", () => {
  expect(summaryIsEmpty(EMPTY_SUMMARY)).toBe(true);
  const entries = summaryEntries({
    ...EMPTY_SUMMARY, topic: "Релиз", tasks: [{ id: "t1", who: null, what: "Акт", due: "пт" }],
  });
  expect(entries).toEqual([["topic", "Релиз"], ["t1", "|Акт|пт"]]);
});

test("«Вам вопрос» важнее закреплённой и стоит наверху списка, свежий первым", async () => {
  const { orderHints } = await import("./liveModel");
  const pinned = hint({ id: "h1", kind: "risk", pinned: true });
  const plain = hint({ id: "h2", kind: "question" });
  const urgentOld = hint({ id: "h3", kind: "ask_you", created_at: 5 });
  const urgentNew = hint({ id: "h4", kind: "ask_you", created_at: 9 });
  expect(topHint([pinned, plain, urgentOld])).toBe(urgentOld);
  expect(orderHints([pinned, plain, urgentOld, urgentNew]).map((h) => h.id)).toEqual(["h4", "h3", "h1", "h2"]);
  const same = [pinned, plain];
  expect(orderHints(same)).toBe(same); // без «Вам вопрос» порядок не трогаем
});
