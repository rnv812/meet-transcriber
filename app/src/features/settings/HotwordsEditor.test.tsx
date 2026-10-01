import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HotwordsEditor } from "./HotwordsEditor";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getHotwords: vi.fn(),
  putHotwords: vi.fn(),
}));

const ep = { base: "/api", token: null };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getHotwords).mockResolvedValue({ text: "", budget: 400, used: 0 });
  vi.mocked(api.putHotwords).mockImplementation(async (_e, text) => ({ text, budget: 400, used: 0 }));
});

test("счётчик считает термины без комментариев: «15 / 400»", async () => {
  render(<HotwordsEditor endpoint={ep} />);
  const box = await screen.findByRole("textbox");
  await userEvent.click(box);
  await userEvent.paste("SIEM\nSOC\n# c\nCMDB");
  expect(screen.getByTestId("hotwords-counter")).toHaveTextContent("15 / 400");
});

test("сверх бюджета: счётчик красный, предупреждение, «Сохранить» активна", async () => {
  render(<HotwordsEditor endpoint={ep} />);
  const box = await screen.findByRole("textbox");
  await userEvent.click(box);
  await userEvent.paste("я".repeat(401));
  const counter = screen.getByTestId("hotwords-counter");
  expect(counter).toHaveClass("hotwords__counter--over");
  expect(screen.getByText("Лимит превышен: лишние слова не будут учтены")).toBeInTheDocument();
  const save = screen.getByRole("button", { name: "Сохранить слова" });
  expect(save).toBeEnabled();
  await userEvent.click(save);
  await waitFor(() => expect(api.putHotwords).toHaveBeenCalledWith(ep, "я".repeat(401)));
});

test("встроенный комментарий отрезается: «SIEM, siem-like»", async () => {
  render(<HotwordsEditor endpoint={ep} />);
  await userEvent.click(await screen.findByRole("textbox"));
  await userEvent.paste("SIEM\nsiem-like # note");
  expect(screen.getByTestId("hotwords-counter")).toHaveTextContent("15 / 400");
});

test("повторы считаются один раз", async () => {
  render(<HotwordsEditor endpoint={ep} />);
  await userEvent.click(await screen.findByRole("textbox"));
  await userEvent.paste("SOC\nSOC");
  expect(screen.getByTestId("hotwords-counter")).toHaveTextContent("3 / 400");
});

test("после сохранения показывается used сервера", async () => {
  vi.mocked(api.putHotwords).mockResolvedValue({ text: "SOC", budget: 400, used: 99 });
  render(<HotwordsEditor endpoint={ep} />);
  await userEvent.click(await screen.findByRole("textbox"));
  await userEvent.paste("SOC");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить слова" }));
  await waitFor(() => expect(screen.getByTestId("hotwords-counter")).toHaveTextContent("99 / 400"));
});

test("ошибка сохранения терминов — сообщение без «Error:»", async () => {
  vi.mocked(api.putHotwords).mockRejectedValue(new Error("диск только для чтения"));
  render(<HotwordsEditor endpoint={ep} />);
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, "SIEM");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить слова" }));
  expect(await screen.findByText("диск только для чтения")).toBeInTheDocument();
});
