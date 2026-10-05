import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StorageNotices } from "./StorageNotices";
import { resetMoveForTests, startMove } from "./storageMove";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getStorage: vi.fn(),
  answerLeftovers: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  storageStatus: vi.fn(),
  storageAbandon: vi.fn(async () => {}),
  storageMove: vi.fn(),
  onStorageProgress: vi.fn(async () => () => {}),
  onEngineProgress: vi.fn(async () => () => {}),
}));

const ep = { base: "/api", token: null };
const GB = 1024 ** 3;

const info = (over: Partial<api.StorageInfo> = {}): api.StorageInfo => ({
  root: "E:\\Meet", custom: true, home: "E:\\Meet", engine_dir: "E:\\Meet\\engine", models_dir: "E:\\Meet\\models",
  hf_cache: "E:\\Meet\\models\\hf", shared_cache: "C:\\hf", missing: null, models_bytes: 0, moving: false,
  busy: null, leftovers: null, ...over,
});
const status = (over: Partial<shell.StorageStatus> = {}): shell.StorageStatus => ({
  root: "E:\\Meet", home: "E:\\Meet", default_home: "C:\\data", missing: null, moving: false, cancellable: false,
  interrupted: null, ...over,
});
const leftovers = { cache: "C:\\hf", bytes: Math.round(3.4 * GB), repos: [{ id: "a/b", bytes: Math.round(3.4 * GB) }] };

beforeEach(() => {
  vi.clearAllMocks();
  resetMoveForTests();
  vi.mocked(api.getStorage).mockResolvedValue(info());
  vi.mocked(shell.storageStatus).mockResolvedValue(status());
});

test("остатки в общем кэше — вопрос поверх окна; «Удалить» главная, фокус — на «Позже»", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ leftovers }));
  vi.mocked(api.answerLeftovers).mockResolvedValue({ ok: true, removed: ["a/b"] });
  render(<StorageNotices endpoint={ep} onOpenEngine={() => {}} />);
  const dialog = await screen.findByRole("alertdialog", { name: "Модели Meet в общем кэше" });
  expect(dialog).toHaveTextContent("3,4 ГБ");
  expect(dialog).toHaveTextContent("могут пользоваться другие программы");
  const remove = within(dialog).getByRole("button", { name: "Удалить из общего кэша" });
  expect(remove).toHaveClass("btn--primary");
  expect(within(dialog).getByRole("button", { name: "Позже" })).toHaveFocus();
  vi.mocked(api.getStorage).mockResolvedValue(info());
  await userEvent.click(remove);
  expect(api.answerLeftovers).toHaveBeenCalledWith(ep, true);
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
});

test("«Оставить» и «Позже» не удаляют", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ leftovers }));
  vi.mocked(api.answerLeftovers).mockResolvedValue({ ok: true, removed: [] });
  const { unmount } = render(<StorageNotices endpoint={ep} onOpenEngine={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Оставить" }));
  expect(api.answerLeftovers).toHaveBeenCalledWith(ep, false);
  unmount();
  vi.mocked(api.answerLeftovers).mockClear();
  render(<StorageNotices endpoint={ep} onOpenEngine={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Позже" }));
  expect(api.answerLeftovers).not.toHaveBeenCalled();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

test("итог переноса виден, где бы ни был человек", async () => {
  vi.mocked(shell.storageMove).mockResolvedValue("E:\\Meet");
  const open = vi.fn();
  render(<StorageNotices endpoint={ep} onOpenEngine={open} />);
  await act(async () => { await startMove("E:\\Meet"); });
  const dialog = await screen.findByRole("alertdialog", { name: "Движок и модели перенесены" });
  expect(dialog).toHaveTextContent("E:\\Meet");
  await userEvent.click(within(dialog).getByRole("button", { name: "Показать в настройках" }));
  expect(open).toHaveBeenCalled();
});

test("прерванный перенос — «Продолжить» в ту же папку или «Отменить перенос»", async () => {
  vi.mocked(api.getStorage).mockResolvedValue(info({ root: null, custom: false, home: "C:\\data" }));
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ root: null, home: "C:\\data", interrupted: "E:\\Meet" }));
  vi.mocked(shell.storageMove).mockReturnValue(new Promise(() => {}));
  const open = vi.fn();
  const { unmount } = render(<StorageNotices endpoint={ep} onOpenEngine={open} />);
  const dialog = await screen.findByRole("alertdialog", { name: "Перенос движка и моделей прерван" });
  expect(dialog).toHaveTextContent("Скопированные модели сохранены");
  await userEvent.click(within(dialog).getByRole("button", { name: "Продолжить" }));
  expect(open).toHaveBeenCalled();
  expect(shell.storageMove).toHaveBeenCalledWith("E:\\Meet");
  unmount();
  resetMoveForTests();
  render(<StorageNotices endpoint={ep} onOpenEngine={open} />);
  vi.mocked(shell.storageStatus).mockResolvedValue(status({ root: null, home: "C:\\data" }));
  await userEvent.click(await screen.findByRole("button", { name: "Отменить перенос" }));
  expect(shell.storageAbandon).toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
});

test("ничего нет — ничего не показывает", async () => {
  const { container } = render(<StorageNotices endpoint={ep} onOpenEngine={() => {}} />);
  await waitFor(() => expect(api.getStorage).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});
