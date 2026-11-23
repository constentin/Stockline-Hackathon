/**
 * "Calculer depuis le stock": runs the clearance model (GET /api/campaigns/proposal), shows why each offer is there,
 * then creates the campaign through the normal import (closing the previous active one) and, if asked, opens the
 * Voix page on the first call with the calls chained.
 */
import { useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Calculator, PhoneOutgoing, RefreshCw } from "lucide-react";
import type { ProposalAnalysis } from "@kit/shared";
import { api } from "@/lib/api";
import { fmtMoney, fmtQty } from "@/lib/format";
import { qk, useApiMutation } from "@/lib/queries";
import { Button, DataTable, DateLabel, EmptyState, Money, Qty, Section, SidePanel, Skeleton, Stat, StatGrid, type Column } from "@/ui";

export function ProposalPanel({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const nav = useNavigate();
  const { data, isFetching, refetch } = useQuery({ queryKey: [...qk.campaigns.all, "proposal"], queryFn: api.campaigns.proposal, staleTime: 0, gcTime: 0 });
  const launch = useRef(false); // "Créer et lancer les appels": open the Voix page once created

  const create = useApiMutation(api.campaigns.import, {
    success: (d) => `Campagne créée : ${d.calls.filter((c) => c.status === "pending").length} client(s) à appeler`,
    invalidate: [qk.campaigns.all],
    onSuccess: async (d) => {
      if (launch.current) {
        const next = await api.campaigns.nextCall(d.id);
        if (next.call) { nav(`/voice?call=${next.call.id}&auto=1`); return; }
      }
      onCreated(d.id);
    },
  });

  const offers = data?.campaign.offers ?? [];
  const kept = data?.analysis.filter((a) => !a.note) ?? [];
  const recovered = kept.reduce((s, a) => s + a.expectedRecovered, 0);
  const atRisk = kept.reduce((s, a) => s + a.lossValue, 0);
  const go = (andCall: boolean) => {
    if (!data) return;
    launch.current = andCall;
    create.mutate(data.campaign);
  };

  return (
    <SidePanel
      id="campaign-proposal"
      width={720}
      title="Proposition de déstockage"
      subtitle="Calculée sur le stock, la demande des 8 dernières semaines et le panier des clients"
      onClose={onClose}
      actions={<Button size="sm" variant="ghost" icon={RefreshCw} loading={isFetching} onClick={() => void refetch()}>Recalculer</Button>}
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button onClick={onClose}>Annuler</Button>
          <Button icon={Calculator} disabled={!offers.length} loading={create.isPending && !launch.current} onClick={() => go(false)}>Créer la campagne</Button>
          <Button variant="primary" icon={PhoneOutgoing} disabled={!offers.length} loading={create.isPending && launch.current} onClick={() => go(true)}>
            Créer et lancer les appels
          </Button>
        </div>
      }
    >
      {!data ? (
        <div className="flex flex-col gap-3 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full" />)}</div>
      ) : !offers.length ? (
        <EmptyState icon={Calculator} title="Rien à déstocker" description="Avec la demande habituelle, le stock actuel se vend avant ses dates limites." />
      ) : (
        <div className="flex flex-col gap-5 pb-4">
          <StatGrid className="md:grid-cols-4">
            <Stat label="Perte évitable" value={fmtMoney(atRisk)} hint="au coût, sans action" tone="destructive" />
            <Stat label="Récupérable" value={fmtMoney(recovered)} hint="espéré, HT" tone="success" />
            <Stat label="Offres" value={offers.length} />
            <Stat label="Clients à appeler" value={data.campaign.targets.length} hint="3 offres max par appel" />
          </StatGrid>
          <Section title="Offres" count={data.analysis.length} className="px-4">
            <DataTable inset paginate={false} columns={COLUMNS} rows={data.analysis} getRowId={(a) => a.offerId} />
          </Section>
          <p className="px-4 text-[12px] text-muted-foreground">
            Remise = celle qui maximise l'argent récupéré, en tenant compte des clients qui auraient acheté au prix normal ; la remise max
            laisse une marge de négociation à l'agent, jamais sous le prix plancher (en dessous, le don rapporte plus). La campagne active
            précédente sera clôturée.
          </p>
        </div>
      )}
    </SidePanel>
  );
}

const COLUMNS: Column<ProposalAnalysis>[] = [
  {
    id: "product",
    header: "Produit",
    cell: (a) => (
      <span className="flex min-w-0 flex-col">
        <span className="truncate">{a.productName}</span>
        {a.note ? <span className="truncate text-[11.5px] text-muted-foreground">{a.note}</span> : null}
      </span>
    ),
  },
  {
    id: "loss",
    header: "Perte prévue",
    numeric: true,
    cell: (a) => (
      <span className="flex flex-col items-end">
        <Qty value={a.expectedLoss} unit={a.unit} />
        <span className="text-[11.5px] text-muted-foreground">{fmtMoney(a.lossValue)}</span>
      </span>
    ),
  },
  { id: "sellBy", header: "À livrer avant", cell: (a) => <DateLabel date={a.sellBy} /> },
  { id: "discount", header: "Remise", numeric: true, cell: (a) => (a.discountPct != null ? `−${a.discountPct} % (max ${a.maxDiscountPct} %)` : "—") },
  { id: "floor", header: "Plancher", numeric: true, cell: (a) => <Money value={a.floorPrice} />, hideBelow: 640 },
  { id: "targets", header: "Clients", numeric: true, cell: (a) => a.targets },
  {
    id: "recovered",
    header: "Récupéré",
    numeric: true,
    cell: (a) => (a.note ? "—" : (
      <span className="flex flex-col items-end">
        <Money value={a.expectedRecovered} />
        <span className="text-[11.5px] text-muted-foreground">{fmtQty(a.expectedCleared, a.unit)}</span>
      </span>
    )),
  },
];
