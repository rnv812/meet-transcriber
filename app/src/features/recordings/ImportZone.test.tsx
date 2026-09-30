import { render, screen } from "@testing-library/react";
import { ImportZone } from "./ImportZone";

const h = vi.hoisted(() => ({ handler: null as null | ((e: unknown) => void), subscribe: vi.fn(), importFile: vi.fn() }));
vi.mock("../../lib/shell", () => ({ inTauri: () => true, pickMedia: async () => null }));
vi.mock("../../lib/api", () => ({ importFile: h.importFile }));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: async (fn: (e: unknown) => void) => {
      h.subscribe();
      h.handler = fn;
      return () => {};
    },
  }),
}));

const ep = { base: "/api", token: null };

test("ошибки по каждому файлу с именем, удачные импортируются", async () => {
  h.importFile.mockReset();
  h.importFile.mockImplementation(async (_ep: unknown, path: string) => {
    if (path.endsWith("a.mp3")) throw new Error("формат не поддержан");
    if (path.endsWith("c.mp3")) throw new Error("файл занят");
    return { recording: "x" };
  });
  render(<ImportZone endpoint={ep} />);
  await vi.waitFor(() => expect(h.handler).not.toBeNull());
  h.handler!({ payload: { type: "drop", paths: ["C:\\m\\a.mp3", "C:\\m\\b.mp3", "/m/c.mp3"] } });
  expect(await screen.findByText("a.mp3: формат не поддержан")).toBeInTheDocument();
  expect(screen.getByText("c.mp3: файл занят")).toBeInTheDocument();
  expect(h.importFile).toHaveBeenCalledTimes(3);
});

test("подписка на перетаскивание одна при повторных рендерах", async () => {
  h.subscribe.mockClear();
  const { rerender } = render(<ImportZone endpoint={ep} onImported={() => {}} />);
  await vi.waitFor(() => expect(h.subscribe).toHaveBeenCalled());
  for (let i = 0; i < 4; i++) rerender(<ImportZone endpoint={ep} onImported={() => {}} />);
  await new Promise((r) => setTimeout(r, 20));
  expect(h.subscribe).toHaveBeenCalledTimes(1);
});
