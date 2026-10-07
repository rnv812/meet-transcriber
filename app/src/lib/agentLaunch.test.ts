import {
  ARGS_CONTROL, ARGS_QUOTE, MEETING_PROMPT, SESSION_ID, envText, maskValue, ourArgs, parseArgs, parseEnv,
  previewCommand, withUserArgs,
} from "./agentLaunch";

// Те же примеры, что у оболочки (pty.rs) и резидента (tests/test_agent_launch.py).
const CASES: [string, string[]][] = [
  ["", []],
  ["   ", []],
  ["--model opus", ["--model", "opus"]],
  ["--permission-mode  acceptEdits\t--verbose", ["--permission-mode", "acceptEdits", "--verbose"]],
  [String.raw`--add-dir D:\Docs`, ["--add-dir", String.raw`D:\Docs`]],
  [String.raw`--add-dir "D:\Мои документы\База"`, ["--add-dir", String.raw`D:\Мои документы\База`]],
  [String.raw`--add-dir \\server\share\kb`, ["--add-dir", String.raw`\\server\share\kb`]],
  [String.raw`"\\server\share\kb"`, [String.raw`\\server\share\kb`]],
  ['--x="a b" c', ["--x=a b", "c"]],
  ["'single quoted' \"\"", ["single quoted", ""]],
  [String.raw`"say \"hi\""`, ['say "hi"']],
  ["-m gpt-5 -c model_reasoning_effort=high", ["-m", "gpt-5", "-c", "model_reasoning_effort=high"]],
];

test.each(CASES)("разбор параметров: %j", (text, expected) => {
  expect(parseArgs(text)).toEqual({ args: expected, error: null });
});

test.each([
  [String.raw`--add-dir "D:\Docs`, ARGS_QUOTE],
  [String.raw`"D:\Docs\"`, ARGS_QUOTE],
  ["'abc", ARGS_QUOTE],
  ["--model opus\n--verbose", ARGS_CONTROL],
  ["a\u0000b", ARGS_CONTROL],
  ["'a\tb'", ARGS_CONTROL],
])("ошибка разбора: %j", (text, error) => {
  expect(parseArgs(text)).toEqual({ args: null, error });
});

test("переменные: строки ИМЯ=значение, пустые строки пропускаются, ошибки — с номером строки", () => {
  expect(parseEnv("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1\n\nEMPTY=\nX=a=b c")).toEqual({
    env: [
      { key: "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", value: "1" }, { key: "EMPTY", value: "" },
      { key: "X", value: "a=b c" },
    ],
    error: null,
    errors: [],
  });
  expect(parseEnv("A=1\nбез знака").error).toBe("Строка 2: нужен вид ИМЯ=значение");
  // Все ошибки — по строкам, каждая со своим номером.
  expect(parseEnv("A=1\nбез знака\n  B=2\n1C=3").errors).toEqual([
    "Строка 2: нужен вид ИМЯ=значение",
    "Строка 4: недопустимое имя «1C» (латинские буквы, цифры и _, не с цифры)",
  ]);
  expect(parseEnv("  B=2  ").env).toEqual([{ key: "B", value: "2  " }]);
  expect(parseEnv("1A=x").error).toMatch(/^Строка 1: недопустимое имя «1A»/);
  expect(parseEnv("A=x\u0007").error).toBe("Строка 1: управляющие символы в значении недопустимы");
  expect(parseEnv("Path=1\nPATH=2").error).toBe("Строка 2: переменная PATH уже задана");
  expect(envText([{ key: "A", value: "1" }, { key: "B", value: "" }])).toBe("A=1\nB=");
  expect(envText("как набрано")).toBe("как набрано");
});

test("свои параметры — после наших; наш дубликат убирается, где повтор — ошибка или лишний", () => {
  const claude = ourArgs("claude-code", String.raw`D:\kb`, true, "opus");
  expect(claude).toEqual(["--resume", SESSION_ID, "--add-dir", String.raw`D:\kb`, "--append-system-prompt", MEETING_PROMPT,
    "--model", "opus"]);
  expect(ourArgs("claude-code", null).slice(0, 2)).toEqual(["--session-id", SESSION_ID]);
  // Свой --model — его, наш убирается.
  expect(withUserArgs("claude-code", claude, ["--model", "fable"])).toEqual([...claude.slice(0, -2), "--model", "fable"]);
  expect(withUserArgs("claude-code", claude, ["--model=fable"])).toEqual([...claude.slice(0, -2), "--model=fable"]);
  for (const user of [["--resume", "abc"], ["-c"], ["--session-id", "abc"], ["--continue"]]) {
    const args = withUserArgs("claude-code", claude, user);
    expect(args).toEqual([...claude.slice(2), ...user]);
  }
  expect(withUserArgs("claude-code", ourArgs("claude-code", null), ["-r"])).not.toContain(SESSION_ID);
  const codex = ourArgs("codex", null, true);
  const merged = withUserArgs("codex", codex, ["--last", "-C", String.raw`D:\other`]);
  expect(merged[0]).toBe("resume");
  expect(merged.filter((a) => a === "--last")).toHaveLength(1);
  expect(merged).not.toContain("--cd");
  expect(merged.slice(-3)).toEqual(["--last", "-C", String.raw`D:\other`]);
});

test("Claude во вкладке «Агент» — всегда с моделью из настроек (llm.model), пусто — sonnet", () => {
  for (const resume of [false, true]) {
    const args = ourArgs("claude-code", null, resume, "opus");
    expect(args.slice(-2)).toEqual(["--model", "opus"]);
  }
  expect(ourArgs("claude-code", null, false, "  ").slice(-2)).toEqual(["--model", "sonnet"]);
  expect(ourArgs("claude-code", null).slice(-2)).toEqual(["--model", "sonnet"]);
  expect(ourArgs("codex", null, false, "opus")).not.toContain("--model");
  expect(ourArgs("opencode", null, true, "opus")).toEqual(["--continue"]);
  expect(previewCommand("claude-code", { args: "", env: [] }, null, "opus")).toBe(
    "claude --session-id \"<id сеанса>\" --append-system-prompt \"<подсказка о встрече>\" --model opus",
  );
});

test("строка «Команда запуска»: переменные, программа, наши и свои параметры; секреты скрыты", () => {
  expect(previewCommand("claude-code", {
    args: "--model opus --add-dir \"D:\\Мои документы\"",
    env: [{ key: "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", value: "1" }, { key: "MY_API_TOKEN", value: "abc" }],
  }, String.raw`D:\kb`)).toBe(
    "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 MY_API_TOKEN=*** claude --session-id \"<id сеанса>\" --add-dir D:\\kb "
    + "--append-system-prompt \"<подсказка о встрече>\" --model opus --add-dir \"D:\\Мои документы\"",
  );
  expect(previewCommand("codex", { args: "-m gpt-5", env: [] }, null)).toBe(
    "codex --cd \"<папка встречи>\" -c \"developer_instructions=<подсказка о встрече>\" -m gpt-5",
  );
  expect(previewCommand("codex", { args: "\"open", env: [] }, null)).toBeNull();
  expect(previewCommand("codex", { args: "", env: "A" }, null)).toBeNull();
  expect(maskValue("PASSWORD", "x")).toBe("***");
  expect(maskValue("CODEX_HOME", "D:\\c")).toBe("D:\\c");
});

test("OpenCode: наши аргументы — только --continue для «Продолжить»; свой выбор сеанса его убирает", () => {
  expect(ourArgs("opencode", "D:\\kb")).toEqual([]);
  expect(ourArgs("opencode", null, true)).toEqual(["--continue"]);
  const ours = ourArgs("opencode", null, true);
  for (const user of [["-s", "ses_1"], ["--session=ses_1"], ["-c"], ["--continue"]]) {
    expect(withUserArgs("opencode", ours, user)).toEqual(user);
  }
  expect(withUserArgs("opencode", ours, ["-m", "openai/gpt-5"])).toEqual(["--continue", "-m", "openai/gpt-5"]);
  expect(previewCommand("opencode", { args: "--agent plan", env: [{ key: "OPENAI_API_KEY", value: "sk" }] }, "D:\\kb"))
    .toBe("OPENAI_API_KEY=*** opencode --agent plan");
});
