import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getDiagnostics: vi.fn(async () => ({ paths: { data_dir: "C:\\data\\meet" } })),
}));

let progressListener: ((p: { done: number; total: number }) => void) | null = null;
vi.mock("../../lib/shell", () => ({
  openUrl: vi.fn(async () => {}),
  releasesPage: vi.fn(async () => "https://github.com/example/updates/releases"),
  checkUpdate: vi.fn(),
  installUpdate: vi.fn(),
  cancelUpdate: vi.fn(async () => {}),
  UPDATE_CANCELLED: "Загрузка обновления отменена",
  updateStatus: vi.fn(async () => null),
  moveToApplications: vi.fn(async () => {}),
  openLogs: vi.fn(async () => {}),
  onUpdateProgress: vi.fn(async (cb: (p: { done: number; total: number }) => void) => {
    progressListener = cb;
    return () => { progressListener = null; };
  }),
}));

import * as shell from "../../lib/shell";
import type { InstallResult, UpdateStatus } from "../../lib/shell";
import { About, LAUNCHED, UPDATE_CONFIRM_WORK, lastFailureText, launchedText, megabytes } from "./About";

const endpoint = { base: "http://127.0.0.1:1", token: "t" };
const check = vi.mocked(shell.checkUpdate);
const install = vi.mocked(shell.installUpdate);

const newer = {
  current: "0.1.1",
  latest: "0.2.0",
  newer: true,
  notes_url: "https://github.com/example/updates/releases/tag/v0.2.0",
  asset_name: "meet_0.2.0_x64-setup.exe",
  size: 52428800,
};

const done = (outcome: InstallResult["outcome"], reason: string | null = null): InstallResult => ({ outcome, reason });

beforeEach(() => {
  check.mockReset();
  install.mockReset();
  vi.mocked(shell.openUrl).mockClear();
  vi.mocked(shell.updateStatus).mockResolvedValue(null);
  vi.mocked(shell.moveToApplications).mockReset();
  vi.mocked(shell.openLogs).mockClear();
});

async function renderAbout() {
  render(<About endpoint={endpoint} />);
  await waitFor(() => expect(screen.getByText("C:\\data\\meet")).toBeInTheDocument());
}

const checkButton = () => screen.getByRole("button", { name: "Проверить обновления" });

test("«Скачать новую версию» открывает страницу выпусков, которую назвала оболочка", async () => {
  await renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Скачать новую версию" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://github.com/example/updates/releases");
});

test("без оболочки ссылки на выпуски нет, но подсказка остаётся", async () => {
  vi.mocked(shell.releasesPage).mockResolvedValueOnce(null);
  await renderAbout();
  expect(screen.queryByRole("button", { name: "Скачать новую версию" })).toBeNull();
  expect(
    screen.getByText("Скачайте новый установщик и запустите его — данные сохранятся"),
  ).toBeInTheDocument();
});

test("рассказывает, как обновиться, и что данные сохранятся", async () => {
  await renderAbout();
  expect(
    screen.getByText("Скачайте новый установщик и запустите его — данные сохранятся"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Как работает обновление" })).toBeInTheDocument();
});

test("последняя версия", async () => {
  check.mockResolvedValue({ ...newer, latest: "0.1.1", newer: false });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("У вас последняя версия (0.1.1)")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Скачать и установить" })).toBeNull();
});

test("выпусков ещё нет — не ошибка", async () => {
  check.mockResolvedValue({
    current: "0.1.1", latest: null, newer: false, notes_url: null, asset_name: null, size: null,
  });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("Обновления пока не опубликованы")).toBeInTheDocument();
});

test("нет связи — текст ошибки оболочки", async () => {
  check.mockRejectedValue("Не удалось проверить: нет связи с GitHub");
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText("Не удалось проверить: нет связи с GitHub")).toBeInTheDocument();
  expect(checkButton()).toBeEnabled();
});

test("новая версия: «Что нового» и «Скачать и установить» с ходом загрузки", async () => {
  check.mockResolvedValue(newer);
  let finish: () => void = () => {};
  install.mockImplementation(() => new Promise<InstallResult>((resolve) => { finish = () => resolve(done("installer")); }));
  await renderAbout();
  await userEvent.click(checkButton());
  expect(await screen.findByText(/Доступна версия 0\.2\.0/)).toBeInTheDocument();
  expect(screen.getByText(/50 МБ/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Что нового" }));
  expect(shell.openUrl).toHaveBeenCalledWith(newer.notes_url);

  await userEvent.click(screen.getByRole("button", { name: "Скачать и установить" }));
  expect(install).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Начинаю загрузку…")).toBeInTheDocument();
  expect(checkButton()).toBeDisabled();
  act(() => progressListener?.({ done: 10485760, total: 52428800 }));
  expect(screen.getByText("Скачиваю: 10 МБ из 50 МБ")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Загрузка обновления" })).toHaveAttribute("aria-valuenow", "20");

  await act(async () => finish());
  expect(await screen.findByText("Установщик запущен, приложение закрывается…")).toBeInTheDocument();
});

test.each([
  ["in-place", "Устанавливаю новую версию — Meet закроется и запустится заново…"],
  ["in-place-admin", "Устанавливаю новую версию — macOS спросит пароль администратора, затем Meet запустится заново…"],
  ["manual", "Образ открыт в Finder: перетащите Meet в «Программы» с заменой и запустите заново. Приложение закрывается…"],
  ["installer", "Установщик запущен, приложение закрывается…"],
] as const)("macOS и Windows: итог установки «%s» назван своими словами", async (outcome, text) => {
  check.mockResolvedValue({ ...newer, asset_name: "Meet_0.2.0_aarch64.dmg" });
  install.mockResolvedValue(done(outcome));
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(LAUNCHED[outcome]).toBe(text);
  expect(checkButton()).toBeDisabled();
});

test("размер неизвестен — бегущая полоска; «Отменить загрузку» прерывает её", async () => {
  check.mockResolvedValue(newer);
  let fail: (e: unknown) => void = () => {};
  install.mockImplementation(() => new Promise<InstallResult>((_, reject) => { fail = reject; }));
  vi.mocked(shell.cancelUpdate).mockImplementation(async () => fail("Загрузка обновления отменена"));
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  act(() => progressListener?.({ done: 1048576, total: 0 }));
  expect(screen.getByRole("progressbar", { name: "Загрузка обновления" }))
    .toHaveClass("progressbar__track--indeterminate");
  await userEvent.click(screen.getByRole("button", { name: "Отменить загрузку" }));
  expect(shell.cancelUpdate).toHaveBeenCalled();
  expect(await screen.findByText("Загрузка отменена")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Скачать и установить" })).toBeEnabled();
});

test("строка итога есть всегда: проверка не сдвигает страницу", async () => {
  await renderAbout();
  const slot = document.querySelector(".update");
  expect(slot).not.toBeNull();
  expect(slot).toBeEmptyDOMElement();
});

test("отказ во время записи виден рядом с кнопкой", async () => {
  check.mockResolvedValue(newer);
  install.mockRejectedValue("Остановите запись, чтобы обновиться");
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  expect(await screen.findByText("Остановите запись, чтобы обновиться")).toBeInTheDocument();
  // Можно попробовать снова, когда запись закончится.
  expect(screen.getByRole("button", { name: "Скачать и установить" })).toBeEnabled();
});

test("идёт расшифровка — сначала вопрос, «Обновить сейчас» повторяет с согласием", async () => {
  check.mockResolvedValue(newer);
  install.mockRejectedValueOnce(UPDATE_CONFIRM_WORK).mockResolvedValueOnce(done("installer"));
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  expect(await screen.findByText(UPDATE_CONFIRM_WORK)).toBeInTheDocument();
  expect(install).toHaveBeenLastCalledWith(false);
  await userEvent.click(screen.getByRole("button", { name: "Обновить сейчас" }));
  expect(install).toHaveBeenLastCalledWith(true);
  expect(await screen.findByText("Установщик запущен, приложение закрывается…")).toBeInTheDocument();
});

test("вопрос о расшифровке можно отклонить — обновление не начинается", async () => {
  check.mockResolvedValue(newer);
  install.mockRejectedValueOnce(UPDATE_CONFIRM_WORK);
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  await userEvent.click(await screen.findByRole("button", { name: "Отмена" }));
  expect(screen.queryByText(UPDATE_CONFIRM_WORK)).toBeNull();
  expect(screen.getByRole("button", { name: "Скачать и установить" })).toBeEnabled();
  expect(install).toHaveBeenCalledTimes(1);
});

test("выпуск без установщика — только ссылка на выпуски", async () => {
  check.mockResolvedValue({ ...newer, asset_name: null, size: null });
  await renderAbout();
  await userEvent.click(checkButton());
  expect(
    await screen.findByText("В выпуске нет установщика — скачайте его со страницы выпусков"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Скачать и установить" })).toBeNull();
});

test("размер в мегабайтах", () => {
  expect(megabytes(52428800)).toBe("50 МБ");
  expect(megabytes(1572864)).toBe("1,5 МБ");
});

test("авторы с ролями и лицензия", async () => {
  await renderAbout();
  const authors = screen.getByRole("list", { name: "Авторы" });
  expect(Array.from(authors.querySelectorAll("li")).map((li) => li.textContent)).toEqual([
    "Андрей Алейников — автор проекта",
    "Андрей Сивуха — архитектура десктопного приложения",
    "Никита Резников — десктопное приложение и интерфейс",
  ]);
  expect(screen.getByText("Лицензия Apache-2.0")).toBeInTheDocument();
});

test("macOS: образ открыт — окно говорит, почему не на месте", async () => {
  check.mockResolvedValue({ ...newer, asset_name: "Meet_0.2.0_aarch64.dmg" });
  install.mockResolvedValue(done("manual", "в «Программах» уже лежит другое приложение с именем Meet.app"));
  await renderAbout();
  await userEvent.click(checkButton());
  await userEvent.click(await screen.findByRole("button", { name: "Скачать и установить" }));
  expect(
    await screen.findByText(
      "Обновление на месте не удалось: в «Программах» уже лежит другое приложение с именем Meet.app. "
      + "Открыт образ диска: перетащите Meet в «Программы» с заменой и запустите заново. Приложение закрывается…",
    ),
  ).toBeInTheDocument();
});

test("итог без причины и ответ старой оболочки — прежние тексты", () => {
  expect(launchedText(done("manual"))).toBe(LAUNCHED.manual);
  expect(launchedText(done("in-place", "не важно"))).toBe(LAUNCHED["in-place"]);
});

const translocated: UpdateStatus = {
  location: "translocated",
  bundle: "/private/var/folders/x/T/AppTranslocation/1/d/Meet.app",
  offer_move: true,
  move_hint: "Meet открыт из «Загрузок» или прямо из образа диска. Переместить его туда и перезапустить?",
  last_failure: null,
};

test("macOS не из «Программ»: пояснение и «Переместить Meet в Программы»", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue(translocated);
  await renderAbout();
  expect(await screen.findByText(translocated.move_hint!)).toBeInTheDocument();
  expect(screen.getByText(translocated.bundle!)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Переместить Meet в Программы" }));
  expect(shell.moveToApplications).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Перемещаю — Meet перезапустится…")).toBeInTheDocument();
});

test("перемещение не удалось — причина рядом с кнопкой, можно повторить", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue(translocated);
  vi.mocked(shell.moveToApplications).mockRejectedValue("Не удалось переместить Meet: подпись копии не прошла проверку");
  await renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Переместить Meet в Программы" }));
  expect(
    await screen.findByText("Не удалось переместить Meet: подпись копии не прошла проверку"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Переместить Meet в Программы" })).toBeEnabled();
});

test("перемещение во время расшифровки — сначала вопрос, «Переместить сейчас» повторяет с согласием", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue(translocated);
  vi.mocked(shell.moveToApplications).mockRejectedValueOnce(UPDATE_CONFIRM_WORK).mockResolvedValueOnce(undefined);
  await renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Переместить Meet в Программы" }));
  expect(await screen.findByText(UPDATE_CONFIRM_WORK)).toBeInTheDocument();
  expect(shell.moveToApplications).toHaveBeenLastCalledWith(false);
  await userEvent.click(screen.getByRole("button", { name: "Переместить сейчас" }));
  expect(shell.moveToApplications).toHaveBeenLastCalledWith(true);
});

test("вопрос о расшифровке при перемещении можно отклонить", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue(translocated);
  vi.mocked(shell.moveToApplications).mockRejectedValueOnce(UPDATE_CONFIRM_WORK);
  await renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Переместить Meet в Программы" }));
  await userEvent.click(await screen.findByRole("button", { name: "Отмена" }));
  expect(screen.queryByText(UPDATE_CONFIRM_WORK)).toBeNull();
  expect(screen.getByRole("button", { name: "Переместить Meet в Программы" })).toBeEnabled();
  expect(shell.moveToApplications).toHaveBeenCalledTimes(1);
});

test("в «Программах» (и на Windows) вопроса о перемещении нет", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue({
    ...translocated, location: "applications", bundle: "/Applications/Meet.app", offer_move: false, move_hint: null,
  });
  await renderAbout();
  await waitFor(() => expect(shell.updateStatus).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Переместить Meet в Программы" })).toBeNull();
});

test("«Открыть папку журналов» и причина прошлой неудачи", async () => {
  vi.mocked(shell.updateStatus).mockResolvedValue({
    ...translocated,
    offer_move: false,
    last_failure: { at: "2026-10-07 10:15:42Z", finish: "gave-up", reason: "пароль администратора не введён (нажато «Отменить»)" },
  });
  await renderAbout();
  expect(
    await screen.findByText(
      "Прошлое обновление (2026-10-07 10:15 UTC) не встало на место: пароль администратора не введён (нажато «Отменить»)",
    ),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть папку журналов" }));
  expect(shell.openLogs).toHaveBeenCalledTimes(1);
});

test("без прошлой неудачи строки о ней нет", () => {
  expect(lastFailureText(null)).toBeNull();
  expect(lastFailureText({ ...translocated, last_failure: null })).toBeNull();
  expect(
    lastFailureText({ ...translocated, last_failure: { at: "2026-10-07 10:15:42Z", finish: "rolled-back", reason: null } }),
  ).toBe("Прошлое обновление (2026-10-07 10:15 UTC) не встало на место: причина в журнале update.log");
});
