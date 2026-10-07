import { agentMsg, attMsg, userMsg } from "../test/chatFixtures";
import {
  type ChatState, EMPTY_CHAT, REVEAL_MS, chatReducer, feedItems, nextReveal, pinnedOf, usedButton, writingShown,
} from "./chatModel";

const snap = (messages: ReturnType<typeof agentMsg>[], seq = 10) =>
  chatReducer(EMPTY_CHAT, { type: "snapshot", snap: { messages, seq }, now: 0 });
const ids = (s: ChatState, now = 0) =>
  feedItems(s, now).map((it) => (it.type === "message" ? it.message.id : `out:${it.out.client_id}`));

test("свёртка: события не новее снимка отбрасываются, новые добавляют и правят", () => {
  let s = snap([agentMsg("m1"), userMsg("m2")], 10);
  s = chatReducer(s, { type: "event", event: { seq: 9, op: "add", message: agentMsg("m0") }, now: 0 });
  s = chatReducer(s, { type: "event", event: { seq: 10, op: "patch", id: "m1", set: { text: "старое" } }, now: 0 });
  expect(ids(s)).toEqual(["m1", "m2"]);
  expect(s.byId.m1!.text).toBe("Сообщение m1");
  s = chatReducer(s, { type: "event", event: { seq: 11, op: "add", message: agentMsg("m3") }, now: 0 });
  s = chatReducer(s, { type: "event", event: { seq: 12, op: "patch", id: "m3", set: { text: "Новый текст" } }, now: 0 });
  expect(ids(s)).toEqual(["m1", "m2", "m3"]);
  expect(s.byId.m3!.text).toBe("Новый текст");
  expect(s.seq).toBe(12);
});

test("вложения, служебные записи и скрытые статусы в ленте не видны", () => {
  let s = snap([attMsg("a1"), agentMsg("m1"), { ...agentMsg("t1"), kind: "tool" }, agentMsg("m2", { status: "superseded" })]);
  expect(ids(s)).toEqual(["m1"]);
  // Правка переводит ответ в скрытый статус — он уходит из ленты.
  s = chatReducer(s, { type: "event", event: { seq: 11, op: "patch", id: "m1", set: { status: "dropped" } }, now: 0 });
  expect(ids(s)).toEqual([]);
  expect(s.byId.a1).toBeDefined(); // вложение доступно сообщению, которое на него сошлётся
});

test("молчаливый ход агента не мигает «Пишет…»; ответ человеку — через REVEAL_MS или с текстом", () => {
  let s = snap([], 1);
  s = chatReducer(s, { type: "event", event: { seq: 2, op: "add", message: agentMsg("m1", { status: "writing", mode: "proactive", text: "" }) }, now: 1000 });
  expect(ids(s, 1000 + 10 * REVEAL_MS)).toEqual([]);
  expect(nextReveal(s, 1000)).toBeNull();
  s = chatReducer(s, { type: "event", event: { seq: 3, op: "patch", id: "m1", set: { status: "dropped", silent: true } }, now: 2000 });
  expect(ids(s, 3000)).toEqual([]);

  s = chatReducer(s, { type: "event", event: { seq: 4, op: "add", message: agentMsg("m2", { status: "writing", mode: "reply", text: "" }) }, now: 5000 });
  expect(ids(s, 5000 + REVEAL_MS - 1)).toEqual([]);
  expect(nextReveal(s, 5000)).toBe(5000 + REVEAL_MS);
  expect(ids(s, 5000 + REVEAL_MS)).toEqual(["m2"]);
  expect(writingShown(s, 5000 + REVEAL_MS)).toBe("m2");

  // Проактивный ход с пришедшим текстом — виден сразу.
  s = chatReducer(s, { type: "event", event: { seq: 5, op: "add", message: agentMsg("m3", { status: "writing", text: "" }) }, now: 6000 });
  s = chatReducer(s, { type: "partial", partial: { id: "m3", text: "Посмотрел план" } });
  expect(ids(s, 6000)).toContain("m3");
  s = chatReducer(s, { type: "event", event: { seq: 6, op: "patch", id: "m3", set: { status: "shown", text: "Посмотрел план — там 15.11" } }, now: 6100 });
  expect(s.partial.m3).toBeUndefined();
});

test("сообщение человека: черновик до записи журнала, по client_id уходит", () => {
  let s = snap([agentMsg("m1")], 5);
  s = chatReducer(s, { type: "queue", out: { client_id: "c1", text: "что там?", attachments: [], at: 0, state: "sending" } });
  expect(ids(s)).toEqual(["m1", "out:c1"]);
  s = chatReducer(s, { type: "sent", client_id: "c1", id: "m2" });
  expect(ids(s)).toEqual(["m1", "out:c1"]);
  s = chatReducer(s, { type: "event", event: { seq: 6, op: "add", message: userMsg("m2", { client_id: "c1", text: "что там?" }) }, now: 0 });
  expect(ids(s)).toEqual(["m1", "m2"]);
});

test("переподключение: снимок заменяет ленту и сверяет черновики по client_id", () => {
  let s = snap([agentMsg("m1")], 5);
  s = chatReducer(s, { type: "queue", out: { client_id: "c1", text: "раз", attachments: [], at: 0, state: "sending" } });
  s = chatReducer(s, { type: "failed", client_id: "c1", error: "нет связи" });
  s = chatReducer(s, { type: "queue", out: { client_id: "c2", text: "два", attachments: [], at: 0, state: "sending" } });
  s = chatReducer(s, { type: "snapshot", snap: { messages: [agentMsg("m1"), userMsg("m2", { client_id: "c2" })], seq: 9 }, now: 0 });
  expect(ids(s)).toEqual(["m1", "m2", "out:c1"]);
  expect(s.outbox[0]!.state).toBe("failed");
});

test("правка неизвестной записи, которая стала видимой, — перечитать ленту", () => {
  let s = snap([], 1);
  s = chatReducer(s, { type: "event", event: { seq: 2, op: "patch", id: "m9", set: { status: "dropped" } }, now: 0 });
  expect(s.stale).toBe(false);
  s = chatReducer(s, { type: "event", event: { seq: 3, op: "patch", id: "m9", set: { status: "shown" } }, now: 0 });
  expect(s.stale).toBe(true);
});

test("закреплённый вопрос — пока человек после него ничего не написал", () => {
  let s = snap([agentMsg("m1", { pin: true })], 2);
  expect(pinnedOf(s)?.id).toBe("m1");
  s = chatReducer(s, { type: "event", event: { seq: 3, op: "add", message: userMsg("m2") }, now: 0 });
  expect(pinnedOf(s)).toBeNull();
});

test("нажатая кнопка: сразу после щелчка и по записи журнала", () => {
  let s = snap([agentMsg("m1", { buttons: ["Глянь", "Не надо"] })], 2);
  expect(usedButton(s, "m1")).toBeNull();
  s = chatReducer(s, { type: "click", id: "m1", label: "Глянь" });
  expect(usedButton(s, "m1")).toBe("Глянь");
  s = chatReducer(s, { type: "event", event: { seq: 3, op: "add", message: userMsg("m2", { via: "button", re: "m1", text: "Глянь" }) }, now: 0 });
  expect(s.clicked).toEqual({});
  expect(usedButton(s, "m1")).toBe("Глянь");
});
