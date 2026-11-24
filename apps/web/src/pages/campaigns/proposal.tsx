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
      subtitle="Par lot : stock libre, ventes prévues au tarif habituel, part à brader, urgence avant la DLC"
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
            <Stat label="Récupérable" value={fmtMoney(recovered)} hint="si les offres sont acceptées, HT" tone="success" />
            <Stat label="Offres" value={offers.length} />
            <Stat label="Clients à appeler" value={data.campaign.targets.length} hint="jusqu'à 4 offres par appel" />
          </StatGrid>
          <Section title="Offres" count={data.analysis.length} className="px-4">
            <DataTable inset paginate={false} columns={COLUMNS} rows={data.analysis} getRowId={(a) => a.offerId} />
          </Section>
          <p className="px-4 text-[12px] text-muted-foreground">
            Remise par client : d* = 40 % · λ · (1 + ε<sub>c</sub>) · R · U(J), avec R la part du lot que les ventes normales
            n'absorberont pas, U(J) = 0,5 + 0,5 / (1 + e<sup>3(J−2)</sup>) l'urgence (J = jours jusqu'à la dernière tournée possible
            pour ce client avant la DLC), puis plafonnée (campagne, prix plancher) ; prix arrondi au centime. Clients classés par
            contribution G = f·[q(p − c − A) − k·max(0, p₀ − c − A)] − F : seules les offres où G &gt; 0 sont gardées. λ et ε
            n'évoluent que par paires d'offres comparées (λ ± 0,05) : tant qu'il n'y a pas assez de résultats, λ = 0,60 et ε = 0.
            La campagne active précédente sera clôturée.
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
  { id: "sellBy", header: "DLC", cell: (a) => <DateLabel date={a.sellBy} /> },
  {
    id: "surplus",
    header: "À brader",
    numeric: true,
    cell: (a) => (
      <span className="flex flex-col items-end" title={`Stock libre ${fmtQty(a.freeStock ?? 0, a.unit)}, ventes prévues ${fmtQty(a.expectedSales ?? 0, a.unit)}`}>
        <span>{a.surplusShare != null ? `R ${Math.round(a.surplusShare * 100)} %` : "—"}</span>
        <span className="text-[11.5px] text-muted-foreground">
          {a.daysLeft != null ? `J ${a.daysLeft} j · U ${Math.round((a.urgency ?? 0) * 100)} %` : ""}
        </span>
      </span>
    ),
    hideBelow: 640,
  },
  { id: "discount", header: "Remise", numeric: true, cell: (a) => (a.discountPct != null ? `−${a.discountPct} % (max ${a.maxDiscountPct} %)` : "—") },  // median of the clients'

  { id: "floor", header: "Plancher", numeric: true, cell: (a) => <Money value={a.floorPrice} />, hideBelow: 640 },
  { id: "targets", header: "Clients", numeric: true, cell: (a) => a.targets },
  {
    id: "recovered",
    header: "Récupéré",
    numeric: true,
    cell: (a) => (a.note ? "—" : (
      <span className="flex flex-col items-end" title="Chiffre d'affaires si les offres sont acceptées ; G = contribution de scénario">
        <Money value={a.expectedRecovered} />
        <span className="text-[11.5px] text-muted-foreground">
          {fmtQty(a.expectedCleared, a.unit)}{a.contribution != null ? ` · G ${fmtMoney(a.contribution)}` : ""}
        </span>
      </span>
    )),
  },
];
