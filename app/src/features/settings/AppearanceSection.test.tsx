import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import * as api from "../../lib/api";
import { type Appearance, DEFAULT_APPEARANCE } from "../../theme/appearance";
import { AppearanceSection } from "./AppearanceSection";

vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), patchSettings: vi.fn() }));

const ep = { base: "/api", token: null };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patchSettings).mockResolvedValue({ settings: {}, restart_required: [] });
});

test("тема: выбор применяется сразу и пишется в настройки", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  expect(onPreview).toHaveBeenCalledWith({ ...DEFAULT_APPEARANCE, theme: "light" });
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { ui: { theme: "light", aurora: "violet", aurora_style: "glow", motion: true } });
});

test("палитра — пять образцов с подписями, выбранный отмечен", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={{ ...DEFAULT_APPEARANCE, aurora: "blue" }} onPreview={onPreview} />);
  const group = screen.getByRole("radiogroup", { name: "Палитра сияния" });
  const swatches = screen.getAllByRole("radio", { name: /Фиолетовая|Зелёная|Синяя|Красная|Янтарная/ });
  expect(swatches).toHaveLength(5);
  expect(group).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Синяя" })).toBeChecked();
  await userEvent.click(screen.getByRole("radio", { name: "Янтарная" }));
  expect(onPreview).toHaveBeenCalledWith({ ...DEFAULT_APPEARANCE, aurora: "amber" });
});

test("вид сияния и живое сияние", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Волны" }));
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, auroraStyle: "waves" });
  await userEvent.click(screen.getByRole("switch", { name: "Живое сияние" }));
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, motion: false });
});

test("резидент не сохранил — выбор откатывается и видна ошибка", async () => {
  vi.mocked(api.patchSettings).mockRejectedValue(new Error("Служба записи не отвечает"));
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await waitFor(() => expect(onPreview).toHaveBeenLastCalledWith(DEFAULT_APPEARANCE));
  expect(screen.getByRole("alert")).toHaveTextContent("Служба записи не отвечает");
});

// Быстрые выборы подряд: записи идут по одной, из ожидающих — только последний,
// откат — к последнему значению, которое резидент принял.
type Settle = { resolve: () => void; reject: (e: Error) => void };

function trackPatches() {
  const calls: { theme: unknown; settle: Settle }[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  vi.mocked(api.patchSettings).mockImplementation((_ep, body) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    return new Promise((resolve, reject) => {
      const done = () => { inFlight -= 1; };
      calls.push({
        theme: (body as { ui: { theme: unknown } }).ui.theme,
        settle: {
          resolve: () => { done(); resolve({ settings: {}, restart_required: [] }); },
          reject: (e) => { done(); reject(e); },
        },
      });
    });
  });
  const at = (i: number) => {
    const call = calls[i];
    if (!call) throw new Error(`записи №${i} не было`);
    return call;
  };
  return { calls, at, maxInFlight: () => maxInFlight };
}

function Harness({ onPreview }: { onPreview: (a: Appearance) => void }) {
  const [value, setValue] = useState<Appearance>(DEFAULT_APPEARANCE);
  return <AppearanceSection endpoint={ep} value={value} onPreview={(a) => { setValue(a); onPreview(a); }} />;
}

test("два быстрых выбора: первый не записался, второй записался — на экране второй", async () => {
  const patches = trackPatches();
  const onPreview = vi.fn();
  render(<Harness onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await userEvent.click(screen.getByRole("radio", { name: "Тёмная" }));
  expect(patches.calls.map((c) => c.theme)).toEqual(["light"]);
  await act(async () => patches.at(0).settle.reject(new Error("занято")));
  await waitFor(() => expect(patches.calls).toHaveLength(2));
  expect(patches.at(1).theme).toBe("dark");
  await act(async () => patches.at(1).settle.resolve());
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, theme: "dark" });
  expect(screen.getByRole("radio", { name: "Тёмная" })).toBeChecked();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(patches.maxInFlight()).toBe(1);
});

test("два быстрых выбора, оба не записались — откат к сохранённому до них", async () => {
  const patches = trackPatches();
  const onPreview = vi.fn();
  render(<Harness onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await userEvent.click(screen.getByRole("radio", { name: "Тёмная" }));
  await act(async () => patches.at(0).settle.reject(new Error("занято")));
  await waitFor(() => expect(patches.calls).toHaveLength(2));
  await act(async () => patches.at(1).settle.reject(new Error("Служба записи не отвечает")));
  expect(onPreview).toHaveBeenLastCalledWith(DEFAULT_APPEARANCE);
  expect(screen.getByRole("radio", { name: "Системная" })).toBeChecked();
  expect(screen.getByRole("alert")).toHaveTextContent("Служба записи не отвечает");
  expect(patches.maxInFlight()).toBe(1);
});

test("первый записался, второй нет — откат к первому, а не к исходному", async () => {
  const patches = trackPatches();
  const onPreview = vi.fn();
  render(<Harness onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await userEvent.click(screen.getByRole("radio", { name: "Тёмная" }));
  await act(async () => patches.at(0).settle.resolve());
  await waitFor(() => expect(patches.calls).toHaveLength(2));
  await act(async () => patches.at(1).settle.reject(new Error("занято")));
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, theme: "light" });
  expect(screen.getByRole("radio", { name: "Светлая" })).toBeChecked();
});

test("пока запись идёт, из ожидающих выборов уходит только последний", async () => {
  const patches = trackPatches();
  render(<Harness onPreview={vi.fn()} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await userEvent.click(screen.getByRole("radio", { name: "Тёмная" }));
  await userEvent.click(screen.getByRole("radio", { name: "Системная" }));
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  expect(patches.calls).toHaveLength(1);
  await act(async () => patches.at(0).settle.resolve());
  await waitFor(() => expect(patches.calls).toHaveLength(2));
  expect(patches.at(1).theme).toBe("light");
  await act(async () => patches.at(1).settle.resolve());
  expect(patches.calls).toHaveLength(2);
  expect(patches.maxInFlight()).toBe(1);
});
