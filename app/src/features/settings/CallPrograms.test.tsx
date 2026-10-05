import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { CALL_PROGRAMS, CallPrograms, callPrograms, exeProblem } from "./CallPrograms";

function Harness({ initial, running = [], onChange }: {
  initial: string[]; running?: string[]; onChange?: (v: string[]) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <CallPrograms value={value} processes={{ available: true, running }}
      onChange={(v) => { setValue(v); onChange?.(v); }} />
  );
}

test("пресеты с понятными именами; отмечено по выбранным exe без учёта регистра", () => {
  render(<Harness initial={["zoom.exe", "Teams.exe", "ms-teams.exe"]} />);
  expect(screen.getByRole("checkbox", { name: "Zoom" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Microsoft Teams" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Яндекс Телемост" })).not.toBeChecked();
  for (const name of ["Dion", "Webex", "Telegram", "Discord", "Slack", "Skype", "WhatsApp", "Viber"]) {
    expect(screen.getByRole("checkbox", { name })).toBeInTheDocument();
  }
  // не стена всех процессов: только пресеты
  expect(screen.getAllByRole("checkbox")).toHaveLength(CALL_PROGRAMS.length);
});

test("часть exe пресета — промежуточное состояние; клик добавляет недостающие", async () => {
  const changes: string[][] = [];
  render(<Harness initial={["Teams.exe"]} onChange={(v) => changes.push(v)} />);
  const teams = screen.getByRole("checkbox", { name: "Microsoft Teams" }) as HTMLInputElement;
  expect(teams.indeterminate).toBe(true);
  await userEvent.click(teams);
  expect(changes.at(-1)).toEqual(["Teams.exe", "ms-teams.exe"]);
  await userEvent.click(teams);
  expect(changes.at(-1)).toEqual([]);
});

test("снять пресет — убрать все его exe, остальное не трогать", async () => {
  const changes: string[][] = [];
  render(<Harness initial={["Zoom.exe", "Dion.exe", "my-call.exe"]} onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "Zoom" }));
  expect(changes.at(-1)).toEqual(["Dion.exe", "my-call.exe"]);
});

test("свои программы — чипы с ×", async () => {
  const changes: string[][] = [];
  render(<Harness initial={["Zoom.exe", "my-call.exe"]} onChange={(v) => changes.push(v)} />);
  const custom = screen.getByRole("list", { name: "Другие программы" });
  expect(within(custom).getByText("my-call.exe")).toBeInTheDocument();
  expect(within(custom).queryByText("Zoom.exe")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Убрать my-call.exe" }));
  expect(changes.at(-1)).toEqual(["Zoom.exe"]);
});

test("«Добавить программу…»: поиск по запущенным, выбор добавляет", async () => {
  const changes: string[][] = [];
  render(<Harness initial={["Zoom.exe"]} running={["explorer.exe", "my-call.exe", "Zoom.exe", "notepad.exe"]}
    onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  const box = screen.getByRole("combobox", { name: "Программа" });
  expect(box).toHaveFocus();
  await userEvent.type(box, "call");
  const list = screen.getByRole("listbox", { name: "Запущенные программы" });
  expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["my-call.exe"]);
  await userEvent.click(within(list).getByRole("option", { name: "my-call.exe" }));
  expect(changes.at(-1)).toEqual(["Zoom.exe", "my-call.exe"]);
  expect(screen.queryByRole("combobox", { name: "Программа" })).toBeNull();
});

test("уже выбранные в списке поиска не предлагаются", async () => {
  render(<Harness initial={["Zoom.exe"]} running={["zoom.exe", "notepad.exe"]} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  const list = screen.getByRole("listbox", { name: "Запущенные программы" });
  expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual(["notepad.exe"]);
});

test("свободный ввод *.exe — Enter добавляет; без .exe — подсказка", async () => {
  const changes: string[][] = [];
  render(<Harness initial={[]} onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  const box = screen.getByRole("combobox", { name: "Программа" });
  await userEvent.type(box, "Ringo");
  expect(screen.getByText("Имя программы должно оканчиваться на .exe")).toBeInTheDocument();
  await userEvent.type(box, "{Enter}");
  expect(changes).toEqual([]);
  await userEvent.type(box, ".exe{Enter}");
  expect(changes.at(-1)).toEqual(["Ringo.exe"]);
});

test("стрелки и Enter выбирают из найденного; Esc закрывает", async () => {
  const changes: string[][] = [];
  render(<Harness initial={[]} running={["alpha.exe", "beta.exe"]} onChange={(v) => changes.push(v)} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  const box = screen.getByRole("combobox", { name: "Программа" });
  await userEvent.type(box, "{ArrowDown}{ArrowDown}{Enter}");
  expect(changes.at(-1)).toEqual(["beta.exe"]);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  await userEvent.type(screen.getByRole("combobox", { name: "Программа" }), "{Escape}");
  expect(screen.queryByRole("combobox", { name: "Программа" })).toBeNull();
});

test("подсказки: браузерные звонки и уведомления мессенджеров", async () => {
  render(<Harness initial={[]} />);
  await userEvent.click(screen.getByRole("button", { name: "Какие звонки распознаются" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent(/браузер/);
  await userEvent.click(screen.getByRole("button", { name: "Почему осторожно с мессенджерами" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent(/уведомлени/);
});

test("ничего не выбрано — предупреждение про список по умолчанию", () => {
  render(<Harness initial={[]} />);
  expect(screen.getByText(/будет использован список по умолчанию/)).toBeInTheDocument();
});

test("combobox: aria-expanded — только когда список виден", async () => {
  render(<Harness initial={[]} running={["alpha.exe"]} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  const box = screen.getByRole("combobox", { name: "Программа" });
  expect(box).toHaveAttribute("aria-expanded", "true");
  await userEvent.type(box, "zzz");
  expect(box).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("listbox")).toBeNull();
});

test("после добавления и после Esc фокус возвращается на «Добавить программу…»", async () => {
  render(<Harness initial={[]} running={["alpha.exe"]} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  await userEvent.click(screen.getByRole("option", { name: "alpha.exe" }));
  expect(screen.getByRole("button", { name: "Добавить программу…" })).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  await userEvent.keyboard("{Escape}");
  expect(screen.getByRole("button", { name: "Добавить программу…" })).toHaveFocus();
});

test("имена exe пресета видны вторым текстом", () => {
  render(<Harness initial={[]} />);
  expect(screen.getByText("ms-teams.exe, Teams.exe")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Microsoft Teams" })).toBeInTheDocument();
});

test("список запущенных перечитывается при каждом открытии поиска", async () => {
  const load = vi.fn()
    .mockResolvedValueOnce({ available: true, running: ["first.exe"] })
    .mockResolvedValueOnce({ available: true, running: ["second.exe"] });
  render(<CallPrograms value={[]} processes={{ available: true, running: ["old.exe"] }}
    loadProcesses={load} onChange={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  expect(await screen.findByRole("option", { name: "first.exe" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
  expect(await screen.findByRole("option", { name: "second.exe" })).toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(2);
});

test("подсказки «?» — общий HelpTip: открываются нажатием и описывают кнопку", async () => {
  render(<Harness initial={[]} />);
  const tip = screen.getByRole("button", { name: "Какие звонки распознаются" });
  await userEvent.click(tip);
  expect(tip).toHaveAccessibleDescription(/Звонки в браузере/);
  const chat = screen.getByRole("button", { name: "Почему осторожно с мессенджерами" });
  await userEvent.click(chat);
  expect(chat).toHaveAccessibleDescription(/звуки уведомлений/);
});

describe("macOS", () => {
  const MacHarness = ({ initial, running = [], onChange }: {
    initial: string[]; running?: string[]; onChange?: (v: string[]) => void;
  }) => {
    const [value, setValue] = useState(initial);
    return (
      <CallPrograms os="macos" value={value} processes={{ available: true, running }}
        onChange={(v) => { setValue(v); onChange?.(v); }} />
    );
  };

  test("каталог macOS: имена процессов без .exe, есть FaceTime и TrueConf", () => {
    render(<MacHarness initial={["zoom.us"]} />);
    const mac = callPrograms("macos");
    expect(mac.flatMap((p) => p.exes).some((n) => /\.exe$/i.test(n))).toBe(false);
    expect(screen.getAllByRole("checkbox")).toHaveLength(mac.length);
    expect(screen.getByRole("checkbox", { name: "Zoom" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "FaceTime" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "TrueConf" })).toBeInTheDocument();
    expect(screen.getByText("MSTeams, Microsoft Teams")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\.exe/i);
  });

  test("каталог Windows не содержит программ только для macOS", () => {
    expect(callPrograms("windows").some((p) => p.id === "facetime")).toBe(false);
    expect(callPrograms("windows").flatMap((p) => p.exes).every((n) => /\.exe$/i.test(n))).toBe(true);
  });

  test("пресет отмечается по имени процесса без учёта регистра; клик добавляет недостающие", async () => {
    const changes: string[][] = [];
    render(<MacHarness initial={["MSTEAMS"]} onChange={(v) => changes.push(v)} />);
    const teams = screen.getByRole("checkbox", { name: "Microsoft Teams" }) as HTMLInputElement;
    expect(teams.indeterminate).toBe(true);
    await userEvent.click(teams);
    expect(changes.at(-1)).toEqual(["MSTEAMS", "Microsoft Teams"]);
  });

  test("свободный ввод без .exe принимается; плейсхолдер без .exe", async () => {
    const changes: string[][] = [];
    render(<MacHarness initial={[]} onChange={(v) => changes.push(v)} />);
    await userEvent.click(screen.getByRole("button", { name: "Добавить программу…" }));
    const box = screen.getByRole("combobox", { name: "Программа" });
    expect(box.getAttribute("placeholder")).not.toMatch(/\.exe/);
    await userEvent.type(box, "My Call App{Enter}");
    expect(changes.at(-1)).toEqual(["My Call App"]);
  });

  test("exeProblem: на macOS .exe не требуется, запрещён только «/»", () => {
    expect(exeProblem("Ringo", "macos")).toBeNull();
    expect(exeProblem("a/b", "macos")).not.toBeNull();
    expect(exeProblem("Ringo", "windows")).toBe("Имя программы должно оканчиваться на .exe");
    expect(exeProblem("Ringo.exe", "windows")).toBeNull();
  });

  test("свои программы с учётом платформы: Zoom.exe на macOS — чип, zoom.us — пресет", () => {
    render(<MacHarness initial={["zoom.us", "Zoom.exe"]} />);
    const custom = screen.getByRole("list", { name: "Другие программы" });
    expect(within(custom).getByText("Zoom.exe")).toBeInTheDocument();
    expect(within(custom).queryByText("zoom.us")).toBeNull();
  });
});
