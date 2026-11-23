# La Cabane de Ronce-les-Bains (C0008, `cli_0008`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0008"`).

| | |
|---|---|
| Type | Restaurant (Écaillers, bars à huîtres et dégustations) — Port de La Grève, 17390 La Tremblade |
| Zone | Marennes, Oléron & Royan — livré mon, wed, fri |
| Statut | **blocked** — client régulier |
| Commercial | Bastien Moreau |
| Client depuis | 01/06/2011 (15 ans) |
| Paiement | 15 jours — mauvais payeur, compte à risque |
| Encours | 20 355,61 € TTC (plafond 10 000 €) — **14 facture échues : 18 368,03 €** |
| Tempérament | fidèle, râleur — qualité ★★☆, prix ★☆☆, risque de départ faible |

## Qui ils sont

Cabane de dégustation sur le port de La Grève, ouverte d'avril à fin décembre. Paie toujours en retard après la saison, mais a toujours fini par payer.

## Les personnes

- **Marie-Hélène Sauvaget** — Gérante · 06 12 48 73 59
  - Comment il/elle parle : Franche, reconnaît ses retards, fatiguée après la saison.
  - Tics : « La saison a été pourrie, laisse-moi jusqu'à Noël. » · « Je paierai tout, comme chaque année. »

## Ce qu'ils achètent et comment

Commande trois fois par semaine, en général la veille entre 7h et 15h, surtout par téléphone et WhatsApp. Produits habituels : fine de claire n°3 en colis vrac 5 kg (écailler), spéciale de claire n°2 (bourriche 4 douzaines), fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines), crevette rose cuite 30/40, pain de seigle tranché (pièce 500 g), vinaigre à l'échalote (bouteille 1 L), beurre demi-sel AOP Charentes-Poitou (plaque 1 kg), pousse en claire Label Rouge n°2.

Sur les 90 derniers jours (30 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Fine de claire n°3 en colis vrac 5 kg (écailler) (`prd_0004`) | 487,14 kg | 12 | 6,58 € |
| Bourriche cadeau 3 douzaines spéciales n°2 (`prd_0012`) | 56,00 pièce | 7 | 34,22 € |
| Fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) (`prd_0002`) | 160,00 douzaine | 14 | 8,77 € |
| Crevette rose cuite 30/40 (`prd_0027`) | 68,15 kg | 15 | 18,66 € |
| Spéciale de claire n°2 (bourriche 4 douzaines) (`prd_0006`) | 64,00 douzaine | 11 | 12,46 € |
| Amande de mer (`prd_0021`) | 78,03 kg | 14 | 9,30 € |
| Pain de seigle tranché (pièce 500 g) (`prd_0045`) | 189,00 pièce | 12 | 3,20 € |
| Pousse en claire Label Rouge n°2 (`prd_0008`) | 27,00 douzaine | 4 | 19,19 € |

Fiche CRM : _Fines en colis vrac, crevettes, pain de seigle, vinaigre échalote._

## Ce qui les agace

- Être bloquée juste avant les fêtes

## Historique marquant

- **12/08/2025** · paiement · Relance : FAC-2025-002374 échue
- **10/11/2025** · paiement · Relance : FAC-2025-003482 échue
- **28/11/2025** · demande spéciale · Fêtes 2025 : réservation de volumes
- **29/12/2025** · paiement · Relance : FAC-2025-004071 échue
- **07/03/2026** · paiement · Relance : FAC-2026-000461 échue
- **07/03/2026** · carte · Nouvelle carte de printemps
- **08/05/2026** · paiement · Relance : FAC-2026-001257 échue
- **29/06/2026** · retour · Retour à la livraison : vinaigre à l'échalote (bouteille 1 L)
- **07/07/2026** · paiement · Relance : FAC-2026-001933 échue
- **23/09/2026** · paiement · Relance : FAC-2026-002913 échue
- **12/11/2026** · paiement · Compte bloqué pour impayés
- **16/11/2026** · échéancier · Proposition d'échéancier

Historique complet : 64 événements (18 paiement, 9 appel, 8 visite, 7 compliment, 6 demande spéciale, 5 qualité, 3 prix, 2 note, 2 retour, 1 ouverture, 1 livraison, 1 carte, 1 échéancier).

## Dossiers ouverts aujourd'hui

- **23/09/2026 — Relance : FAC-2026-002913 échue** (`inv_012384`)
  Facture FAC-2026-002913 (2 493,47 € TTC) échue le 08/09/2026. Relance par mail. Marie-Hélène : « Le comptable s'en occupe. »
- **12/11/2026 — Compte bloqué pour impayés** (`inv_012123`)
  14 factures échues non réglées pour 18 368,03 € TTC. Livraisons suspendues jusqu'à règlement d'au moins 50 % de l'échu et un échéancier validé.
- **16/11/2026 — Proposition d'échéancier**
  Marie-Hélène propose de payer en 4 fois et demande la reprise des livraisons. En attente de validation par la direction.

## Comment leur parler

- Rester humain : proposer un échéancier, voir avec Sylvie.

## Prix & groupe

Groupes : **Écaillers** (`ecaillers`), **À surveiller** (`a-surveiller`).
Prix négociés (`clientPrices`, toujours prioritaires) : bourriche cadeau 3 douzaines spéciales n°2 34,28 € (liste 36,50) ; fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) 8,64 € (liste 9,20) ; amande de mer 8,92 € (liste 9,50).

Règles de prix qui peuvent s'appliquer :
- `prl_0001` Écaillers : −5 % sur le colis vrac
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre

Dernières notes CRM :

- 31/01/2026, Bastien (commercial) : Encours au-dessus du plafond, blocage le 12/11.
- 21/06/2026, Bastien (commercial) : Demande à être livrée pour les fêtes contre un premier versement : décision de Jean-Luc attendue.
- 12/11/2026, Sylvie (comptabilité) : Compte BLOQUÉ pour impayés. 14 factures échues non réglées, la plus ancienne FAC-2026-002652 du 03/08/2026. Encours 20 355,61 € TTC pour un plafond de 10 000,00 €. Reprise des livraisons seulement après un règlement d'au moins 50 % de l'échu et un échéancier validé par la direction.
- 20/11/2026, Bastien (commercial) : Marie-Hélène a rappelé pour reprendre les livraisons et promet un virement. Ne rien livrer sans l'accord de la comptabilité.
