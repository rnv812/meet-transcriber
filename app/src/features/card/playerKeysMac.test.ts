import { describe, expect, it } from "vitest";
import { playerKey } from "./AudioPlayer";

const base = { key: "ArrowRight", code: "ArrowRight", ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
  defaultPrevented: false, target: document.body } as const;

describe("клавиши плеера по ОС", () => {
  it("Mac: ⌥+→ и ⌥+← — к соседней реплике, Ctrl+стрелки не наши", () => {
    expect(playerKey({ ...base, altKey: true }, "macos")).toEqual({ kind: "turn", dir: 1 });
    expect(playerKey({ ...base, key: "ArrowLeft", code: "ArrowLeft", altKey: true }, "macos")).toEqual({ kind: "turn", dir: -1 });
    expect(playerKey({ ...base, ctrlKey: true }, "macos")).toBeNull();
    expect(playerKey({ ...base }, "macos")).toEqual({ kind: "seek", by: 5 });
  });

  it("Windows: Ctrl+→ — к реплике, ⌥+→ не наши", () => {
    expect(playerKey({ ...base, ctrlKey: true }, "windows")).toEqual({ kind: "turn", dir: 1 });
    expect(playerKey({ ...base, altKey: true }, "windows")).toBeNull();
  });
});
