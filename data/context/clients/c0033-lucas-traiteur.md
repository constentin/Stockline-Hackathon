# Lucas Traiteur (C0033, `cli_0033`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0033"`).

| | |
|---|---|
| Type | Traiteur (Traiteurs et plateaux de fruits de mer) — 93 avenue du Général-de-Gaulle, 17000 La Rochelle |
| Zone | La Rochelle, Ré & Aunis — livré tue, fri |
| Statut | **active** — en baisse |
| Commercial | Élodie Guillotin |
| Client depuis | 13/08/2006 (20 ans) |
| Paiement | 30 jours — paie en retard (3 à 6 semaines), mais finit par payer |
| Encours | 0,00 € TTC (plafond 9 500 €) |
| Tempérament | négociateur, désorganisé — qualité ★☆☆, prix ★★★, risque de départ élevé |

## Qui ils sont

Traiteur de La Rochelle spécialisé dans les plateaux de fruits de mer à emporter : 600 plateaux le 24 et le 31 décembre.

## Les personnes

- **Fabrice Thomas** — Gérant · 06 73 30 95 92 · fabrice@lucastraiteur.fr
  - Comment il/elle parle : Parle en faisant autre chose, bruit de cuisine derrière.
  - Tics : « C'était quoi déjà la dernière fois ? » · « Vous pouvez encore rajouter ? »
- **Rachid Roussel** — Chef de production · 06 98 15 18 46
  - Comment il/elle parle : Commence toujours par le prix, compare avec la concurrence.
  - Tics : « À ce prix-là, je prends ailleurs. » · « Vous êtes plus cher que votre voisin, là. »

## Ce qu'ils achètent et comment

Commande de façon irrégulière (dépannages, événements), en général 2 jours avant entre 9h et 16h, surtout par téléphone et e-mail. Produits habituels : fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines), crevette rose cuite 30/40, tourteau cuit, bulot cuit, goémon frais pour présentation (colis 2 kg), spéciale de claire n°2 (bourriche 4 douzaines), fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines), spéciale de claire n°3 (bourriche 4 douzaines).

Sur les 90 derniers jours (1 commande livrée) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) (`prd_0001`) | 106,00 douzaine | 1 | 7,69 € |
| Spéciale de claire n°3 (bourriche 4 douzaines) (`prd_0007`) | 51,00 douzaine | 1 | 10,23 € |
| Crevette rose cuite 30/40 (`prd_0027`) | 25,91 kg | 1 | 18,74 € |
| Spéciale de claire n°2 (bourriche 4 douzaines) (`prd_0006`) | 37,00 douzaine | 1 | 12,56 € |
| Fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) (`prd_0002`) | 50,00 douzaine | 1 | 9,20 € |
| Bulot cuit (`prd_0022`) | 22,85 kg | 1 | 10,47 € |

Fiche CRM : _Réservations de Noël bloquées début novembre. Goémon pour les plateaux. Livré mar. ven. Refuse les substitutions : appeler avant toute modification._

## Ce qui les agace

- Rupture le 24 décembre
- Crustacés vides

## Historique marquant

- **27/12/2024** · demande spéciale · Fêtes 2024 : réservation de volumes
- **20/01/2025** · prix · Tarifs 2025
- **10/03/2025** · carte · Nouvelle carte de printemps
- **12/05/2025** · paiement · Relance : FAC-2025-001140 échue
- **11/07/2025** · paiement · Relance : FAC-2025-001852 échue
- **15/10/2025** · paiement · Relance : FAC-2025-002976 échue
- **08/12/2025** · demande spéciale · Fêtes 2025 : réservation de volumes
- **13/03/2026** · paiement · Relance : FAC-2026-000363 échue
- **21/03/2026** · carte · Nouvelle carte de printemps
- **10/08/2026** · paiement · Relance : FAC-2026-002252 échue
- **15/10/2026** · paiement · Relance : FAC-2026-003046 échue
- **09/11/2026** · demande spéciale · Fêtes 2026 : réservation de volumes

Historique complet : 55 événements (17 visite, 7 demande spéciale, 7 paiement, 6 appel, 5 compliment, 4 carte, 3 qualité, 2 note, 2 prix, 1 ouverture, 1 livraison).

## Dossiers ouverts aujourd'hui

- **09/11/2026 — Fêtes 2026 : réservation de volumes**
  Rachid veut réserver pour les fêtes : crevette grise cuite et crevette rose cuite 30/40 en volume pour les 23-24 et 30-31/12. Fabrice : « Vous pouvez encore rajouter ? »

## Comment leur parler

- Faire préciser le nombre de plateaux et la date.
- Réserver les volumes de fêtes tôt.

## Prix & groupe

Groupes : **Traiteurs** (`traiteurs`), **À surveiller** (`a-surveiller`).
Prix négociés (`clientPrices`, toujours prioritaires) : huître creuse de Bretagne Nord n°3 7,44 € (liste 7,90) ; spéciale de claire n°3 (bourriche 4 douzaines) 10,17 € (liste 10,80) ; tourteau cuit 13,18 € (liste 14,00).

Règles de prix qui peuvent s'appliquer :
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre
- `prl_0008` Traiteurs : −5 % sur les crustacés en décembre

Dernières notes CRM :

- 28/06/2026, Élodie (commerciale) : Commande irrégulièrement, surtout en dépannage. Lui proposer les arrivages quand il appelle.
- 04/11/2026, Élodie (commerciale) : Ne veut aucune substitution sans être prévenu : si un produit manque, appeler Fabrice avant le départ du camion.
