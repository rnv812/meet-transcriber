import { saveText } from "./shell";

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
