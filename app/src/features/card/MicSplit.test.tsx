/**
 * Микрофон по голосам в окне (0.3.3, meet.mic_split): «в комнате» у людей
 * рядом с владельцем, «голос под вопросом» вместо «нахлёста» на микрофоне,
 * бейдж дорожки и список «Убрано с микрофона» в панели «Спикеры», подсказки
 * карточки по статусу разделения.
 */
import { createRef } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { mergeTurns } from "../../lib/speakers";
import { stageLabel } from "../../lib/status";
import type { Segment, SpeakerRow, SpeakersView } from "../../lib/types";
import { MicSplitNote, micSplitHint, removedSummary } from "./micSplit";
import { SpeakersPanel } from "./speakers/SpeakersPanel";
import { Turns } from "./Turns";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSpeakers: vi.fn(),
  applySpeakers: vi.fn(),
}));

const seg = (start: number, speaker: string, text: string, extra: Partial<Segment> = {}): Segment => ({
  start, end: start + 1, speaker, text, uncertain: false, ...extra,
});

describe("реплики", () => {
  const segments: Segment[] = [
    seg(0, "Спикер 1", "Добрый день.", { uncertain: true }),
    seg(2, "Вы", "Да, слышно.", { track: "mic" }),
    seg(4, "Спикер 2", "А машина есть?", { track: "mic", room: true }),
    seg(5, "Спикер 2", "К четвергу.", { track: "mic", room: true }),
    seg(8, "Вы", "Наверное.", { track: "mic", uncertain: true }),
  ];

  test("склейка: «в комнате» и «голос под вопросом» — свои флаги, не нахлёст", () => {
    const turns = mergeTurns(segments);
    expect(turns.map((t) => [t.speaker, !!t.room, !!t.unsure, t.uncertain])).toEqual([
      ["Спикер 1", false, false, true],
      ["Вы", false, false, false],
      ["Спикер 2", true, false, false],
      ["Вы", false, true, false],
    ]);
  });

  test("звонок и микрофон одного человека, голос под вопросом — отдельные реплики", () => {
    const turns = mergeTurns([
      seg(0, "Анна", "В звонке."),
      seg(1.5, "Анна", "В комнате.", { track: "mic", room: true }),
      seg(3, "Вы", "Да.", { track: "mic" }),
      seg(4, "Вы", "Наверное.", { track: "mic", uncertain: true }),
      seg(5, "Вы", "Точно.", { track: "mic", uncertain: true }),
    ]);
    expect(turns.map((t) => [t.texts.join(" "), !!t.room, !!t.unsure])).toEqual([
      ["В звонке.", false, false],
      ["В комнате.", true, false],
      ["Да.", false, false],
      ["Наверное. Точно.", false, true],
    ]);
  });

  test("у человека в комнате — пометка, у сомнительного голоса микрофона — своя", () => {
    render(<Turns turns={mergeTurns(segments)} colors={new Map()} playable={false} onPlay={() => {}} />);
    const rows = document.querySelectorAll("[data-turn]");
    expect(within(rows[0] as HTMLElement).getByText("(нахлёст)")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).queryByText("в комнате")).toBeNull();
    expect(within(rows[2] as HTMLElement).getByText("в комнате")).toHaveAttribute("title",
      expect.stringMatching(/микрофон/));
    expect(within(rows[3] as HTMLElement).getByText("(голос под вопросом)")).toBeInTheDocument();
    expect(within(rows[3] as HTMLElement).queryByText("(нахлёст)")).toBeNull();
  });

  test("шаг расшифровки «голоса микрофона» назван по-своему", () => {
    expect(stageLabel({ stage: "voices", note: "mic", label: "голоса микрофона" })).toBe("Голоса микрофона");
    expect(stageLabel({ stage: "voices", note: null, label: "сопоставление голосов" })).toBe("Узнавание голосов");
  });
});

describe("панель «Спикеры»", () => {
  const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
    label, name: /^Спикер/.test(label) ? null : label, seconds: 60, share: 0.25, turns: 4,
    samples: [], has_voice: true, suggestions: [], ...extra,
  });
  const view = (extra: Partial<SpeakersView> = {}): SpeakersView => ({
    owner: "Вы", history: [], pos: 0,
    speakers: [row("Спикер 1", { track: "sys" }), row("Спикер 2", { track: "mic", room: true }),
      row("Анна", { track: "mixed", room: true }), row("Вы", { track: "mic", owner_voice_only: true }),
      // Владелец под прежним именем или переименованный «Вы»: резидент говорит — не в комнате.
      row("Кузьма", { track: "mic", room: false })],
    mic_split: { rule: 1, status: "ok", owner_profile: "enroll", room_speakers: 1,
      dropped: { echo: 1, neighbour: 2, owner_leak: 0 } },
    mic_removed: [
      { start: 20, end: 21.5, text: "да слышно", reason: "echo", track: "mic" },
      { start: 30, end: 31, text: "всем привет", reason: "neighbour", track: "mic" },
      { start: 40, end: 41, text: "до завтра", reason: "neighbour", track: "mic" },
    ],
    ...extra,
  });

  function setup(props: Partial<Parameters<typeof SpeakersPanel>[0]> = {}) {
    const cardRef = createRef<HTMLElement>();
    const onPlay = vi.fn();
    render(
      <section ref={cardRef}>
        <SpeakersPanel endpoint={{ base: "/api", token: null }} recordingId="r1" people={[]} open focus={null}
          version={1} playable cardRef={cardRef} onClose={vi.fn()} onPlay={onPlay} onShowTurns={vi.fn()}
          onChanged={vi.fn()} {...props} />
      </section>,
    );
    return { onPlay };
  }

  beforeEach(() => vi.mocked(api.getSpeakers).mockResolvedValue(view()));

  test("бейдж дорожки: «в комнате» у людей у микрофона, у владельца — нет", async () => {
    setup();
    const room = await screen.findByRole("region", { name: /^Спикер 2/ });
    expect(within(room).getByText("в комнате")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: /^Анна/ })).getByText("в звонке и в комнате"))
      .toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: /^Спикер 1/ })).queryByText(/комнате/)).toBeNull();
    expect(within(screen.getByRole("region", { name: /^Вы/ })).queryByText(/комнате/)).toBeNull();
    expect(within(screen.getByRole("region", { name: /^Кузьма/ })).queryByText(/комнате/)).toBeNull();
  });

  test("голос владельца не предлагают запомнить в базу людей", async () => {
    setup();
    const me = await screen.findByRole("region", { name: /^Вы/ });
    await userEvent.click(within(me).getByRole("button", { name: "Назначить…" }));
    await userEvent.type(screen.getByRole("combobox"), "Пётр{Enter}");
    expect(within(me).queryByRole("checkbox", { name: /Запомнить голос/ })).toBeNull();
    const room = screen.getByRole("region", { name: /^Спикер 2/ });
    await userEvent.click(within(room).getByRole("button", { name: "Назначить…" }));
    await userEvent.type(screen.getByRole("combobox"), "Ольга{Enter}");
    expect(within(room).getByRole("checkbox", { name: /Запомнить голос/ })).toBeInTheDocument();
  });

  test("голос-кандидат: на строке «Вы» честный вопрос, флажок выключен, «Применить» — с подтверждением", async () => {
    vi.mocked(api.getSpeakers).mockResolvedValue(view({ owner_voice: true, owner_voice_candidate: true }));
    vi.mocked(api.applySpeakers).mockResolvedValue(view());
    setup();
    const me = await screen.findByRole("region", { name: /^Вы/ });
    expect(within(me).getByText("Голос не совпал с образцом — это точно вы?")).toBeInTheDocument();
    const box = within(me).getByRole("checkbox", { name: /Запомнить мой голос/ });
    expect(box).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Применить" })).toBeNull();
    await userEvent.click(box);
    expect(screen.getByText("Будет запомнен ваш голос из этой встречи")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(api.applySpeakers).toHaveBeenCalledWith(expect.anything(), "r1", [], {}, true, true);
  });

  test("голос-кандидат при «Это я» на другой строке — флажок есть, но выключен", async () => {
    vi.mocked(api.getSpeakers).mockResolvedValue(view({ owner_voice: true, owner_voice_candidate: true }));
    vi.mocked(api.applySpeakers).mockResolvedValue(view());
    setup();
    const room = await screen.findByRole("region", { name: /^Спикер 2/ });
    await userEvent.click(within(room).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(screen.getByRole("option", { name: /Это я — Вы/ }));
    const box = within(room).getByRole("checkbox", { name: /Запомнить мой голос/ });
    expect(box).not.toBeChecked();
    expect(within(room).getByText("Голос не совпал с образцом — это точно вы?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    expect(api.applySpeakers).toHaveBeenLastCalledWith(expect.anything(), "r1", expect.any(Array), {}, false);
  });

  test("подтверждённый голос владельца: на строке «Вы» флажка нет, при «Это я» — включён", async () => {
    vi.mocked(api.getSpeakers).mockResolvedValue(view({ owner_voice: true, owner_voice_candidate: false }));
    setup();
    const me = await screen.findByRole("region", { name: /^Вы/ });
    expect(within(me).queryByRole("checkbox", { name: /Запомнить мой голос/ })).toBeNull();
    const room = screen.getByRole("region", { name: /^Спикер 2/ });
    await userEvent.click(within(room).getByRole("button", { name: "Назначить…" }));
    await userEvent.click(screen.getByRole("option", { name: /Это я — Вы/ }));
    expect(within(room).getByRole("checkbox", { name: /Запомнить мой голос/ })).toBeChecked();
    expect(within(room).queryByText(/не совпал с образцом/)).toBeNull();
  });

  test("«Показать» из карточки раскрывает список убранного", async () => {
    const cardRef = createRef<HTMLElement>();
    render(
      <section ref={cardRef}>
        <SpeakersPanel endpoint={{ base: "/api", token: null }} recordingId="r1" people={[]} open focus={null}
          version={1} playable cardRef={cardRef} onClose={vi.fn()} onPlay={vi.fn()} onShowTurns={vi.fn()}
          onChanged={vi.fn()} removedAsk={1} />
      </section>,
    );
    const box = await screen.findByRole("group", { name: "Убрано с микрофона" });
    expect(within(box).getByRole("button", { name: "Скрыть" })).toHaveAttribute("aria-expanded", "true");
    expect(within(box).getAllByRole("listitem")).toHaveLength(3);
  });

  test("«Убрано с микрофона»: счёт, «Показать» и ▶ у каждой убранной фразы", async () => {
    const { onPlay } = setup();
    const box = await screen.findByRole("group", { name: "Убрано с микрофона" });
    expect(within(box).getByText("Убрано с микрофона: 2 дубля соседа, 1 эхо")).toBeInTheDocument();
    expect(within(box).queryByText("всем привет")).toBeNull();
    const show = within(box).getByRole("button", { name: "Показать" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(show);
    expect(within(box).getByRole("button", { name: "Скрыть" })).toHaveAttribute("aria-expanded", "true");
    const items = within(box).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining("эхода слышно"),
      expect.stringContaining("дубль соседавсем привет"),
      expect.stringContaining("дубль соседадо завтра"),
    ]);
    await userEvent.click(within(items[1]!).getByRole("button", { name: "Прослушать убранное с 00:30" }));
    expect(onPlay).toHaveBeenCalledWith(30, 31);
  });

  test("без звука ▶ у убранного неактивна; нечего показать — блока нет", async () => {
    setup({ playable: false });
    const box = await screen.findByRole("group", { name: "Убрано с микрофона" });
    await userEvent.click(within(box).getByRole("button", { name: "Показать" }));
    expect(within(box).getByRole("button", { name: "Прослушать убранное с 00:20" })).toBeDisabled();
  });

  test("старый резидент или ничего не убрано — блока нет", async () => {
    vi.mocked(api.getSpeakers).mockResolvedValue(view({ mic_removed: [] }));
    setup();
    await screen.findByRole("region", { name: /^Спикер 1/ });
    expect(screen.queryByRole("group", { name: "Убрано с микрофона" })).toBeNull();
  });
});

describe("подсказки карточки", () => {
  test("текст по каждому статусу разделения", () => {
    expect(micSplitHint({ status: "no_profile" })?.text).toMatch(/^Микрофон не разделён на голоса/);
    expect(micSplitHint({ status: "no_profile" })?.action).toBe("sound");
    expect(micSplitHint({ status: "owner_not_found" })?.text).toMatch(/не найден/);
    expect(micSplitHint({ status: "owner_not_found" })?.action).toBe("sound");
    expect(micSplitHint({ status: "no_voice" })?.text).toMatch(/мало речи/);
    expect(micSplitHint({ status: "skipped_error" })?.text).toMatch(/ошибк/);
    expect(micSplitHint({ status: "skipped_no_token" })?.text).toMatch(/Hugging Face/);
    expect(micSplitHint({ status: "skipped_no_token" })?.action).toBe("engine");
    // Без токена диаризации уже есть баннер «настройте Hugging Face» — второй раз не говорим,
    // и образец без него не записать.
    expect(micSplitHint({ status: "skipped_no_token" }, "skipped_no_token")).toBeNull();
    expect(micSplitHint({ status: "no_profile" }, "skipped_no_token")).toBeNull();
    expect(micSplitHint({ status: "ok" })).toBeNull();
    expect(micSplitHint({ status: "off" })).toBeNull();
    expect(micSplitHint(null)).toBeNull();
  });

  test("сводка убранного по причинам", () => {
    expect(removedSummary({ echo: 3, neighbour: 6, owner_leak: 0 })).toBe("6 дублей соседа, 3 эха");
    expect(removedSummary({ neighbour: 1, owner_leak: 2 })).toBe("1 дубль соседа, 2 ваши фразы через звонок");
    expect(removedSummary({})).toBe("");
  });

  test("подсказка с кнопкой в настройки и строка об убранных повторах", async () => {
    const onOpenSettings = vi.fn();
    const onShowRemoved = vi.fn();
    const { rerender } = render(<MicSplitNote info={{ status: "no_profile", room_speakers: 0, dropped: {} }}
      onOpenSettings={onOpenSettings} onShowRemoved={onShowRemoved} />);
    // Без резидента (нечем открыть окно записи) — тихая строка и переход в настройки.
    expect(screen.getByRole("note")).toHaveTextContent(/Запишите образец своего голоса/);
    await userEvent.click(screen.getByRole("button", { name: "Записать образец" }));
    expect(onOpenSettings).toHaveBeenCalledWith("sound");

    rerender(<MicSplitNote info={{ status: "ok", room_speakers: 1, dropped: { neighbour: 2, echo: 0 } }}
      onOpenSettings={onOpenSettings} onShowRemoved={onShowRemoved} />);
    expect(screen.getByRole("note")).toHaveTextContent("С микрофона убраны повторы: 2 дубля соседа");
    await userEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(onShowRemoved).toHaveBeenCalled();

    // Убрано только из звука собеседников (ваш голос через звонок) — не «с микрофона».
    rerender(<MicSplitNote info={{ status: "ok", room_speakers: 0, dropped: { owner_leak: 2 } }}
      onOpenSettings={onOpenSettings} onShowRemoved={onShowRemoved} />);
    expect(screen.getByRole("note")).toHaveTextContent("Убраны повторы: 2 ваши фразы через звонок");
    expect(screen.getByRole("note")).not.toHaveTextContent("С микрофона");

    rerender(<MicSplitNote info={{ status: "ok", room_speakers: 0, dropped: { neighbour: 0 } }}
      onOpenSettings={onOpenSettings} onShowRemoved={onShowRemoved} />);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
