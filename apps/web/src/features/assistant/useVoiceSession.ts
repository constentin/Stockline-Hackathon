/**
 * Push-to-talk voice session over WS /ai/voice (contract: VoiceClientMessage / VoiceServerMessage in @kit/shared).
 * Mic → MediaRecorder (webm/opus) chunks every 100 ms, streamed as binary frames between audio_start and audio_end:
 * the server transcribes them as they arrive and sends `transcript` final:false (live text) before the final one.
 * Agent audio: binary frames between agent_audio_start/end → one Blob → <audio>. Mock TTS → speechSynthesis (fr-FR).
 * Outbound clearance call: dial(callId) restarts the session on that call (the agent speaks first), hangUp() ends it.
 * Hands-free (startListening, when the STT supports it): the mic streams the whole call, the server detects the end of
 * each sentence and sends `caller_speaking` when the caller starts one (the agent's audio is cut: barge-in). The page
 * tells the server when the agent's audio plays (`playback`), so its own voice coming back through the mic is ignored.
 * Phone call (Twilio, when the AI service is configured for it): dialPhone(callId) makes the agent ring YOUR phone; the
 * audio goes phone ↔ server, and the page only WATCHES (WS /ai/voice/phone: the same messages, no audio), so the
 * conversation, the tools and the timings show live here. hangUpPhone() ends the call through Twilio.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { MOCK_TTS_MIME, type PhoneCallRequest, type PhoneInfo, type TurnTimings, type VoiceClientMessage, type VoiceServerMessage } from "@kit/shared";
import { AI_BASE } from "@/lib/api";
import { toast } from "@/ui/toast";
import { SegmentPlayer } from "./audio-player";
import type { ToolRun } from "./tools";

export type Conn = "connecting" | "ready" | "offline";
export type Activity = "idle" | "recording" | "thinking" | "speaking";
export type MicState = "unknown" | "granted" | "denied" | "unsupported";

export interface VoiceTurn {
  id: number;
  /** text: live while recording/transcribing (stable = its final words), definitive once status is "final". */
  caller: { text: string; stable?: string; at: number; status: "recording" | "transcribing" | "final"; typed?: boolean; cue?: boolean };
  agent: { text: string; at?: number; tools: ToolRun[]; done: boolean; error?: string; interrupted?: boolean; unheard?: string };
  timings?: TurnTimings;
  /** Client-side: caller release → agent audio actually starts playing (or speech synthesis starts). */
  perceivedMs?: number;
}

export interface Providers { llm: string; stt: string; tts: string; sessionId: string; handsFree: boolean }

/** The outbound call of the session: dialing → live (call_started) → ending (hangup sent) → ended (call_ended). */
export interface OutboundCallState {
  callId: string;
  clientName: string;
  offers: string[];
  status: "dialing" | "live" | "ending" | "ended";
  /** recorded at hang-up by the server; null = the agent had recorded it itself */
  outcome?: string | null;
  error?: string;
  /** a real phone call through Twilio: the audio is on the phone, this page only shows the conversation */
  phone?: boolean;
}

const REC_MIME = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
const wsUrl = (path = "/voice") => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${AI_BASE}${path}`;

async function postPhone(path: string, body: PhoneCallRequest): Promise<string | undefined> {
  try {
    const res = await fetch(`${AI_BASE}/phone/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (res.ok) return undefined;
    const j = await res.json().catch(() => undefined);
    return j?.error?.message ?? `Erreur ${res.status}`;
  } catch {
    return "Service IA injoignable (localhost:4100)";
  }
}

export function useVoiceSession() {
  const [conn, setConn] = useState<Conn>("connecting");
  const [activity, setActivity] = useState<Activity>("idle");
  const [mic, setMic] = useState<MicState>("unknown");
  const [providers, setProviders] = useState<Providers>();
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [level, setLevel] = useState(0);
  const [lastError, setLastError] = useState<string>();
  const [call, setCall] = useState<OutboundCallState>();
  const [listening, setListening] = useState(false);
  const [phone, setPhone] = useState<PhoneInfo>();

  const ws = useRef<WebSocket>();
  const sessionId = useRef<string>();
  const alive = useRef(true);
  const turnSeq = useRef(0);
  const currentTurn = useRef<number>();
  // mic
  const stream = useRef<MediaStream>();
  const recorder = useRef<MediaRecorder>();
  const pressed = useRef(false);
  const listenRec = useRef<MediaRecorder>(); // hands-free recorder (whole call)
  const audioCtx = useRef<AudioContext>();
  const analyser = useRef<AnalyserNode>();
  const raf = useRef<number>();
  // playback
  const player = useRef<SegmentPlayer>();
  const audioMime = useRef<string>();
  const discardAudio = useRef(false); // audio of an interrupted turn still draining
  const releasedAt = useRef<number>();
  const turnHasAudio = useRef(false);

  const patchTurn = useCallback((id: number | undefined, fn: (t: VoiceTurn) => VoiceTurn) => {
    if (id == null) return;
    setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));
  }, []);

  const newTurn = useCallback((caller: VoiceTurn["caller"]) => {
    const id = ++turnSeq.current;
    const prev = currentTurn.current;
    currentTurn.current = id;
    turnHasAudio.current = false;
    audioMime.current = undefined;
    discardAudio.current = true;
    setTurns((ts) => [
      ...ts.map((t) => (t.id === prev && !t.agent.done ? { ...t, agent: { ...t.agent, done: true, interrupted: true } } : t)),
      { id, caller, agent: { text: "", tools: [], done: false } },
    ]);
    return id;
  }, []);

  const send = useCallback((m: VoiceClientMessage) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(m));
  }, []);

  // ── Playback ──
  const playing = useRef(false);
  const setPlaying = useCallback((on: boolean, playedMs?: number) => {
    if (playing.current === on) return;
    playing.current = on;
    // hands-free: the server ignores the agent's own voice meanwhile; playedMs (cut by the caller) = what he heard
    send(playedMs != null ? { type: "playback", playing: on, playedMs } : { type: "playback", playing: on });
  }, [send]);

  const stopPlayback = useCallback(() => {
    try { window.speechSynthesis?.cancel(); } catch { /* ignore */ }
    const p = player.current;
    player.current = undefined;
    const playedMs = p?.playedMs;
    p?.stop();
    setPlaying(false, playedMs);
    setActivity((a) => (a === "speaking" ? "idle" : a));
  }, [setPlaying]);

  const markPerceived = useCallback((id: number | undefined) => {
    const t0 = releasedAt.current;
    if (t0 == null) return;
    releasedAt.current = undefined;
    patchTurn(id, (t) => ({ ...t, perceivedMs: Math.round(performance.now() - t0) }));
  }, [patchTurn]);

  const speak = useCallback((text: string, id: number | undefined) => {
    const synth = window.speechSynthesis;
    if (!synth || !text.trim()) { setActivity("idle"); return; }
    stopPlayback();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "fr-FR";
    const fr = synth.getVoices().find((v) => v.lang?.toLowerCase().startsWith("fr"));
    if (fr) u.voice = fr;
    u.rate = 1.08;
    u.onstart = () => { setActivity("speaking"); setPlaying(true); markPerceived(id); };
    u.onend = u.onerror = () => { setPlaying(false); setActivity((a) => (a === "speaking" ? "idle" : a)); };
    setActivity("speaking");
    synth.speak(u);
  }, [markPerceived, setPlaying, stopPlayback]);

  // ── Server messages ──
  const onMessage = useCallback((ev: MessageEvent) => {
    if (typeof ev.data !== "string") {
      if (!discardAudio.current) player.current?.push(ev.data as ArrayBuffer);
      return;
    }
    let m: VoiceServerMessage;
    try { m = JSON.parse(ev.data); } catch { return; }
    const id = currentTurn.current;
    switch (m.type) {
      case "ready":
        sessionId.current = m.sessionId;
        setProviders({ llm: m.llm, stt: m.stt, tts: m.tts, sessionId: m.sessionId, handsFree: !!m.handsFree });
        setConn("ready");
        break;
      case "transcript": {
        const turn = id;
        if (!m.final) {
          // Live partial while the caller speaks (or just after the release): never over a final text.
          patchTurn(turn, (t) => (t.caller.status === "final" ? t : { ...t, caller: { ...t.caller, text: m.text, stable: m.stable ?? "" } }));
          break;
        }
        // Typed turns already show the caller text; the server echoes it as a transcript.
        if (turn == null) newTurn({ text: m.text, at: Date.now(), status: "final" });
        else patchTurn(turn, (t) => ({ ...t, caller: { ...t.caller, text: m.text, status: "final" } }));
        if (listenRec.current) releasedAt.current = performance.now(); // hands-free: from the end of turn the server decided
        setActivity((a) => (a === "recording" ? a : "thinking"));
        break;
      }
      case "caller_speaking":
        // hands-free: a new caller sentence (the server already stopped the agent): cut its audio, open the turn
        stopPlayback();
        newTurn({ text: "", at: Date.now(), status: "recording" });
        setActivity("recording");
        break;
      case "listen_stopped": {
        // the server's STT stream failed: drop our recorder; the call page starts a new one (fresh stream) once
        const rec = listenRec.current;
        listenRec.current = undefined;
        try { rec?.stop(); } catch { /* ignore */ }
        setListening(false);
        setLastError(m.reason);
        break;
      }
      case "agent_cut": {
        // the caller cut the agent: mark what he did not hear on the turn it belongs to (the previous agent reply)
        setTurns((ts) => {
          const i = [...ts].reverse().findIndex((t) => t.agent.text);
          if (i < 0) return ts;
          const k = ts.length - 1 - i;
          return ts.map((t, j) => (j === k ? { ...t, agent: { ...t.agent, unheard: m.unheard } } : t));
        });
        break;
      }
      case "agent_text":
        patchTurn(id, (t) => ({ ...t, agent: { ...t.agent, text: t.agent.text + m.delta, at: t.agent.at ?? Date.now() } }));
        break;
      case "agent_tool":
        patchTurn(id, (t) => {
          const tools = [...t.agent.tools];
          if (m.ok === undefined) tools.push({ id: `${t.id}-${tools.length}`, name: m.name, args: m.args });
          else {
            const i = tools.findIndex((x) => x.name === m.name && x.ok === undefined);
            if (i >= 0) tools[i] = { ...tools[i], ok: m.ok, ms: m.ms };
            else tools.push({ id: `${t.id}-${tools.length}`, name: m.name, ok: m.ok, ms: m.ms });
          }
          return { ...t, agent: { ...t.agent, tools, at: t.agent.at ?? Date.now() } };
        });
        break;
      case "agent_audio_start":
        discardAudio.current = false;
        audioMime.current = m.mime;
        turnHasAudio.current = true;
        if (m.mime !== MOCK_TTS_MIME) {
          stopPlayback();
          const p: SegmentPlayer = new SegmentPlayer(m.mime, {
            onPlaying: () => { setActivity("speaking"); setPlaying(true); markPerceived(id); },
            onEnded: () => {
              if (player.current === p) { player.current = undefined; setPlaying(false); }
              setActivity((a) => (a === "speaking" ? "idle" : a));
            },
          });
          player.current = p;
        }
        break;
      case "agent_audio_end":
        // An interrupted turn's segment closes after the new turn started: drop it.
        if (discardAudio.current) break;
        player.current?.end();
        break;
      case "agent_done":
        patchTurn(id, (t) => ({ ...t, timings: m.timings, agent: { ...t.agent, done: true, text: t.agent.text || m.text } }));
        if (audioMime.current === MOCK_TTS_MIME && turnHasAudio.current) speak(m.text, id);
        else if (!turnHasAudio.current || !player.current) setActivity((a) => (a === "thinking" ? "idle" : a));
        audioMime.current = undefined;
        currentTurn.current = undefined;
        break;
      case "call_started":
        setCall((c) => ({ ...(c ?? { callId: m.callId }), callId: m.callId, clientName: m.clientName, offers: m.offers, status: "live" }));
        newTurn({ text: `Appel sortant vers ${m.clientName}`, at: Date.now(), status: "final", cue: true });
        setActivity("thinking");
        break;
      case "call_ended":
        setCall((c) => (c && c.callId === m.callId ? { ...c, status: "ended", outcome: m.outcome ?? null } : c));
        break;
      case "error":
        setLastError(m.message);
        // The call could not start (ERP refused): end it here, there will be no call_started.
        setCall((c) => (c?.status === "dialing" ? { ...c, status: "ended", error: m.message } : c));
        if (id != null) patchTurn(id, (t) => ({ ...t, caller: { ...t.caller, status: "final" }, agent: { ...t.agent, done: true, error: m.message } }));
        currentTurn.current = undefined;
        setActivity((a) => (a === "thinking" ? "idle" : a));
        break;
    }
  }, [markPerceived, newTurn, patchTurn, setPlaying, speak, stopPlayback]);

  // ── Connection (auto-reconnect, same sessionId keeps the server-side history) ──
  const connect = useCallback(() => {
    if (!alive.current) return;
    ws.current?.close();
    setConn("connecting");
    const sock = new WebSocket(wsUrl());
    sock.binaryType = "arraybuffer";
    ws.current = sock;
    sock.onopen = () => sock.send(JSON.stringify({ type: "start", sessionId: sessionId.current, actorName: "Agent vocal" } satisfies VoiceClientMessage));
    sock.onmessage = onMessage;
    sock.onclose = () => {
      if (ws.current !== sock) return;
      setConn("offline");
      setActivity((a) => (a === "speaking" ? a : "idle"));
      if (alive.current) window.setTimeout(() => { if (ws.current === sock) connect(); }, 3000);
    };
  }, [onMessage]);

  // ── Phone call watcher (WS /ai/voice/phone): the phone call's messages, shown like a local call ──
  const onPhoneMessage = useCallback((ev: MessageEvent) => {
    if (typeof ev.data !== "string") return;
    let m: VoiceServerMessage;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "call_started") { // a fresh conversation on screen, flagged as a phone call
      stopPlayback();
      currentTurn.current = undefined;
      setTurns([]);
      setCall((c) => ({ callId: m.callId, clientName: "", offers: [], status: "dialing", phone: true, outcome: c?.outcome }));
    }
    onMessage(ev);
  }, [onMessage, stopPlayback]);

  useEffect(() => {
    let sock: WebSocket | undefined;
    let retry: number | undefined;
    let on = true;
    const open = () => {
      if (!on) return;
      sock = new WebSocket(wsUrl("/voice/phone"));
      sock.onmessage = onPhoneMessage;
      sock.onclose = () => { if (on) retry = window.setTimeout(open, 3000); };
    };
    fetch(`${AI_BASE}/phone`)
      .then((r) => (r.ok ? (r.json() as Promise<PhoneInfo>) : undefined))
      .then((info) => { if (!on) return; setPhone(info); if (info?.enabled) open(); })
      .catch(() => undefined);
    return () => { on = false; window.clearTimeout(retry); sock?.close(); };
  }, [onPhoneMessage]);

  useEffect(() => {
    alive.current = true;
    connect();
    return () => {
      alive.current = false;
      const s = ws.current;
      ws.current = undefined;
      s?.close();
      stopPlayback();
      if (raf.current) cancelAnimationFrame(raf.current);
      try { recorder.current?.stop(); } catch { /* ignore */ }
      try { listenRec.current?.stop(); } catch { /* ignore */ }
      stream.current?.getTracks().forEach((t) => t.stop());
      void audioCtx.current?.close().catch(() => undefined);
    };
  }, [connect, stopPlayback]);

  // ── Mic ──
  const ensureMic = useCallback(async (): Promise<MediaStream | undefined> => {
    if (stream.current?.active) return stream.current;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setMic("unsupported"); return undefined; }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      stream.current = s;
      setMic("granted");
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(s).connect(an);
      audioCtx.current = ctx;
      analyser.current = an;
      return s;
    } catch (e) {
      setMic((e as DOMException).name === "NotAllowedError" || (e as DOMException).name === "SecurityError" ? "denied" : "unsupported");
      return undefined;
    }
  }, []);

  const meter = useCallback(() => {
    const an = analyser.current;
    if (!an) return;
    const buf = new Uint8Array(an.fftSize);
    const tick = () => {
      an.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 4));
      raf.current = requestAnimationFrame(tick);
    };
    tick();
  }, []);

  const startTalking = useCallback(async () => {
    if (pressed.current || conn !== "ready" || listenRec.current) return; // hands-free: no push-to-talk
    pressed.current = true;
    stopPlayback();
    const s = await ensureMic();
    // Released while the permission prompt / mic warm-up was pending: do not record a stub.
    if (!s || !pressed.current) { pressed.current = false; return; }
    const mime = REC_MIME.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
    const rec = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
    recorder.current = rec;
    send({ type: "audio_start", mime: rec.mimeType || mime || "audio/webm" });
    rec.ondataavailable = (e) => { if (e.data.size && ws.current?.readyState === WebSocket.OPEN) ws.current.send(e.data); };
    rec.onstop = () => {
      // The last dataavailable fires before stop, so every chunk has been queued on the socket.
      send({ type: "audio_end" });
      releasedAt.current = performance.now();
    };
    void audioCtx.current?.resume();
    rec.start(100); // a chunk every 100 ms: the STT hears the words while they are spoken
    newTurn({ text: "", at: Date.now(), status: "recording" });
    setActivity("recording");
    setLastError(undefined);
    meter();
  }, [conn, ensureMic, meter, newTurn, send, stopPlayback]);

  const stopTalking = useCallback(() => {
    if (!pressed.current) return;
    pressed.current = false;
    if (raf.current) cancelAnimationFrame(raf.current);
    setLevel(0);
    const rec = recorder.current;
    recorder.current = undefined;
    if (rec && rec.state !== "inactive") {
      rec.stop();
      patchTurn(currentTurn.current, (t) => ({ ...t, caller: { ...t.caller, status: "transcribing" } }));
      setActivity("thinking");
    }
  }, [patchTurn]);

  const sendText = useCallback((text: string) => {
    const v = text.trim();
    if (!v || conn !== "ready") return;
    stopPlayback();
    newTurn({ text: v, at: Date.now(), status: "final", typed: true });
    send({ type: "text", text: v });
    releasedAt.current = performance.now();
    setActivity("thinking");
    setLastError(undefined);
  }, [conn, newTurn, send, stopPlayback]);

  /** Hands-free: stream the mic for the whole call; the server decides when the caller has finished. */
  const startListening = useCallback(async () => {
    if (listenRec.current || conn !== "ready" || !providers?.handsFree) return;
    const s = await ensureMic();
    if (!s) return;
    const mime = REC_MIME.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
    const rec = new MediaRecorder(s, mime ? { mimeType: mime } : undefined);
    listenRec.current = rec;
    send({ type: "listen_start", mime: rec.mimeType || mime || "audio/webm" });
    rec.ondataavailable = (e) => { if (e.data.size && ws.current?.readyState === WebSocket.OPEN) ws.current.send(e.data); };
    void audioCtx.current?.resume();
    rec.start(100);
    setListening(true);
    meter();
  }, [conn, ensureMic, meter, providers?.handsFree, send]);

  const stopListening = useCallback(() => {
    const rec = listenRec.current;
    if (!rec) return;
    listenRec.current = undefined;
    try { rec.stop(); } catch { /* ignore */ }
    send({ type: "listen_stop" });
    if (raf.current) cancelAnimationFrame(raf.current);
    setLevel(0);
    setListening(false);
  }, [send]);

  /** Start an outbound call: fresh history server-side, then call_started and the agent's opening turn. */
  const dial = useCallback((callId: string) => {
    if (conn !== "ready") return;
    stopPlayback();
    currentTurn.current = undefined;
    setTurns([]);
    setCall({ callId, clientName: "", offers: [], status: "dialing" });
    releasedAt.current = performance.now(); // perceived latency of the opening = click → first audio
    send({ type: "start", sessionId: sessionId.current, actorName: "Agent vocal", campaignCallId: callId });
    setActivity("thinking");
    setLastError(undefined);
  }, [conn, send, stopPlayback]);

  const hangUp = useCallback(() => {
    stopListening();
    stopPlayback();
    send({ type: "hangup" });
    setCall((c) => (c && c.status !== "ended" ? { ...c, status: "ending" } : c));
    setActivity("idle");
  }, [send, stopListening, stopPlayback]);

  /** Ring the user's phone (TWILIO_TO_NUMBER) for this campaign call; the agent speaks when they pick up. */
  const dialPhone = useCallback(async (callId: string) => {
    stopPlayback();
    currentTurn.current = undefined;
    setTurns([]);
    setLastError(undefined);
    setCall({ callId, clientName: "", offers: [], status: "dialing", phone: true });
    const error = await postPhone("call", { campaignCallId: callId });
    if (error) {
      toast.error("Appel téléphonique impossible", error);
      setCall((c) => (c?.callId === callId && c.status === "dialing" ? { ...c, status: "ended", error } : c));
    }
  }, [stopPlayback]);

  const hangUpPhone = useCallback(async (callId: string) => {
    setCall((c) => (c && c.callId === callId && c.status !== "ended" ? { ...c, status: "ending" } : c));
    const error = await postPhone("hangup", { campaignCallId: callId });
    if (error) toast.error("Raccrochage impossible", error);
  }, []);

  const reset = useCallback(() => {
    stopPlayback();
    send({ type: "reset" });
    currentTurn.current = undefined;
    setTurns([]);
    setActivity("idle");
    setLastError(undefined);
  }, [send, stopPlayback]);

  return {
    conn, activity, mic, providers, turns, level, lastError, call, listening, phone,
    startTalking, stopTalking, sendText, stopPlayback, reset, reconnect: connect, dial, hangUp, startListening, stopListening,
    dialPhone, hangUpPhone,
  };
}
