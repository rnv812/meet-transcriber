import { describe, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { clipboardKeys } from "./agentSessions";

function press(handler: (e: KeyboardEvent) => boolean, init: KeyboardEventInit) {
  const e = new KeyboardEvent("keydown", { cancelable: true, ...init });
  return { passed: handler(e), prevented: e.defaultPrevented };
}

function term(selection: string) {
  return { getSelection: () => selection, paste: vi.fn() } as unknown as Terminal & { paste: ReturnType<typeof vi.fn> };
}

describe("клавиши буфера в терминале агента", () => {
  it("Mac: ⌘C копирует выделение", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText, readText: vi.fn() }, configurable: true });
    const r = press(clipboardKeys(term("текст"), "macos"), { code: "KeyC", metaKey: true });
    expect(r).toEqual({ passed: false, prevented: true });
    expect(writeText).toHaveBeenCalledWith("текст");
  });

  it("Mac: ⌘C без выделения и Ctrl+C уходят агенту", () => {
    const h = clipboardKeys(term(""), "macos");
    expect(press(h, { code: "KeyC", metaKey: true }).passed).toBe(true);
    expect(press(clipboardKeys(term("текст"), "macos"), { code: "KeyC", ctrlKey: true }).passed).toBe(true);
  });

  it("Mac: ⌘V вставляет из буфера", async () => {
    const t = term("");
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(), readText: vi.fn().mockResolvedValue("вставка") }, configurable: true,
    });
    const r = press(clipboardKeys(t, "macos"), { code: "KeyV", metaKey: true });
    expect(r).toEqual({ passed: false, prevented: true });
    await vi.waitFor(() => expect(t.paste).toHaveBeenCalledWith("вставка"));
  });

  it("Windows: прежнее — Ctrl+Shift+C копирует, Ctrl+C и ⌘C не трогаем", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText, readText: vi.fn() }, configurable: true });
    const h = clipboardKeys(term("текст"), "windows");
    expect(press(h, { code: "KeyC", ctrlKey: true, shiftKey: true }).passed).toBe(false);
    expect(writeText).toHaveBeenCalledWith("текст");
    expect(press(h, { code: "KeyC", ctrlKey: true }).passed).toBe(true);
    expect(press(h, { code: "KeyC", metaKey: true }).passed).toBe(true);
  });
});
