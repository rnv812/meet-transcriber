/** Заготовки чата агента-участника для тестов окна. */

import type { AgentInfo, ChatMessage } from "../lib/types";

export const agentInfo = (o: Partial<AgentInfo> = {}): AgentInfo => ({
  state: "listening", error: null, provider: "claude-code", label: "Claude Code (sonnet)", vision: true, tools: true,
  deny_enforced: true, frequency: "чаще", session: "new", writing: null,
  sees: { conversation: true, kb: true, materials: 0, images: 0 }, ...o,
});

let seq = 0;
export const agentMsg = (id: string, o: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id, seq: ++seq, at: 1_800_000_000, kind: "agent", status: "shown", mode: "proactive", text: `Сообщение ${id}`, t: 65, ...o });
export const userMsg = (id: string, o: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id, seq: ++seq, at: 1_800_000_000, kind: "user", text: `Вопрос ${id}`, t: 70, ...o });
export const attMsg = (id: string, o: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id, seq: ++seq, at: 1_800_000_000, kind: "attachment", type: "image", name: "Скриншот.png", status: "ready", ...o });
