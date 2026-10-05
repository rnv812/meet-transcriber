import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import * as api from "../../lib/api";
import { SoundSection } from "./SoundSection";
import type { Raw } from "./Section";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  testDevice: vi.fn(),
  getOwnerVoice: vi.fn(),
}));

const ep = { base: "/api", token: null };
const devices: api.Devices = {
  available: true, pinning: true,
  inputs: [{ name: "Микрофон", default: true }, { name: "USB-микрофон", default: false }],
  outputs: [{ name: "Колонки", default: true }, { name: "Наушники", default: false }],
};

function Harness({ initial, onDraft, list = devices }: {
  initial: Raw; onDraft?: (d: Raw) => void; list?: api.Devices | null;
}) {
  const [draft, setDraft] = useState<Raw>(initial);
  return (
    <SoundSection draft={draft} devices={list} endpoint={ep}
      set={(g, k, v) => setDraft((cur) => {
        const next = { ...cur, [g]: { ...(cur[g] ?? {}), [k]: v } };
        onDraft?.(next);
        return next;
      })} />
  );
}

const recording = { mic_device: null, output_device: null };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    samples: [{ id: "s1", source: "enroll", date: "2026-10-05", seconds: 21, device: "USB-микрофон",
      recording: null, quality: 0.8 }],
    take: null, ready: true, reason: null, recording: false, seconds: 25,
  });
});

test("по умолчанию — «Как в системе» с именем текущего системного устройства", () => {
  render(<Harness initial={{ recording }} />);
  const mic = screen.getByRole("combobox", { name: "Микрофон" });
  expect(mic).toHaveDisplayValue("Как в системе (сейчас: Микрофон)");
  const out = screen.getByRole("combobox", { name: "Звук собеседников (вывод)" });
  expect(out).toHaveDisplayValue("Как в системе (сейчас: Колонки)");
  expect(screen.getByText(/применятся со следующей записи/)).toBeInTheDocument();
});

test("выбор устройства пишет {name}, «Как в системе» — null", async () => {
  const drafts: Raw[] = [];
  render(<Harness initial={{ recording }} onDraft={(d) => drafts.push(d)} />);
  const mic = screen.getByRole("combobox", { name: "Микрофон" });
  await userEvent.selectOptions(mic, "USB-микрофон");
  expect(drafts.at(-1)?.recording?.mic_device).toEqual({ name: "USB-микрофон" });
  await userEvent.selectOptions(mic, "");
  expect(drafts.at(-1)?.recording?.mic_device).toBeNull();
  const out = screen.getByRole("combobox", { name: "Звук собеседников (вывод)" });
  await userEvent.selectOptions(out, "Наушники");
  expect(drafts.at(-1)?.recording?.output_device).toEqual({ name: "Наушники" });
});

test("выбранное, но отключённое устройство остаётся в списке с пометкой", () => {
  render(<Harness initial={{ recording: { ...recording, mic_device: { name: "Гарнитура" } } }} />);
  expect(screen.getByRole("combobox", { name: "Микрофон" }))
    .toHaveDisplayValue("Гарнитура (не подключено)");
});

test("«Проверить» проверяет выбранное в черновике и показывает уровень", async () => {
  vi.mocked(api.testDevice).mockResolvedValue({ ok: true, peak: 0.42, device: "USB-микрофон", fallback: false });
  render(<Harness initial={{ recording: { ...recording, mic_device: { name: "USB-микрофон" } } }} />);
  const row = screen.getByRole("group", { name: "Микрофон" });
  await userEvent.click(within(row).getByRole("button", { name: "Проверить" }));
  expect(api.testDevice).toHaveBeenCalledWith(ep, "mic", "USB-микрофон");
  const meter = await within(row).findByRole("meter", { name: "Уровень: Микрофон" });
  expect(meter).toHaveAttribute("aria-valuenow", "42");
  expect(within(row).getByText(/Звук есть: USB-микрофон/)).toBeInTheDocument();
});

test("тишина на выводе — подсказка включить звук; системное — name null", async () => {
  vi.mocked(api.testDevice).mockResolvedValue({ ok: true, peak: 0, device: "Колонки", fallback: false });
  render(<Harness initial={{ recording }} />);
  const row = screen.getByRole("group", { name: "Звук собеседников (вывод)" });
  await userEvent.click(within(row).getByRole("button", { name: "Проверить" }));
  expect(api.testDevice).toHaveBeenCalledWith(ep, "output", null);
  expect(await within(row).findByText(/Звука нет\. Включите любой звук/)).toBeInTheDocument();
});

test("выбранного нет — проверено системное, так и сказано", async () => {
  vi.mocked(api.testDevice).mockResolvedValue({ ok: true, peak: 0.3, device: "Микрофон", fallback: true });
  render(<Harness initial={{ recording: { ...recording, mic_device: { name: "Гарнитура" } } }} />);
  const row = screen.getByRole("group", { name: "Микрофон" });
  await userEvent.click(within(row).getByRole("button", { name: "Проверить" }));
  expect(await within(row).findByText(/Выбранное устройство не найдено — проверено системное: Микрофон/))
    .toBeInTheDocument();
});

test("отказ резидента (идёт запись) — текстом", async () => {
  vi.mocked(api.testDevice).mockRejectedValue(new api.ApiError(409, "Идёт запись — проверка устройства недоступна"));
  render(<Harness initial={{ recording }} />);
  const row = screen.getByRole("group", { name: "Микрофон" });
  await userEvent.click(within(row).getByRole("button", { name: "Проверить" }));
  expect(await within(row).findByText("Идёт запись — проверка устройства недоступна")).toBeInTheDocument();
  await waitFor(() => expect(within(row).getByRole("button", { name: "Проверить" })).toBeEnabled());
});

test("«?» у вывода объясняет запись через loopback", async () => {
  render(<Harness initial={{ recording }} />);
  await userEvent.click(screen.getByRole("button", { name: "Как записывается звук собеседников" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent(/loopback/);
});

test("список недоступен — только «Как в системе» и причина", () => {
  render(<Harness initial={{ recording }} list={{ available: false, pinning: true, error: "нет WASAPI" }} />);
  const mic = screen.getByRole("combobox", { name: "Микрофон" });
  expect(within(mic).getAllByRole("option")).toHaveLength(1);
  expect(mic).toHaveDisplayValue("Как в системе");
  expect(screen.getByText(/Список устройств недоступен: нет WASAPI/)).toBeInTheDocument();
});

test("строка итога проверки есть и до проверки: её появление не сдвигает разделы ниже", () => {
  const { container } = render(<SoundSection draft={{ recording: {} }} set={() => {}} devices={null} endpoint={{ base: "/api", token: null }} />);
  expect(container.querySelectorAll(".sound__result").length).toBeGreaterThan(0);
});

test("«Мой голос»: что записано и «Перезаписать»", async () => {
  render(<Harness initial={{ recording }} />);
  const group = screen.getByRole("group", { name: "Мой голос" });
  expect(await within(group).findByText("записан 05.10 · USB-микрофон")).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Перезаписать" })).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Удалить образец: записан 05.10 · USB-микрофон" }))
    .toBeInTheDocument();
});
