/**
 * Outbound clearance call bar (Voix page, ?call=ccl_…): Appeler → the agent speaks first → Raccrocher → the next call
 * of the campaign. ?auto=1 chains the calls by itself (3 s after each hang-up).
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PhoneCall, PhoneOff, PhoneOutgoing, SkipForward } from "lucide-react";
import type { NextCall } from "@kit/shared";
import { api } from "@/lib/api";
import { useCampaignCall } from "@/lib/queries";
import { useUrlPatch, useUrlState } from "@/lib/url-state";
import { Button, CAMPAIGN_CALL_STATUS, Dot, Segmented, Spinner, StatusDot } from "@/ui";
import type { useVoiceSession } from "@/features/assistant/useVoiceSession";

type Voice = ReturnType<typeof useVoiceSession>;
const AUTO_DELAY_S = 3;

export function OutboundBar({ v }: { v: Voice }) {
  const [callId] = useUrlState("call", "", { keepPage: true });
  const [auto, setAuto] = useUrlState("auto", "", { keepPage: true });
  const patch = useUrlPatch();
  const { data: call } = useCampaignCall(callId || undefined);
  const [next, setNext] = useState<NextCall>();
  const [countdown, setCountdown] = useState<number>();

  const session = v.call && v.call.callId === callId ? v.call : undefined;
  const ended = session?.status === "ended";
  const inCall = !!session && !ended;
  const ready = v.conn === "ready";
  const closed = call?.status === "skipped" || call?.status === "do_not_call";

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
    v.dial(id);
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

      {inCall && session?.offers.length ? (
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
      ) : (
        <span className="min-w-0 flex-1 truncate text-[12px] text-muted-foreground">
          {closed ? call?.skipReason ?? "Appel fermé" : call?.reason ?? ""}
        </span>
      )}

      <span className="ml-auto flex items-center gap-2">
        <Segmented
          value={auto === "1" ? "auto" : "manual"}
          onChange={(m) => setAuto(m === "auto" ? "1" : "")}
          options={[{ id: "manual", label: "Un par un" }, { id: "auto", label: "Enchaîner" }]}
        />
        {inCall ? (
          <Button size="sm" variant="danger" icon={PhoneOff} onClick={v.hangUp} disabled={session?.status === "ending"}>
            {session?.status === "ending" ? "Raccrochage…" : "Raccrocher"}
          </Button>
        ) : ended ? (
          next?.call ? (
            <Button size="sm" variant="primary" icon={SkipForward} onClick={() => goTo(next.call!.id)} disabled={!ready}>
              {countdown != null && countdown > 0 ? `Appel suivant dans ${countdown} s` : `Appeler ${next.call.clientName}`}
            </Button>
          ) : next ? (
            <span className="text-[12px] text-muted-foreground">Plus d'appel à passer</span>
          ) : (
            <Spinner size={12} />
          )
        ) : (
          <Button size="sm" variant="primary" icon={PhoneCall} onClick={() => v.dial(callId)} disabled={!ready || closed}>
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
