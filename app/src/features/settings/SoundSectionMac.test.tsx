/**
 * «Звук» на macOS (0.6.1): в «Звук собеседников» — «Системный звук» и все устройства ввода
 * (ради BlackHole). Выбирают свои наушники с микрофоном — и запись пишет микрофон вместо
 * собеседников. Подпись без «(вывод)», под списком — что выбрано на самом деле. Данные выдуманные.
 */

import { render, screen } from "@testing-library/react";
import { useState } from "react";
import type * as api from "../../lib/api";
import { SoundSection } from "./SoundSection";
import type { Raw } from "./Section";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});

const ep = { base: "/api", token: null };
const devices: api.Devices = {
  available: true, pinning: true,
  inputs: [{ name: "Микрофон MacBook", default: true }, { name: "AirPods Анны", default: false },
    { name: "BlackHole 2ch", default: false }],
  outputs: [{ name: "Системный звук", default: true }, { name: "Микрофон MacBook", default: false },
    { name: "AirPods Анны", default: false }, { name: "BlackHole 2ch", default: false }],
};

function Harness({ recording }: { recording: Record<string, unknown> }) {
  const [draft] = useState<Raw>({ recording });
  return <SoundSection draft={draft} devices={devices} endpoint={ep} set={() => {}} />;
}

const other = () => screen.getByRole("group", { name: "Звук собеседников" });

test("подпись — «Звук собеседников», без «(вывод)»: на Mac это не устройство вывода", () => {
  render(<Harness recording={{ mic_device: null, output_device: null }} />);
  expect(screen.getByRole("combobox", { name: "Звук собеседников" })).toHaveTextContent("Как в системе (сейчас: Системный звук)");
  expect(other()).not.toHaveTextContent(/микрофон:/);
});

test("выбран тот же микрофон, что пишет голос (системный или выбранный), — предупреждение", () => {
  const { unmount } = render(<Harness recording={{ mic_device: null, output_device: { name: "Микрофон MacBook" } }} />);
  expect(other()).toHaveTextContent(
    "«Микрофон MacBook» — ваш микрофон: запишется ваш голос, а не собеседники. Во время записи Meet возьмёт «Системный звук».");
  unmount();
  render(<Harness recording={{ mic_device: { name: "AirPods Анны" }, output_device: { name: "AirPods Анны" } }} />);
  expect(other()).toHaveTextContent("«AirPods Анны» — ваш микрофон");
});

test("другое устройство ввода — тихая строка: подходит только то, куда направлен звук звонка", () => {
  render(<Harness recording={{ mic_device: null, output_device: { name: "AirPods Анны" } }} />);
  expect(other()).toHaveTextContent(
    "Это устройство ввода: сюда должен приходить звук звонка (BlackHole и т. п.). Микрофон наушников не подойдёт — выберите «Системный звук».");
  expect(other()).not.toHaveTextContent("ваш микрофон");
});
