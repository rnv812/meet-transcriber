import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { BROWSERS, BrowserCalls, browsersFor } from "./BrowserCalls";

type Value = { browsers: string[]; requireSite: boolean; sites: string[] };

function Harness({ initial, onChange }: { initial: Partial<Value>; onChange?: (v: Value) => void }) {
  const [v, setV] = useState<Value>({ browsers: [], requireSite: false, sites: ["Dion", "Google Meet"], ...initial });
  const put = (patch: Partial<Value>) => setV((cur) => {
    const next = { ...cur, ...patch };
    onChange?.(next);
    return next;
  });
  return (
    <BrowserCalls browsers={v.browsers} requireSite={v.requireSite} sites={v.sites}
      onBrowsers={(browsers) => put({ browsers })} onRequireSite={(requireSite) => put({ requireSite })}
      onSites={(sites) => put({ sites })} />
  );
}

test("группа браузеров: все распространённые, отмечены выбранные без учёта регистра", () => {
  render(<Harness initial={{ browsers: ["Chrome.exe"] }} />);
  const group = screen.getByRole("group", { name: "Звонки в браузере" });
  for (const name of ["Google Chrome", "Microsoft Edge", "Firefox", "Яндекс Браузер", "Opera", "Brave", "Vivaldi"]) {
    expect(within(group).getByRole("checkbox", { name })).toBeInTheDocument();
  }
  expect(BROWSERS.map((b) => b.exe)).toEqual(
    ["chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe", "brave.exe", "vivaldi.exe"]);
  expect(screen.getByRole("checkbox", { name: "Google Chrome" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Firefox" })).not.toBeChecked();
});

test("отметить и снять браузер", async () => {
  const changes: Value[] = [];
  render(<Harness initial={{ browsers: ["chrome.exe"] }} onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Яндекс Браузер" }));
  expect(changes.at(-1)?.browsers).toEqual(["chrome.exe", "browser.exe"]);
  await userEvent.click(screen.getByRole("checkbox", { name: "Google Chrome" }));
  expect(changes.at(-1)?.browsers).toEqual(["browser.exe"]);
});

test("строгий режим — флажок", async () => {
  const changes: Value[] = [];
  render(<Harness initial={{}} onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("checkbox", { name: /Только если в заголовке окна сайт звонка/ }));
  expect(changes.at(-1)?.requireSite).toBe(true);
});

test("сайты звонков: чипы с ×, добавление по Enter и кнопкой, без повторов", async () => {
  const changes: Value[] = [];
  render(<Harness initial={{}} onChange={(v) => changes.push(v)} />);
  const chips = screen.getByRole("list", { name: "Сайты звонков" });
  expect(within(chips).getAllByRole("listitem").map((li) => li.firstChild?.textContent)).toEqual(["Dion", "Google Meet"]);
  await userEvent.click(screen.getByRole("button", { name: "Убрать Google Meet" }));
  expect(changes.at(-1)?.sites).toEqual(["Dion"]);
  const box = screen.getByRole("textbox", { name: "Новый сайт звонка" });
  await userEvent.type(box, "  Моя платформа {Enter}");
  expect(changes.at(-1)?.sites).toEqual(["Dion", "Моя платформа"]);
  expect(box).toHaveValue("");
  await userEvent.type(box, "dion");
  expect(screen.getByText("Такой сайт уже есть в списке")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Добавить" })).toBeDisabled();
  await userEvent.clear(box);
  await userEvent.type(box, "Видеосвязь");
  await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
  expect(changes.at(-1)?.sites).toEqual(["Dion", "Моя платформа", "Видеосвязь"]);
});

test("подсказка объясняет правило: микрофон, а не звук", async () => {
  render(<Harness initial={{}} />);
  const tip = screen.getByRole("button", { name: "Как распознаются звонки в браузере" });
  await userEvent.click(tip);
  expect(tip).toHaveAccessibleDescription(
    /Запись начнётся, когда браузер использует микрофон\. Музыка и видео без микрофона запись не запускают\./);
});

test("подсказка честно говорит про мьют в веб-клиенте и короткое использование микрофона", async () => {
  render(<Harness initial={{}} />);
  const tip = screen.getByRole("button", { name: "Как распознаются звонки в браузере" });
  await userEvent.click(tip);
  expect(tip).toHaveAccessibleDescription(/дольше 15 секунд/);
  expect(tip).toHaveAccessibleDescription(/выключенным микрофоном может его освободить/);
});

test("macOS: браузеры — имена процессов без .exe", () => {
  const onBrowsers = vi.fn();
  render(<BrowserCalls os="macos" browsers={["google chrome"]} requireSite={false} sites={[]}
    onBrowsers={onBrowsers} onRequireSite={() => {}} onSites={() => {}} />);
  expect(browsersFor("macos").some((b) => /\.exe$/i.test(b.exe))).toBe(false);
  expect(screen.getByRole("checkbox", { name: "Google Chrome" })).toBeChecked();
  expect(screen.getByText("Brave Browser")).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/\.exe/i);
});
