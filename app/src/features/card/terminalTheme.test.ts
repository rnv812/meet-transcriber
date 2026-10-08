import { TERMINAL_TOKENS, terminalTheme, watchAppearance } from "./terminalTheme";

const root = document.documentElement;
const set = (tokens: Record<string, string>) => {
  for (const [name, value] of Object.entries(tokens)) root.style.setProperty(name, value);
};

afterEach(() => {
  for (const name of Object.values(TERMINAL_TOKENS)) root.style.removeProperty(name);
  root.removeAttribute("data-theme");
  root.removeAttribute("data-aurora");
});

test("цвета терминала — токены блока кода текущей темы", () => {
  set({
    "--code-bg": "rgb(1, 2, 3)", "--code-ink": "rgb(200, 201, 202)", "--code-comment": "rgb(90, 90, 90)",
    "--selection": "rgb(10, 60, 40)", "--danger": "rgb(220, 50, 50)", "--success": "rgb(40, 200, 120)",
  });
  const theme = terminalTheme();
  expect(theme.background).toBe("rgb(1, 2, 3)");
  expect(theme.foreground).toBe("rgb(200, 201, 202)");
  // Курсор — цвет текста, под курсором — фон (блок, как в макете).
  expect(theme.cursor).toBe("rgb(200, 201, 202)");
  expect(theme.cursorAccent).toBe("rgb(1, 2, 3)");
  expect(theme.selectionBackground).toBe("rgb(10, 60, 40)");
  // Серый ANSI (строка «— агент завершил работу —») — цвет комментария.
  expect(theme.brightBlack).toBe("rgb(90, 90, 90)");
  expect(theme.red).toBe("rgb(220, 50, 50)");
  expect(theme.green).toBe("rgb(40, 200, 120)");
});

test("токена нет — цвета нет: остаётся умолчание xterm, а не пустая строка", () => {
  set({ "--code-bg": "rgb(1, 2, 3)" });
  const theme = terminalTheme();
  expect(theme.background).toBe("rgb(1, 2, 3)");
  expect("foreground" in theme).toBe(false);
});

test("смена темы и палитры (data-theme, data-aurora на <html>) — сигнал перечитать цвета; другие атрибуты — нет", async () => {
  const onChange = vi.fn();
  const stop = watchAppearance(onChange);
  const tick = () => new Promise((r) => setTimeout(r, 0));
  root.setAttribute("data-theme", "light");
  await tick();
  expect(onChange).toHaveBeenCalledTimes(1);
  root.setAttribute("data-aurora", "green");
  await tick();
  expect(onChange).toHaveBeenCalledTimes(2);
  root.setAttribute("lang", "ru");
  await tick();
  expect(onChange).toHaveBeenCalledTimes(2);
  stop();
  root.setAttribute("data-theme", "dark");
  await tick();
  expect(onChange).toHaveBeenCalledTimes(2);
  root.removeAttribute("lang");
});
