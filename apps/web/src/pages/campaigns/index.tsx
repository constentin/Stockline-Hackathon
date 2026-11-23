/**
 * Déstockage — clearance campaigns: short-dated lots offered at a discount, one outbound call per client.
 * The campaign comes from the clearance model ("Calculer depuis le stock", ./proposal.tsx) or a JSON (Importer); "Lancer les appels" opens the Voix page on the
 * next call with ?auto=1 (calls chained). Everything refreshes live as the voice agent sells (SSE).
 */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Archive, Calculator, PhoneCall, PhoneOutgoing, Upload } from "lucide-react";
import { ProposalPanel } from "./proposal";
import type { CampaignCallView, CampaignImportInput, CampaignOfferView } from "@kit/shared";
import { api } from "@/lib/api";
import { fmtMoney, fmtQty } from "@/lib/format";
import { qk, useApiMutation, useCampaign, useCampaigns } from "@/lib/queries";
import { useUrlState } from "@/lib/url-state";
import {
  Button, DataTable, DateLabel, EmptyState, Field, Mono, Money, Page, PageBody, PageHeader, Qty, Section, Select, SidePanel, Stat,
  StatGrid, StatusDot, Textarea, type Column,
} from "@/ui";

const CALLABLE = new Set(["pending", "callback", "no_answer", "in_progress"]);

const TEMPLATE = `{
  "name": "Déstockage du jour",
  "offers": [
    { "offerId": "off_1", "productId": "prd_0013", "lotId": "lot_023163", "qty": 120, "discountPct": 20, "maxDiscountPct": 30 }
  ],
  "targets": [
    { "clientId": "cli_0065", "offerIds": ["off_1"], "score": 0.9, "reason": "achète des moules chaque semaine" }
  ]
}`;

export default function CampaignsPage() {
  const nav = useNavigate();
  const { data: campaigns, isLoading } = useCampaigns();
  const [cParam, setC] = useUrlState("c");
  const [importing, setImporting] = useUrlState("import", "", { keepPage: true });
  const [proposing, setProposing] = useUrlState("propose", "", { keepPage: true });
  const current = cParam || campaigns?.find((c) => c.status === "active")?.id || campaigns?.[0]?.id;
  const { data: campaign } = useCampaign(current);

  const close = useApiMutation((id: string) => api.campaigns.update(id, { status: "closed" }), {
    success: "Campagne clôturée",
    invalidate: [qk.campaigns.all],
  });

  const launch = async () => {
    if (!campaign) return;
    const next = await api.campaigns.nextCall(campaign.id);
    if (next.call) nav(`/voice?call=${next.call.id}&auto=1`);
  };

  const stats = campaign?.stats;
  const pending = campaign?.calls.filter((c) => c.status === "pending").length ?? 0;
  const active = campaign?.status === "active";

  return (
    <Page>
      <PageHeader
        title="Déstockage"
        meta={campaign ? <span className="text-[12px] text-muted-foreground">{active ? "Campagne active" : "Clôturée"}</span> : null}
        filters={campaigns?.length ? (
          <Select
            size="sm"
            value={current ?? ""}
            onChange={setC}
            options={campaigns.map((c) => ({
              value: c.id,
              label: c.name,
              hint: `${c.stats.done}/${c.stats.calls} appels${c.status === "closed" ? " · clôturée" : ""}`,
            }))}
          />
        ) : null}
        actions={
          <>
            {campaign && active ? (
              <Button size="sm" variant="ghost" icon={Archive} onClick={() => close.mutate(campaign.id)} loading={close.isPending}>Clôturer</Button>
            ) : null}
            <Button size="sm" variant="ghost" icon={Upload} onClick={() => setImporting("1")}>Importer</Button>
            <Button icon={Calculator} onClick={() => setProposing("1")}>Calculer depuis le stock</Button>
            <Button variant="primary" icon={PhoneOutgoing} onClick={() => void launch()} disabled={!active || !pending}>
              Lancer les appels
            </Button>
          </>
        }
      />
      <PageBody>
        {!isLoading && !campaigns?.length ? (
          <EmptyState
            icon={PhoneOutgoing}
            title="Aucune campagne de déstockage"
            description="Calculez-la depuis le stock (produits qui vont être perdus, clients qui peuvent les prendre, remises), ou importez la sortie de votre modèle. L'agent vocal appelle ensuite les clients un par un."
            action={<Button size="sm" variant="primary" icon={Calculator} onClick={() => setProposing("1")}>Calculer depuis le stock</Button>}
          />
        ) : campaign ? (
          <div className="flex flex-col gap-6">
            <StatGrid className="-mx-5 -mt-5">
              <Stat label="Appels passés" value={`${stats?.done ?? 0} / ${stats?.calls ?? 0}`} hint={`${pending} à appeler`} />
              <Stat label="Acceptés" value={stats?.accepted ?? 0} tone="success" hint={`${stats?.declined ?? 0} refus`} />
              <Stat label="Quantité écoulée" value={fmtQty(stats?.qtySold ?? 0)} tone="violet" />
              <Stat label="CA récupéré" value={fmtMoney(stats?.revenueHt ?? 0)} hint={`remise accordée ${fmtMoney(stats?.discountGivenHt ?? 0)} HT`} />
            </StatGrid>
            <Section title="Offres" count={campaign.offers.length}>
              <DataTable inset paginate={false} columns={OFFER_COLUMNS} rows={campaign.offers} getRowId={(o) => o.id} onRowClick={(o) => nav(`/products/${o.productId}`)} />
            </Section>
            <Section title="Appels" count={campaign.calls.length}>
              <DataTable
                inset
                paginate={false}
                columns={callColumns(active, (id) => nav(`/voice?call=${id}`))}
                rows={campaign.calls}
                getRowId={(c) => c.id}
                onRowClick={(c) => nav(`/clients/${c.clientId}`)}
              />
            </Section>
          </div>
        ) : null}
      </PageBody>
      {proposing ? <ProposalPanel onClose={() => setProposing("")} onCreated={(id) => { setProposing(""); setC(id); }} /> : null}
      {importing ? <ImportPanel onClose={() => setImporting("")} onDone={(id) => { setImporting(""); setC(id); }} /> : null}
    </Page>
  );
}

const OFFER_COLUMNS: Column<CampaignOfferView>[] = [
  { id: "product", header: "Produit", cell: (o) => o.productName, sortValue: (o) => o.productName },
  { id: "lot", header: "Lot", cell: (o) => (o.lotId ? <Mono>{o.lotId}</Mono> : "—"), hideBelow: 900 },
  { id: "expires", header: "Se garde jusqu'au", cell: (o) => <DateLabel date={o.expiresAt} />, sortValue: (o) => o.expiresAt },
  { id: "qty", header: "À écouler", numeric: true, cell: (o) => <Qty value={o.qty} unit={o.unit} /> },
  { id: "sold", header: "Vendu", numeric: true, cell: (o) => <Qty value={o.sold} unit={o.unit} /> },
  { id: "remaining", header: "Reste", numeric: true, cell: (o) => <Qty value={o.remaining} unit={o.unit} tone={o.remaining <= 0 ? "destructive" : undefined} /> },
  { id: "price", header: "Prix catalogue", numeric: true, cell: (o) => <Money value={o.listPrice} />, hideBelow: 1100 },
  { id: "discount", header: "Remise", numeric: true, cell: (o) => `−${o.discountPct} % (max ${o.maxDiscountPct} %)` },
];

function callColumns(active: boolean, call: (id: string) => void): Column<CampaignCallView>[] {
  return [
    { id: "rank", header: "#", width: 40, numeric: true, cell: (c) => c.rank, sortValue: (c) => c.rank },
    {
      id: "client",
      header: "Client",
      cell: (c) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate">{c.clientName}</span>
          {c.reason ? <span className="truncate text-[11.5px] text-muted-foreground">{c.reason}</span> : null}
        </span>
      ),
      sortValue: (c) => c.clientName,
    },
    { id: "score", header: "Score", numeric: true, cell: (c) => c.score.toFixed(2), sortValue: (c) => c.score, hideBelow: 1000 },
    {
      id: "status",
      header: "Statut",
      cell: (c) => (
        <span className="flex min-w-0 flex-col">
          <StatusDot kind="campaignCall" value={c.status} />
          {c.skipReason ? <span className="truncate text-[11.5px] text-muted-foreground">{c.skipReason}</span> : null}
        </span>
      ),
      sortValue: (c) => c.status,
    },
    { id: "delivery", header: "Livraison", cell: (c) => <DateLabel date={c.deliveryDate} />, hideBelow: 1100 },
    {
      id: "order",
      header: "Commande",
      cell: (c) => (c.orderId ? (
        <Link to={`/orders/${c.orderId}`} onClick={(e) => e.stopPropagation()} className="hover:underline"><Mono>{c.orderNumber}</Mono></Link>
      ) : "—"),
    },
    { id: "summary", header: "Résumé", muted: true, cell: (c) => <span className="line-clamp-2">{c.summary ?? ""}</span>, hideBelow: 1200 },
    {
      id: "action",
      header: "",
      width: 110,
      cell: (c) => (active && CALLABLE.has(c.status) && c.openOffers > 0 ? (
        <Button size="sm" icon={PhoneCall} onClick={(e) => { e.stopPropagation(); call(c.id); }}>Appeler</Button>
      ) : null),
    },
  ];
}

function ImportPanel({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [text, setText] = useState(TEMPLATE);
  const parsed = useMemo(() => {
    try {
      return { body: JSON.parse(text) as CampaignImportInput };
    } catch (e) {
      return { error: (e as Error).message };
    }
  }, [text]);
  const imp = useApiMutation(api.campaigns.import, {
    success: (d) => `Campagne importée : ${d.calls.length} client${d.calls.length > 1 ? "s" : ""}`,
    invalidate: [qk.campaigns.all],
    onSuccess: (d) => onDone(d.id),
  });
  return (
    <SidePanel
      title="Importer une campagne"
      subtitle="Sortie du modèle de déstockage (JSON)"
      onClose={onClose}
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button onClick={onClose}>Annuler</Button>
          <Button variant="primary" icon={Upload} disabled={!parsed.body} loading={imp.isPending} onClick={() => parsed.body && imp.mutate(parsed.body)}>
            Importer
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-3 p-4">
        <p className="text-[12px] text-muted-foreground">
          offers : les lots à écouler (remise d'ouverture et remise maximale, appliquée par l'ERP). targets : les clients à appeler,
          avec les offres à leur proposer et un score (les meilleurs d'abord). Les clients bloqués, ceux qui ne veulent plus être
          appelés et ceux livrés après la date de péremption sont écartés automatiquement.
        </p>
        <Field label="JSON" hint={parsed.error ? `JSON invalide : ${parsed.error}` : undefined}>
          <Textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} className="min-h-[360px] font-mono text-[12px]" />
        </Field>
      </div>
    </SidePanel>
  );
}
