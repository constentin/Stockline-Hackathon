/**
 * Outbound clearance call bar (Voix page, ?call=ccl_…): Appeler → the agent speaks first → Raccrocher → the next call
 * of the campaign. ?auto=1 chains the calls by itself (3 s after each hang-up). ?hf=1 = hands-free: the mic stays
 * open during the call and the server detects when the client has finished (no Space key; Soniox STT only).
 * "Voix : ERP | Téléphone" picks where the call is heard, remembered in this browser: ERP = in this page (mic + speakers),
 * Téléphone = the agent rings the team's phone through Twilio (GUIDE-TELEPHONE.md); the page then only shows the
 * conversation live (the audio is on the phone). One "Appeler" button uses the chosen channel, the next calls too.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Mic, PhoneCall, PhoneOff, PhoneOutgoing, SkipForward, Smartphone } from "lucide-react";
import type { NextCall } from "@kit/shared";
import { api } from "@/lib/api";
import { useCampaignCall } from "@/lib/queries";
import { useUrlPatch, useUrlState } from "@/lib/url-state";
import { Button, CAMPAIGN_CALL_STATUS, Dot, Segmented, Spinner, StatusDot } from "@/ui";
import type { useVoiceSession } from "@/features/assistant/useVoiceSession";

type Voice = ReturnType<typeof useVoiceSession>;
const AUTO_DELAY_S = 3;

type Channel = "erp" | "phone";
const CHANNEL_KEY = "kit-call-channel";

/** Where the clearance calls are heard, remembered in this browser (localStorage). */
function useChannel(): [Channel, (c: Channel) => void] {
  const [channel, setState] = useState<Channel>(() => {
    try { return localStorage.getItem(CHANNEL_KEY) === "phone" ? "phone" : "erp"; } catch { return "erp"; }
  });
  const setChannel = useCallback((c: Channel) => {
    setState(c);
    try { localStorage.setItem(CHANNEL_KEY, c); } catch { /* private mode: not remembered */ }
  }, []);
  return [channel, setChannel];
}

export function OutboundBar({ v }: { v: Voice }) {
  const [callId] = useUrlState("call", "", { keepPage: true });
  const [auto, setAuto] = useUrlState("auto", "", { keepPage: true });
  const [hf, setHf] = useUrlState("hf", "", { keepPage: true });
  const patch = useUrlPatch();
  const { data: call } = useCampaignCall(callId || undefined);
  const [next, setNext] = useState<NextCall>();
  const [countdown, setCountdown] = useState<number>();

  const session = v.call && v.call.callId === callId ? v.call : undefined;
  const ended = session?.status === "ended";
  const inCall = !!session && !ended;
  const ready = v.conn === "ready";
  const closed = call?.status === "skipped" || call?.status === "do_not_call";

  const [channel, setChannel] = useChannel();
  const onPhone = !!session?.phone; // the call shown is on the phone (whatever the channel chosen since)
  const phoneReady = !!v.phone?.enabled;
  const viaPhone = channel === "phone";
  const canDial = viaPhone ? phoneReady : ready;
  const dialTitle = viaPhone ? (phoneReady ? `L'agent appelle ${v.phone?.to}` : v.phone?.reason ?? "Téléphone non configuré") : undefined;
  const startCall = (id: string) => (viaPhone ? void v.dialPhone(id) : v.dial(id));

  // Hands-free: the mic opens when the client picks up and closes at the hang-up (not for a phone call: the mic is the phone).
  const handsFree = hf === "1" && !!v.providers?.handsFree;
  const live = session?.status === "live" && !onPhone;
  const { startListening, stopListening, listening } = v;
  const [restarts, setRestarts] = useState(0);
  useEffect(() => {
    if (live && handsFree) void startListening();
    else stopListening();
  }, [live, handsFree, startListening, stopListening]);
  // The server stopped listening (STT stream failed): one new recording = one new stream; at most twice per call.
  useEffect(() => {
    if (!live) { setRestarts(0); return; }
    if (handsFree && !listening && restarts < 2) {
      const t = window.setTimeout(() => { setRestarts((n) => n + 1); void startListening(); }, 500);
      return () => window.clearTimeout(t);
    }
  }, [live, handsFree, listening]); // eslint-disable-line react-hooks/exhaustive-deps

  // After a hang-up: ask the campaign for the next call.
  useEffect(() => {
    if (!ended || !call) return;
    let live = true;
    void api.campaigns.nextCall(call.campaignId).then((n) => { if (live) setNext(n); });
    return () => { live = false; };
  }, [ended, call]);

  const goTo = (id: string) => {
    setNext(undefined);
    setCountdown(undefined);
    patch({ call: id }, { keepPage: true });
    startCall(id);
  };

  // Auto mode: dial the next call after a short pause (cancellable).
  useEffect(() => {
    if (!ended || auto !== "1" || !next?.call) return;
    setCountdown(AUTO_DELAY_S);
    const tick = window.setInterval(() => setCountdown((s) => (s ?? 0) - 1), 1000);
    return () => { window.clearInterval(tick); setCountdown(undefined); };
  }, [ended, auto, next]);
  useEffect(() => {
    if (countdown === 0 && next?.call) goTo(next.call.id);
  }, [countdown]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!callId) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-violet/[.04] px-5 py-2.5 text-[13px]">
      <span className="flex min-w-0 items-center gap-2">
        {inCall ? <Dot tone="violet" pulse /> : <PhoneOutgoing size={14} strokeWidth={1.75} className="shrink-0 text-violet" />}
        <span className="truncate font-medium">{session?.clientName || call?.clientName || "…"}</span>
        {call ? <StatusDot kind="campaignCall" value={call.status} /> : null}
        {call?.attempts ? <span className="text-[12px] text-muted-foreground">· {call.attempts} essai{call.attempts > 1 ? "s" : ""}</span> : null}
      </span>

      {onPhone && session?.status === "dialing" ? (
        <span className="min-w-0 flex-1 truncate text-[12px] text-violet">Ça sonne sur {v.phone?.to ?? "votre téléphone"}… décrochez !</span>
      ) : inCall && session?.offers.length ? (
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground" title={session.offers.join("\n")}>
          {session.offers.join(" · ")}
        </span>
      ) : ended ? (
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground">
          {session?.error
            ? `Appel impossible : ${session.error}`
            : session?.outcome
              ? `Raccroché — résultat enregistré : ${CAMPAIGN_CALL_STATUS[session.outcome as keyof typeof CAMPAIGN_CALL_STATUS]?.label ?? session.outcome}`
              : "Raccroché — résultat enregistré par l'agent"}
          {call?.summary ? ` · ${call.summary}` : ""}
          {call?.orderNumber ? ` · commande ${call.orderNumber}` : ""}
        </span>
      ) : viaPhone && !closed ? (
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground" title={dialTitle}>
          {phoneReady ? `Au téléphone : l'agent appellera ${v.phone?.to}` : `Téléphone indisponible : ${v.phone?.reason ?? "service IA injoignable"}`}
        </span>
      ) : (
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground">
          {closed ? call?.skipReason ?? "Appel fermé" : call?.reason ?? ""}
        </span>
      )}

      <span className="ml-auto flex items-center gap-2">
        {onPhone && inCall ? (
          <span className="flex items-center gap-1.5 text-[12px] text-violet">
            <Smartphone size={13} strokeWidth={1.75} /> Au téléphone
          </span>
        ) : null}
        {v.listening ? (
          <span className="flex items-center gap-1.5 text-[12px] text-violet">
            <Mic size={13} strokeWidth={1.75} /> Micro ouvert
          </span>
        ) : null}
        <span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          Voix
          <Segmented
            value={channel}
            onChange={setChannel}
            options={[{ id: "erp", label: "ERP" }, { id: "phone", label: "Téléphone" }]}
          />
        </span>
        {v.providers?.handsFree && !viaPhone ? (
          <Segmented
            value={hf === "1" ? "hf" : "ptt"}
            onChange={(m) => setHf(m === "hf" ? "1" : "")}
            options={[{ id: "ptt", label: "Espace" }, { id: "hf", label: "Mains libres" }]}
          />
        ) : null}
        <Segmented
          value={auto === "1" ? "auto" : "manual"}
          onChange={(m) => setAuto(m === "auto" ? "1" : "")}
          options={[{ id: "manual", label: "Un par un" }, { id: "auto", label: "Enchaîner" }]}
        />
        {inCall ? (
          <Button
            size="sm"
            variant="danger"
            icon={PhoneOff}
            onClick={() => (onPhone ? void v.hangUpPhone(session!.callId) : v.hangUp())}
            disabled={session?.status === "ending"}
          >
            {session?.status === "ending" ? "Raccrochage…" : "Raccrocher"}
          </Button>
        ) : ended ? (
          next?.call ? (
            <Button size="sm" variant="primary" icon={SkipForward} onClick={() => goTo(next.call!.id)} disabled={!canDial} title={dialTitle}>
              {countdown != null && countdown > 0 ? `Appel suivant dans ${countdown} s` : `Appeler ${next.call.clientName}`}
            </Button>
          ) : next ? (
            <span className="text-[12px] text-muted-foreground">Plus d'appel à passer</span>
          ) : (
            <Spinner size={12} />
          )
        ) : (
          <Button
            size="sm"
            variant="primary"
            icon={viaPhone ? Smartphone : PhoneCall}
            onClick={() => startCall(callId)}
            disabled={!canDial || closed}
            title={dialTitle}
          >
            Appeler
          </Button>
        )}
        {call ? (
          <Link to={`/campaigns?c=${call.campaignId}`} className="text-[12px] text-muted-foreground hover:text-foreground">
            Campagne
          </Link>
        ) : null}
      </span>
    </div>
  );
}
