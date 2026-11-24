/**
 * « Rappel prévu » banner, on every page: a client asked to be called back (« rappelez-moi à 15 h ») and the time has
 * come. With the phone (Twilio) configured, the AI service rings it by itself (services/callbacks.py) — the banner
 * just says so. Without it, one click starts the call in the browser (audio can't start without a click).
 * Polls GET /api/campaign-calls/callbacks every 20 s.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PhoneCall, X } from "lucide-react";
import type { PhoneInfo } from "@kit/shared";
import { AI_BASE } from "@/lib/api";
import { useCallbacks } from "@/lib/queries";
import { Button, Dot, IconButton } from "@/ui";

const AUTO_WINDOW_MIN = 15; // same as VOICE_CALLBACK_WINDOW_MIN: older callbacks are never rung by themselves

const minutesLate = (at: string, now: string) => (Date.parse(`${now.slice(0, 16)}:00`) - Date.parse(`${at.slice(0, 16)}:00`)) / 60000;

export function CallbackBanner() {
  const nav = useNavigate();
  const { data } = useCallbacks();
  const [phone, setPhone] = useState<PhoneInfo>();
  const [dismissed, setDismissed] = useState<string[]>([]);

  useEffect(() => {
    fetch(`${AI_BASE}/phone`).then((r) => (r.ok ? r.json() : undefined)).then(setPhone).catch(() => undefined);
  }, []);

  const call = data?.due.find((c) => !dismissed.includes(c.id));
  if (!call || !data) return null;
  const late = minutesLate(call.callbackAt ?? data.now, data.now);
  const ringing = !!phone?.enabled && late <= AUTO_WINDOW_MIN;
  const at = (call.callbackAt ?? "").slice(11, 16).replace(":", " h ");

  return (
    <div className="absolute bottom-5 right-5 z-40 flex max-w-[420px] items-center gap-3 rounded-[6px] border border-violet/30 bg-background px-4 py-3 shadow-lg">
      <Dot tone="violet" pulse />
      <div className="flex min-w-0 flex-col text-[13px]">
        <span className="truncate font-medium">Rappel prévu à {at} : {call.clientName}</span>
        <span className="truncate text-[12px] text-muted-foreground">
          {ringing ? "Le téléphone va sonner : l'agent reprend l'appel." : late > AUTO_WINDOW_MIN ? `En retard de ${Math.round(late)} min` : call.summary ?? "Le client a demandé à être rappelé"}
        </span>
      </div>
      {ringing ? null : (
        <Button size="sm" variant="primary" icon={PhoneCall} onClick={() => nav(`/voice?call=${call.id}`)}>
          Appeler
        </Button>
      )}
      <IconButton icon={X} label="Masquer" size="sm" onClick={() => setDismissed((d) => [...d, call.id])} />
    </div>
  );
}
