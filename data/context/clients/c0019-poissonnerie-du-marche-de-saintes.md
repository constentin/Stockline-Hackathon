# Poissonnerie du Marché de Saintes (C0019, `cli_0019`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0019"`).

| | |
|---|---|
| Type | Commerce de détail (Poissonniers et étals de marché) — 57 quai de la Fontaine, 17100 Saintes |
| Zone | Rochefort, Saintes & Cognac — livré mon, tue, thu |
| Statut | **active** — en croissance |
| Commercial | Bastien Moreau |
| Client depuis | 10/01/2006 (20 ans) |
| Paiement | 30 jours — paie à l'échéance, sans histoire |
| Encours | 5 573,85 € TTC (plafond 9 000 €) |
| Tempérament | économe — qualité ★☆☆, prix ★★★, risque de départ moyen |

## Qui ils sont

Poissonnerie de Saintes, étal de 5 mètres et un bac à huîtres en vitrine. Hugo Esposito réserve ses bourriches de Noël dès la mi-novembre.

## Les personnes

- **Hugo Esposito** — Poissonnier-gérant · 06 75 60 43 32 · hugo@poissonneriemarchesainte.fr
  - Comment il/elle parle : Calcule tout, demande les promos et les fins de lot.
  - Tics : « Vous avez des fins de lot ? » · « Il y a une promo cette semaine ? »
- **Rachel Mathieu** — Vendeuse · 06 86 42 85 93
  - Comment il/elle parle : Commande au plus juste, petites quantités.
  - Tics : « C'est combien au kilo, ramené ? » · « Vous avez des fins de lot ? »

## Ce qu'ils achètent et comment

Commande trois fois par semaine, en général la veille entre 6h et 15h, surtout par téléphone et WhatsApp. Produits habituels : spéciale de claire n°2 (bourriche 4 douzaines), crevette rose cuite 30/40, bulot cuit, palourde japonaise d'élevage, fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines), pétoncle vanneau, spéciale de claire n°3 (bourriche 4 douzaines), tourteau cuit.

Sur les 90 derniers jours (37 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Palourde japonaise d'élevage (`prd_0017`) | 181,90 kg | 20 | 13,50 € |
| Fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) (`prd_0001`) | 292,00 douzaine | 16 | 7,60 € |
| Fine de claire n°3 en colis vrac 5 kg (écailler) (`prd_0004`) | 325,30 kg | 13 | 6,49 € |
| Spéciale de claire n°2 (bourriche 4 douzaines) (`prd_0006`) | 168,00 douzaine | 22 | 12,50 € |
| Tourteau cuit (`prd_0031`) | 112,70 kg | 15 | 13,86 € |
| Pousse en claire Label Rouge n°2 (`prd_0008`) | 79,00 douzaine | 11 | 19,45 € |
| Fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) (`prd_0002`) | 158,00 douzaine | 15 | 9,21 € |
| Bulot cuit (`prd_0022`) | 129,51 kg | 17 | 10,80 € |

Fiche CRM : _Moules de bouchot en saison, d'Espagne sinon. Réservation de Noël avant le 10 décembre. Livré lun. mar. jeu. Refuse les substitutions : appeler avant toute modification._

## Ce qui les agace

- Les prix de décembre annoncés trop tard
- Des huîtres laiteuses en vitrine
- Une bourriche avec des huîtres ouvertes

## Historique marquant

- **03/04/2025** · retour · Retour à la livraison : bulot cuit
- **29/04/2025** · réclamation · Rupture : tourteau cuit non livré
- **13/05/2025** · retour · Retour à la livraison : tourteau cuit
- **19/06/2025** · livraison · Livraison laissée devant la porte
- **23/09/2025** · retour · Retour à la livraison : bulot cuit
- **29/09/2025** · note · Agrandissement : plus de couverts
- **09/11/2025** · demande spéciale · Fêtes 2025 : réservation de volumes
- **22/01/2026** · prix · Tarifs 2026
- **05/02/2026** · qualité · Qualité : fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines)
- **18/05/2026** · qualité · Qualité : fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines)
- **20/05/2026** · avoir · Avoir qualité : fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines)
- **23/07/2026** · qualité · Qualité : bulot cuit

Historique complet : 56 événements (14 visite, 8 demande spéciale, 7 compliment, 6 appel, 5 qualité, 5 réclamation, 4 prix, 3 retour, 1 ouverture, 1 livraison, 1 note, 1 avoir).

## Dossiers ouverts aujourd'hui

Aucun.

## Comment leur parler

- Parler calibres et affinage.
- Tutoyer s'il tutoie, c'est un métier de terrain.
- Proposer de réserver les fêtes tôt.

## Prix & groupe

Groupes : **Poissonniers** (`poissonniers`).
Prix négociés (`clientPrices`, toujours prioritaires) : spéciale de claire n°3 (bourriche 4 douzaines) 10,15 € (liste 10,80) ; fine de claire n°3 en colis vrac 5 kg (écailler) 6,48 € (liste 6,90) ; bulot cuit 10,80 € (liste 11,50).

Règles de prix qui peuvent s'appliquer :
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre
- `prl_0007` Poissonniers : −3 % sur les crevettes cuites

Dernières notes CRM :

- 05/11/2025, Bastien (commercial) : Hugo réserve ses volumes de Noël chaque année la deuxième semaine de novembre.
- 25/07/2026, Bastien (commercial) : Ne veut aucune substitution sans être prévenu : si un produit manque, appeler Hugo avant le départ du camion.
