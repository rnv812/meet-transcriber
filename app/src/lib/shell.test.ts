import { saveText, toInstallResult } from "./shell";

test("браузер: ссылка в документе на время клика, URL отзывается позже", async () => {
  vi.useFakeTimers();
  const create = vi.fn(() => "blob:x");
  const revoke = vi.fn();
  Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
  let attached = false;
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    attached = document.body.contains(this);
    expect(this.download).toBe("Встреча.md");
    expect(this.getAttribute("href")).toBe("blob:x");
  });
  try {
    expect(await saveText("Встреча.md", "# текст")).toBe("Встреча.md");
    expect(click).toHaveBeenCalledTimes(1);
    expect(attached).toBe(true);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:x");
  } finally {
    click.mockRestore();
    vi.useRealTimers();
  }
});

test("браузер: openUrl открывает новую вкладку без доступа к окну", async () => {
  const { openUrl } = await import("./shell");
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  try {
    await openUrl("https://huggingface.co/settings/tokens");
    expect(open).toHaveBeenCalledWith("https://huggingface.co/settings/tokens", "_blank", "noopener,noreferrer");
  } finally {
    open.mockRestore();
  }
});

test("автозапуск: команды нет в оболочке — недоступен; есть (ругается на аргументы) — доступен", async () => {
  const { probeCommand } = await import("./shell");
  expect(await probeCommand(async () => { throw "Command set_autostart not found"; })).toBe(false);
  expect(await probeCommand(async () => {
    throw "invalid args `enabled` for command `set_autostart`: command set_autostart missing required key enabled";
  })).toBe(true);
  expect(await probeCommand(async () => undefined)).toBe(true);
});

test("ответ install_update: объект с причиной, а у старой оболочки — строка", () => {
  expect(toInstallResult({ outcome: "manual", reason: "нет прав" })).toEqual({ outcome: "manual", reason: "нет прав" });
  expect(toInstallResult("in-place")).toEqual({ outcome: "in-place", reason: null });
  expect(toInstallResult(undefined)).toEqual({ outcome: "installer", reason: null });
});
