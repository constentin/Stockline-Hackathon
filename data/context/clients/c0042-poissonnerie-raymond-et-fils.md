# Poissonnerie Raymond & Fils (C0042, `cli_0042`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0042"`).

| | |
|---|---|
| Type | Commerce de détail (Poissonniers et étals de marché) — 135 rue Jean-Moulin, 16100 Cognac |
| Zone | Rochefort, Saintes & Cognac — livré mon, tue, wed, thu, fri |
| Statut | **active** — client régulier |
| Commercial | Bastien Moreau |
| Client depuis | 26/09/2023 (3 ans) |
| Paiement | 15 jours — paie en retard (3 à 6 semaines), mais finit par payer |
| Encours | 18 149,75 € TTC (plafond 23 000 €) — **6 facture échues : 12 111,17 €** |
| Tempérament | négociateur, méfiant, fidèle — qualité ★★☆, prix ★★★, risque de départ faible |

## Qui ils sont

Poissonnier sur les marchés de Cognac et des alentours, trois matinées par semaine ; les huîtres font la moitié de son chiffre en décembre.

## Les personnes

- **Florian Schmitt** — Poissonnier-gérant · 06 16 47 31 34 · florian@poissonnerieraymondfils.fr
  - Comment il/elle parle : Joue la surprise sur chaque tarif, finit par commander.
  - Tics : « Vous me faites un geste ? » · « Et si j'en prends le double ? »
- **Inès Brunet** — Vendeuse · 07 75 85 19 34
  - Comment il/elle parle : Calme, habitué, appelle à heure fixe.
  - Tics : « On travaille ensemble depuis le temps… » · « Comme d'habitude. »

## Ce qu'ils achètent et comment

Commande tous les jours de livraison, en général la veille entre 6h et 14h, surtout par téléphone et WhatsApp. Produits habituels : fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines), spéciale de claire n°2 (bourriche 4 douzaines), moule de bouchot de Charron (sac 10 kg), crevette rose cuite 30/40, bulot cuit, palourde japonaise d'élevage, moule de corde d'Espagne (Galice), coque.

Sur les 90 derniers jours (55 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Bourriche familiale 8 douzaines fines n°3 (`prd_0011`) | 82,00 pièce | 13 | 54,03 € |
| Moule de bouchot de Charron (sac 10 kg) (`prd_0013`) | 1 052,57 kg | 30 | 3,94 € |
| Bar de ligne de l'île d'Oléron (`prd_0034`) | 112,42 kg | 25 | 34,00 € |
| Fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) (`prd_0001`) | 415,00 douzaine | 30 | 7,60 € |
| Spéciale de claire n°2 (bourriche 4 douzaines) (`prd_0006`) | 141,00 douzaine | 22 | 12,50 € |
| Crevette rose cuite 30/40 (`prd_0027`) | 95,71 kg | 24 | 17,95 € |
| Moule de corde d'Espagne (Galice) (`prd_0015`) | 427,99 kg | 22 | 3,25 € |
| Coque (`prd_0019`) | 186,80 kg | 24 | 5,85 € |

Fiche CRM : _Moules de bouchot en saison, d'Espagne sinon. Réservation de Noël avant le 10 décembre. Livré lun. mar. mer. jeu. ven. Accepte les substitutions équivalentes si on prévient._

## Ce qui les agace

- Des huîtres laiteuses en vitrine
- Une bourriche avec des huîtres ouvertes

## Historique marquant

- **16/12/2025** · paiement · Relance : FAC-2025-003927 échue
- **21/01/2026** · prix · Tarifs 2026
- **02/02/2026** · paiement · Relance : FAC-2026-000045 échue
- **05/04/2026** · paiement · Relance : FAC-2026-000838 échue
- **26/05/2026** · paiement · Relance : FAC-2026-001443 échue
- **11/06/2026** · qualité · Qualité : spéciale de claire n°2 (bourriche 4 douzaines)
- **13/06/2026** · avoir · Avoir qualité : spéciale de claire n°2 (bourriche 4 douzaines)
- **09/07/2026** · retour · Retour à la livraison : coque
- **12/07/2026** · paiement · Relance : FAC-2026-002030 échue
- **08/09/2026** · paiement · Relance : FAC-2026-002757 échue
- **23/10/2026** · paiement · Relance : FAC-2026-003383 échue
- **24/11/2026** · réclamation · Rupture : huître plate (belon de Bretagne) n°0 non livré (remplacé)

Historique complet : 48 événements (19 paiement, 5 appel, 4 visite, 4 qualité, 4 avoir, 3 compliment, 3 demande spéciale, 2 retour, 2 prix, 1 ouverture, 1 réclamation).

## Dossiers ouverts aujourd'hui

- **23/10/2026 — Relance : FAC-2026-003383 échue** (`inv_012854`)
  Facture FAC-2026-003383 (2 014,96 € TTC) échue le 13/10/2026. Relance par téléphone. Florian : « C'est parti cette semaine. »
- **24/11/2026 — Rupture : huître plate (belon de Bretagne) n°0 non livré (remplacé)** (`ord_024906`)
  Huître plate (belon de Bretagne) n°0 manquant sur la commande CMD-2026-007442. Remplacé par un produit équivalent avec son accord. Florian : « Vous me faites un geste ? »

## Comment leur parler

- Parler calibres et affinage.
- Proposer de réserver les fêtes tôt.
- Tutoyer s'il tutoie, c'est un métier de terrain.

## Prix & groupe

Groupes : **Poissonniers** (`poissonniers`), **À surveiller** (`a-surveiller`).
Pas de prix négocié.

Règles de prix qui peuvent s'appliquer :
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre
- `prl_0007` Poissonniers : −3 % sur les crevettes cuites

Dernières notes CRM :

- 31/03/2026, Bastien (commercial) : Les bourriches de 8 douzaines partent pour les comités d'entreprise.
- 06/06/2026, Sylvie (comptabilité) : Paie systématiquement en retard (3 à 5 semaines). Relancer par téléphone, Florian ne lit pas les mails de relance.
- 18/10/2026, Bastien (commercial) : Bastien lui a proposé palourde japonaise d'élevage pour l'étal du week-end.
