# L'Estran de Saint-Martin-de-Ré (C0048, `cli_0048`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0048"`).

| | |
|---|---|
| Type | Restaurant (Restaurants de poisson et gastronomie du littoral) — 81 bis rue Saint-Nicolas, 17410 Saint-Martin-de-Ré |
| Zone | La Rochelle, Ré & Aunis — livré wed, sat |
| Statut | **active** — client régulier |
| Commercial | Élodie Guillotin |
| Client depuis | 09/08/2019 (7 ans) |
| Paiement | 30 jours — paie en retard (3 à 6 semaines), mais finit par payer |
| Encours | 11 885,97 € TTC (plafond 16 000 €) — **8 facture échues : 5 759,70 €** |
| Tempérament | impulsif — qualité ★★☆, prix ★★★, risque de départ moyen |

## Qui ils sont

Restaurant de poisson à Saint-Martin-de-Ré, 120 couverts, carte qui suit la pêche de La Cotinière et les huîtres de la maison.

## Les personnes

- **Jean-Luc Muller** — Chef-patron · 06 34 88 81 45 · jean-luc@estransaintmartinre.fr
  - Comment il/elle parle : Change d'avis souvent, commandes de dernière minute.
  - Tics : « Je change tout pour demain. » · « On improvise. »
- **Amadou Simon** — Second de cuisine · 06 73 77 47 76
  - Comment il/elle parle : S'emballe vite, en positif comme en négatif.
  - Tics : « On improvise. » · « Vous avez quoi de beau ? »

## Ce qu'ils achètent et comment

Commande deux fois par semaine, en général la veille entre 9h et 15h, surtout par téléphone et WhatsApp. Produits habituels : spéciale de claire n°2 (bourriche 4 douzaines), palourde japonaise d'élevage, sole de La Cotinière 300-400 g, bar de ligne de l'île d'Oléron, salicorne fraîche des marais (barquette 500 g), moule de bouchot de Charron (sac 10 kg), fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines), coque.

Sur les 90 derniers jours (24 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Moule de bouchot de Charron (sac 10 kg) (`prd_0013`) | 1 206,25 kg | 16 | 3,71 € |
| Fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) (`prd_0001`) | 534,00 douzaine | 17 | 7,58 € |
| Bar de ligne de l'île d'Oléron (`prd_0034`) | 117,29 kg | 10 | 31,89 € |
| Sole de La Cotinière 300-400 g (`prd_0035`) | 109,62 kg | 15 | 31,58 € |
| Spéciale de claire n°2 (bourriche 4 douzaines) (`prd_0006`) | 120,00 douzaine | 8 | 12,41 € |
| Palourde japonaise d'élevage (`prd_0017`) | 96,87 kg | 15 | 13,58 € |
| Fine de claire Marennes-Oléron n°4 (bourriche 4 douzaines) (`prd_0003`) | 206,00 douzaine | 12 | 6,01 € |
| Maigre de l'estuaire de la Gironde (`prd_0036`) | 54,14 kg | 5 | 18,47 € |

Fiche CRM : _Poisson de La Cotinière du jour. Salicorne en saison. Livré mer. sam. Refuse les substitutions : appeler avant toute modification._

## Ce qui les agace

- Une substitution sans appel
- Une livraison après 10 h
- Un poisson de la veille

## Historique marquant

- **19/09/2025** · paiement · Relance : FAC-2025-002682 échue
- **28/09/2025** · carte · Carte d'automne
- **14/11/2025** · paiement · Relance : FAC-2025-003392 échue
- **31/12/2025** · retour · Retour à la livraison : bar de ligne de l'île d'Oléron
- **17/01/2026** · paiement · Relance : FAC-2025-004136 échue
- **06/03/2026** · carte · Nouvelle carte de printemps
- **22/03/2026** · paiement · Relance : FAC-2026-000455 échue
- **04/05/2026** · paiement · Relance : FAC-2026-001041 échue
- **20/06/2026** · paiement · Relance : FAC-2026-001581 échue
- **07/08/2026** · paiement · Relance : FAC-2026-002161 échue
- **21/09/2026** · paiement · Relance : FAC-2026-002735 échue
- **09/11/2026** · paiement · Relance : FAC-2026-003361 échue

Historique complet : 65 événements (21 paiement, 14 visite, 4 qualité, 4 demande spéciale, 3 prix, 3 compliment, 3 appel, 3 retour, 2 note, 2 réclamation, 2 livraison, 2 carte, 1 ouverture, 1 avoir).

## Dossiers ouverts aujourd'hui

- **09/11/2026 — Relance : FAC-2026-003361 échue** (`inv_012832`)
  Facture FAC-2026-003361 (976,42 € TTC) échue le 26/10/2026. Relance par téléphone. Jean-Luc : « J'ai eu un trou de trésorerie, je régularise. »

## Comment leur parler

- Parler produit et producteur.
- Annoncer les ruptures avant le départ du camion.

## Prix & groupe

Groupes : **Restaurants** (`restaurants`), **À surveiller** (`a-surveiller`).
Prix négociés (`clientPrices`, toujours prioritaires) : fine de claire Marennes-Oléron n°4 (bourriche 4 douzaines) 6,03 € (liste 6,40) ; moule de bouchot de Charron (sac 10 kg) 3,72 € (liste 3,95) ; bar de ligne de l'île d'Oléron 32,01 € (liste 34,00) ; salicorne fraîche des marais (barquette 500 g) 4,61 € (liste 4,90).

Règles de prix qui peuvent s'appliquer :
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre

Dernières notes CRM :

- 02/11/2025, Élodie (commerciale) : Jean-Luc prend ses langoustines vivantes le vendredi.
- 22/06/2026, Élodie (commerciale) : Le chef veut les premières vertes d'octobre.
