/**
 * macOS: токен Hugging Face хранит оболочка (связка ключей от имени Meet.app),
 * поэтому сохранение и удаление идут командами оболочки, а не прямо в резидент.
 * На Windows — как раньше (`api.test.ts`).
 */

const invoke = vi.fn();

vi.mock("./platform", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./platform")>()),
  IS_MAC: true,
  OS: "macos",
}));
vi.mock("./shell", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./shell")>()),
  inTauri: () => true,
  invoke: (...args: unknown[]) => invoke(...args),
}));

const ep = { base: "http://h", token: "t" };

beforeEach(() => {
  invoke.mockReset();
  globalThis.fetch = vi.fn().mockRejectedValue(new Error("резидент напрямую не зовётся"));
});

test("Mac: сохранение токена — командой оболочки, результат проверки — её ответ", async () => {
  const check = { ok: true, reason: "ok", message: "Доступ есть" };
  invoke.mockResolvedValue(check);
  const api = await import("./api");
  expect(await api.setHfToken(ep, "hf_x")).toEqual(check);
  expect(invoke).toHaveBeenCalledWith("hf_token_save", { token: "hf_x" });
  expect(globalThis.fetch).not.toHaveBeenCalled();
});

test("Mac: удаление токена — командой оболочки", async () => {
  const status = { configured: false, source: null, check: null };
  invoke.mockResolvedValue(status);
  const api = await import("./api");
  expect(await api.deleteHfToken(ep)).toEqual(status);
  expect(invoke).toHaveBeenCalledWith("hf_token_delete");
  expect(globalThis.fetch).not.toHaveBeenCalled();
});
