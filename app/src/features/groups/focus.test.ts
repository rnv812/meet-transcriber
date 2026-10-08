import { allRow, focusInRow, focusLost, focusWhenReady, groupRow, rescueFocus } from "./focus";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ""; });

test("фокус потерян (строка ушла из списка) — на запасной элемент; не потерян — не трогаем", () => {
  document.body.innerHTML = `<input id="search"><button id="row">Созвон</button><button id="other">Ещё</button>`;
  const row = document.getElementById("row")!;
  row.focus();
  row.remove();
  expect(focusLost()).toBe(true);
  rescueFocus(() => document.getElementById("search"));
  vi.advanceTimersByTime(0);
  expect(document.getElementById("search")).toHaveFocus();
  document.getElementById("other")!.focus();
  rescueFocus(() => document.getElementById("search"));
  vi.advanceTimersByTime(1000);
  expect(document.getElementById("other")).toHaveFocus();
});

test("фокус в строке удалённой группы — на «Все записи», один раз", () => {
  document.body.innerHTML = `<button data-scope-key="all">Все записи</button>
    <ul><li data-row-key="g-a"><button id="more">⋯</button></li></ul>`;
  document.getElementById("more")!.focus();
  expect(focusInRow("g-a")).toBe(true);
  rescueFocus(allRow, () => focusInRow("g-a"));
  vi.advanceTimersByTime(0);
  expect(allRow()).toHaveFocus();
  // Потом человек вернулся в строку (например, «Отменить» вернул группу) — больше не отнимаем.
  document.getElementById("more")!.focus();
  vi.advanceTimersByTime(1000);
  expect(document.getElementById("more")).toHaveFocus();
});

test("focusWhenReady ждёт, пока элемент появится", () => {
  focusWhenReady(() => document.getElementById("late"));
  vi.advanceTimersByTime(100);
  const late = document.createElement("button");
  late.id = "late";
  document.body.append(late);
  vi.advanceTimersByTime(1000);
  expect(late).toHaveFocus();
});

test("дерево групп закрыто — «Все записи» и строка группы — это кнопка-список; открыто — только его строки", () => {
  document.body.innerHTML = `<button data-groups-picker="">Все записи</button>`;
  const picker = document.querySelector<HTMLElement>("[data-groups-picker]")!;
  expect(allRow()).toBe(picker);
  expect(groupRow("g-a")).toBe(picker);
  // Дерево открыто, строки группы ещё нет (возвращается после «Отменить») — ждём её, а не кнопку.
  document.body.insertAdjacentHTML("beforeend", `<div class="nav-groups"><button data-scope-key="all">Все</button></div>`);
  expect(allRow()).toHaveAttribute("data-scope-key", "all");
  expect(groupRow("g-a")).toBeNull();
});
