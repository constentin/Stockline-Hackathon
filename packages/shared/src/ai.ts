/**
 * Contract of the AI service (apps/ai, default http://localhost:4100).
 *
 *  GET  /health                         → { ok, llm: {provider, model}, stt: {provider}, tts: {provider} }
 *  GET  /tools                          → ToolInfo[]  (tools the harness exposes to the model)
 *  POST /chat                           body ChatRequest → text/event-stream of ChatStreamEvent (one JSON per `data:` line)
 *  WS   /voice                          bidirectional, see VoiceClientMessage / VoiceServerMessage
 *  POST /stt                            raw audio body (audio/webm | audio/wav) → { text, ms }
 *  POST /tts                            body { text, voice? } → audio bytes (audio/mpeg or audio/wav), header x-ms
 *  POST /command[?speak=1]              single-turn voice COMMAND (stateless, act-first, one-line answer).
 *                                       body = raw audio (content-type audio/webm|wav…, header x-mock-text for the mock STT)
 *                                       or JSON { text, actorName? } → text/event-stream of CommandStreamEvent:
 *                                       transcript → (text | tool_call | tool_result)* → navigate? → speech? → done (with timings) | error.
 *                                       speak=1 → a `speech` event (base64 audio; MOCK_TTS_MIME = speak the text in the browser).
 *  WS   /voice/command[?speak=1]        the same command with LIVE transcription: send audio_start {mime, hint?}, binary frames
 *                                       while the key is down, audio_end (or one { type: "text" }); receive CommandStreamEvent JSON
 *                                       (transcript final=false while speaking, then the POST /command events). Closed after done | error.
 *  GET  /phone                          → PhoneInfo: can the agent ring a real phone (Twilio), which one (masked)
 *  POST /phone/call                     body PhoneCallRequest → PhoneCallResponse: ring TWILIO_TO_NUMBER for a campaign call
 *  POST /phone/hangup                   body PhoneCallRequest → 204: hang up the phone call in progress
 *  WS   /voice/phone                    receive only: the phone call's VoiceServerMessage JSON (no audio), to show it live
 */

export type ChatRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  toolCallId?: string; // for role "tool"
  toolCalls?: ToolCall[]; // for role "assistant"
  name?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown>; // JSON schema
}

export interface ChatRequest {
  messages: ChatMessage[]; // full conversation so far (no system message; the harness adds it)
  mode?: "chat" | "voice" | "command" | "outbound"; // voice → short spoken-style answers; command → single-turn, act-first, one line; outbound → clearance call (needs a call brief: WS /voice)
  actorName?: string;
}

export type ChatStreamEvent =
  | { type: "text"; delta: string }
  | { type: "tool_call"; call: ToolCall }
  | { type: "tool_result"; callId: string; name: string; ok: boolean; result: unknown; ms: number }
  | { type: "done"; message: ChatMessage; usage?: { inputTokens?: number; outputTokens?: number; cacheCreationInputTokens?: number; cacheReadInputTokens?: number }; ms: number; timings?: TurnTimings }
  | { type: "error"; message: string };

// ── Voice (push-to-talk, two tracks: the human "caller" and the agent) ──

export type VoiceClientMessage =
  // opens a conversation; campaignCallId (ccl_…) = an outbound clearance call: fresh history, the AGENT speaks first
  | { type: "start"; sessionId?: string; actorName?: string; campaignCallId?: string }
  | { type: "audio_start"; mime: string; hint?: string } // caller pressed space (hint: optional STT vocabulary; mock STT returns it)
  | { type: "audio_end" } // caller released space; binary frames sent in between (every 100–250 ms) are the utterance, streamed to the STT as they arrive
  | { type: "text"; text: string } // typed instead of spoken (debug)
  | { type: "reset" }
  | { type: "hangup" } // ends the outbound call: outcome recorded if the agent did not, then call_ended
  // hands-free (only when ready.handsFree): binary frames stream continuously, the server detects the ends of sentences
  | { type: "listen_start"; mime: string }
  | { type: "listen_stop" }
  | { type: "playback"; playing: boolean }; // the agent's audio starts / stops playing (echo, barge-in)

export interface TurnTimings {
  sttMs?: number;
  llmFirstTokenMs?: number;
  llmTotalMs?: number;
  ttsFirstByteMs?: number;
  totalMs?: number; // end of caller speech → first agent audio byte
  // outbound calls
  endpointMs?: number; // hands-free: last word heard → end of turn decided (included in totalMs)
  routeMs?: number; // Jev routing still to wait at the start of the turn
  intent?: string; // what Jev understood (valide, refuse, negocie…)
  action?: string; // what the code did before the model (confirmed, declined, do_not_call)
  model?: string; // the model that answered
  fillerMs?: number; // end of caller speech → pre-recorded waiting phrase sent
  ackMs?: number; // end of caller speech → first word of the agent's immediate reply (small model, services/ack.py)
}
/** The TurnTimings fields that are durations in ms (not intent / action / model). */
export type TurnTimingMs = { [K in keyof TurnTimings]-?: NonNullable<TurnTimings[K]> extends number ? K : never }[keyof TurnTimings];

export type VoiceServerMessage =
  | { type: "ready"; sessionId: string; stt: string; tts: string; llm: string; handsFree?: boolean }
  | { type: "caller_speaking" } // hands-free: the caller started a sentence (the agent was stopped if it was talking)
  | { type: "listen_stopped"; reason: string } // hands-free stream failed server-side: restart it (new recording) or use Space
  // While the caller speaks (streaming STT): final=false, the whole text heard so far, resent as words are recognised;
  // `stable` = its leading part made of final words (won't change). Then once final=true: what the agent answers.
  | { type: "transcript"; track: "caller"; text: string; final: boolean; stable?: string }
  | { type: "agent_text"; delta: string }
  | { type: "agent_tool"; name: string; ok?: boolean; ms?: number; args?: Record<string, unknown> }
  | { type: "agent_audio_start"; mime: string } // binary frames follow
  | { type: "agent_audio_end" }
  | { type: "agent_done"; text: string; timings: TurnTimings }
  | { type: "error"; message: string }
  // outbound call opened (after ready): the agent's first turn follows. offers = "Moule … : 120 kg à 2,98 € (−20 %)"
  | { type: "call_started"; callId: string; clientName: string; offers: string[] }
  // after hangup; outcome = recorded at hang-up, absent when the agent had already recorded it
  | { type: "call_ended"; callId: string; outcome?: string };

/**
 * Mime returned by the AI service's mock TTS (no key configured): the bytes are a short silent WAV.
 * Clients should ignore the audio and speak the agent text with the browser's speechSynthesis instead.
 */
export const MOCK_TTS_MIME = "audio/x-mock-wav";

// ── Voice command (POST /command: one utterance → one action → one line) ──

/** JSON body of POST /command (or send raw audio instead). */
export interface CommandRequest {
  text: string;
  actorName?: string;
}

export type CommandStreamEvent =
  | ChatStreamEvent
  // final (default true) comes before the agent's events; ms = STT time, 0 for typed text.
  // WS /ai/voice/command also sends final=false ones while the user speaks (text so far, `stable` = final words).
  | { type: "transcript"; text: string; ms: number; final?: boolean; stable?: string }
  | { type: "navigate"; path: string; label?: string } // the user asked to SEE an entity: web route to open (e.g. /products/prd_0012)
  | { type: "speech"; mime: string; audio: string; ms: number }; // ?speak=1: base64 audio of the answer; mime MOCK_TTS_MIME → speechSynthesis

// ── Phone calls through Twilio (apps/ai/app/services/phone.py, GUIDE-TELEPHONE.md) ──

export interface PhoneInfo {
  enabled: boolean;
  to?: string; // the number called, masked ("+336 •• •• •• 78")
  reason?: string; // why it is disabled (missing .env settings, unsupported TTS / STT)
}

export interface PhoneCallRequest { campaignCallId: string }

export interface PhoneCallResponse { callSid: string; to: string }
