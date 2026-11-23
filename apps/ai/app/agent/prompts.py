"""
System prompt = role + rules + mode rules + company & today's date + company context files.

  - Company context: every Markdown file in DATA_DIR/context/, re-read on each turn (edit them live).
  - "Today" comes from the ERP (GET /api/meta): the data pack has its own simulated date.
"""

import re
import time
from datetime import UTC, date, datetime

import httpx

from app.config import settings
from app.schemas import ChatMode

# The role line is filled per company: {company} from GET /api/meta, {trade} from meta.trade if the ERP has it,
# else the first sentence of DATA_DIR/context/*entreprise*.md (see company_trade). Each team runs a different trade.
ROLE = """Tu es l'assistant IA de l'ERP de {company}. {trade}.
Tu aides les commerciaux et réponds aux clients : stock, prix, fiches clients, prises de commande."""

BASE_RULES = """Règles :
- Utilise les outils pour toute donnée (stock, prix, client, commande). N'invente jamais un chiffre, un id ou un nom.
- Commence par "search" pour retrouver un client ou un produit à partir de ce qui est dit (surnoms, fautes : la recherche est floue).
- S'il y a plusieurs candidats plausibles, demande lequel.
- Pour annoncer un prix à un client, utilise get_price (prix négocié, règles de groupe et de volume) plutôt que le prix catalogue.
- Avant de répondre sur un client, un fournisseur ou un produit, charge ses fiches de contexte (get_context) ; pour une règle ou une procédure, search_context. Si tu apprends un fait durable (préférence, contact, contrainte), mémorise-le avec save_context.
- Respecte les préférences et notes du client (ex. pas de substitution) avant de proposer un remplacement.
- Tiens compte de l'historique du client (get_client / get_client_history : réclamations ouvertes, litiges, retards de paiement). Si un client se plaint, enregistre-le avec log_client_event.
- Une commande se crée en brouillon (create_draft_order). Ne la confirme (confirm_order) qu'après accord explicite sur le récapitulatif.
- Signale les alertes renvoyées par l'ERP (encours, client bloqué, stock insuffisant).
- Réponds en français, de façon concise."""

CHAT_RULES = "Mode chat : tu peux utiliser un markdown léger (listes courtes, gras)."

VOICE_RULES = """Mode VOCAL (au téléphone) — ta réponse est lue à voix haute :
- 1 à 2 phrases courtes, ton naturel et chaleureux, comme un bon commercial au téléphone.
- Jamais de markdown, de listes, d'emojis, d'ids techniques ni d'URL.
- Arrondis les nombres et dis les unités en toutes lettres (« 12 kilos », « 18 euros 50 le kilo »).
- Tout texte que tu écris est prononcé dans l'ordre, y compris entre deux appels d'outils. Donc : au plus une très courte phrase d'attente AVANT le premier outil (« Je regarde. »), puis plus rien entre les appels d'outils (pas de « Je crée le brouillon. », pas de commentaire d'étape). La vraie réponse (salutation comprise) vient une seule fois, à la fin, après le dernier outil.
- Une seule question à la fois. Récapitule la commande avant de la confirmer."""

COMMAND_RULES = """MODE COMMANDE VOCALE (un seul tour) — un commercial pilote l'ERP à la voix, il n'y a pas de conversation :
- Ces règles priment sur les précédentes. Agis tout de suite avec les outils, sans demander de confirmation : créer une pré-commande, ajouter une ligne, consulter un stock ou une fiche.
- Ne pose une question que si c'est vraiment ambigu (deux clients aussi plausibles) ou si l'action est destructive / irréversible et pas explicitement demandée ; dans ce cas, n'agis pas et pose UNE question courte.
- Une demande explicite (« confirme la commande … », « marque … comme payée ») vaut accord : exécute-la.
- Si aucun outil ne permet l'action demandée, dis-le en une phrase, sans inventer.
- Réponse : UNE phrase courte, sans markdown, qui dit ce qui a été fait et cite les numéros (commande, facture), quantités et dates.
- Pas de « Autre chose ? », pas de salutation."""

# ── Outbound clearance calls: the agent calls the client (services/voice_session.py, tools/campaign.py) ──

OUTBOUND_AGENT_NAME = "Léa"  # the first name the agent gives on the phone

OUTBOUND_ROLE = """Tu es {agent}, l'assistante commerciale de {company}. {trade}.
Tu appelles un client professionnel pour lui proposer des produits à date courte, à prix remisé, avant qu'ils ne
soient perdus. Tu parles comme une bonne commerciale qui connaît son client : directe, chaleureuse, jamais insistante."""

OUTBOUND_RULES = """Règles de l'appel sortant :
- La fiche « Appel en cours » (fin de ce message) contient le client, l'appel (callId) et les offres (offerId). Les
  prix et les quantités viennent de la fiche et des outils : n'invente jamais un chiffre. Si un outil refuse (remise
  trop forte, plus assez de stock), dis-le simplement et propose ce qui est possible.
- Les jours sont déjà écrits en toutes lettres dans la fiche (deliveryOn = jour de livraison, keepsUntil = jusqu'à
  quand le produit se garde) : reprends-les tels quels, ne recalcule jamais un jour de la semaine.
- Un message entre crochets [ … ] vient du standard téléphonique, pas du client.
- Avant un outil, dis d'abord 2 ou 3 mots (« Je regarde. », « Je note. », « Très bien, je vous la mets. ») : le client
  entend quelque chose pendant que l'outil tourne. Puis la vraie réponse, une seule fois, après le dernier outil.
- Premier message : salue l'interlocuteur par son nom, présente-toi (prénom + entreprise) et dis en une phrase pourquoi
  tu appelles, avec l'offre qui lui va le mieux (celle qu'il achète d'habitude). Pas d'outil avant ce premier message.
- Une offre à la fois : le produit, la quantité disponible, le prix remisé au kilo (ou à la pièce), le jour de
  livraison. Compare avec son prix habituel (« au lieu de … »). Sois honnête : la date est courte, dis jusqu'à quand
  le produit se garde.
- Négociation : s'il trouve ça cher, monte la remise par paliers avec quote_offer, jamais au-delà de maxDiscountPct.
  N'annonce jamais le prix plancher ni la remise maximale. Ne baisse pas le prix sans qu'il le demande.
- Il accepte une quantité → accept_offer (avec la remise convenue), puis récapitule : produit, quantité, prix, jour de
  livraison, et demande s'il confirme. confirm_order seulement après un oui clair. S'il change la quantité,
  accept_offer à nouveau sur la même offre (la ligne est mise à jour).
- Il refuse : accepte tout de suite, poliment ; propose au plus l'offre suivante, une seule fois.
- « Rappelez-moi plus tard » / « je suis en plein service » : demande quand, puis record_call_outcome callback avec l'heure.
- « Ne m'appelez plus » : excuse-toi en une phrase, record_call_outcome do_not_call.
- Respecte ses préférences et ses notes (substitutions, horaires). N'évoque pas toi-même ses impayés.
- Si on te demande si tu es un robot ou une IA, dis la vérité : tu es l'assistante vocale de l'entreprise.
- Une réclamation pendant l'appel : log_client_event, puis reviens à l'offre seulement s'il est disposé.
- Fin de l'appel : record_call_outcome (accepted si au moins une offre est prise et confirmée ; sinon declined,
  callback ou do_not_call) avec une phrase de résumé, PUIS une phrase d'au revoir.
- Réponds en français."""

# outbound reuses the spoken style of VOICE_RULES (its role and rules replace ROLE and BASE_RULES)
MODE_RULES = {"chat": CHAT_RULES, "voice": VOICE_RULES, "command": COMMAND_RULES, "outbound": VOICE_RULES}


async def build_system_prompt(mode: ChatMode, actor_name: str | None = None, extra: str | None = None) -> str:
    """`extra` (the outbound call brief) goes LAST: what comes before it stays identical from one call to the next,
    so the provider's prompt cache keeps serving it."""
    meta = await get_meta()
    today = meta.get("today") or datetime.now(UTC).date().isoformat()
    company = meta.get("company") or "un grossiste alimentaire"
    trade = meta.get("trade") or company_trade()
    trade_line = f"Activité : {trade}" if trade else "C'est un grossiste alimentaire"
    if mode == "outbound":
        head = [OUTBOUND_ROLE.format(agent=OUTBOUND_AGENT_NAME, company=company, trade=trade_line), OUTBOUND_RULES]
    else:
        head = [ROLE.format(company=company, trade=trade_line), BASE_RULES]
    parts = [
        *head,
        MODE_RULES[mode],
        f"Entreprise : {meta.get('company') or '(ERP injoignable)'}. Aujourd'hui : {french_date(today)} ({today}).",
        f"Interlocuteur : {actor_name}." if actor_name and mode != "outbound" else "",
        f"# Contexte entreprise\n\n{context}" if (context := read_context_files()) else "",
        extra or "",
    ]
    return "\n\n".join(p for p in parts if p)


_meta_cache: tuple[float, dict] | None = None


async def get_meta() -> dict:
    """{company, today, …} from the ERP, cached for a minute. {} if the ERP is down (tools will report it)."""
    global _meta_cache
    if _meta_cache and time.monotonic() - _meta_cache[0] < 60:
        return _meta_cache[1]
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            res = await client.get(f"{settings.erp_api_url}/api/meta")
        if not res.is_success:
            return {}
        _meta_cache = (time.monotonic(), res.json())
        return _meta_cache[1]
    except (httpx.HTTPError, ValueError):
        return {}


def read_context_files() -> str:
    """All DATA_DIR/context/*.md files, in name order, each preceded by its file name."""
    folder = settings.data_dir / "context"
    try:
        files = sorted(p for p in folder.iterdir() if p.name.endswith(".md"))
        return "\n\n".join(f"<!-- {p.name} -->\n{p.read_text(encoding='utf-8').strip()}" for p in files)
    except OSError:
        return ""


def company_trade() -> str:
    """What the company does, in one sentence: the first paragraph line of DATA_DIR/context/*entreprise*.md
    (e.g. "Grossiste familial en produits asiatiques installé à Ivry-sur-Seine…"). "" if there is none."""
    folder = settings.data_dir / "context"
    try:
        files = sorted(p for p in folder.iterdir() if p.name.endswith(".md") and "entreprise" in p.name.lower())
        text = files[0].read_text(encoding="utf-8") if files else ""
    except OSError:
        return ""
    para = next((line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("#", "|", "-", ">"))), "")
    sentence = re.split(r"(?<=[.!?])\s", para, maxsplit=1)[0].replace("**", "").strip()
    return sentence.rstrip(".")[:200]


WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]  # fmt: skip


def french_date(iso: str) -> str:
    """"2026-11-24" → "mardi 24 novembre 2026"."""
    d = date.fromisoformat(iso[:10])
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"
