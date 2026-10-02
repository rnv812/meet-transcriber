import { detectOs, IS_MAC, osTexts } from "./platform";

test("macOS узнаётся по platform или по Macintosh в userAgent", () => {
  expect(detectOs({ platform: "MacIntel", userAgent: "" })).toBe("macos");
  expect(detectOs({
    platform: "",
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
  })).toBe("macos");
  expect(detectOs({
    platform: "Win32",
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/140.0",
  })).toBe("windows");
  // jsdom: «(darwin) AppleWebKit … jsdom» — это не WKWebView, тексты Windows.
  expect(detectOs({ platform: "", userAgent: "Mozilla/5.0 (darwin) AppleWebKit/537.36 jsdom/26" }))
    .toBe("windows");
});

test("в тестах окно — как на Windows", () => {
  expect(IS_MAC).toBe(false);
});

test("тексты с названием ОС", () => {
  expect(osTexts("windows")).toEqual({
    keyring: "диспетчере учётных данных Windows",
    autostart: "Запускать вместе с Windows",
    trayArea: "области уведомлений",
    systemName: "Windows",
  });
  const mac = osTexts("macos");
  expect(mac.keyring).toBe("связке ключей macOS");
  expect(mac.autostart).toBe("Запускать при входе в macOS");
  expect(mac.trayArea).toBe("строке меню");
});
