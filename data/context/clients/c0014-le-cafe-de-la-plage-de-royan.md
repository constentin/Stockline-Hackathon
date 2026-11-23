# Le Café de la Plage de Royan (C0014, `cli_0014`)

> Fiche persona pour jouer ce client au téléphone. L'historique complet est dans `seed/clientEvents.json` (filtrer sur `clientId: "cli_0014"`).

| | |
|---|---|
| Type | Brasserie (Brasseries et restaurants de plage) — 79 rue Thiers, 17200 Royan |
| Zone | Marennes, Oléron & Royan — livré mon, thu |
| Statut | **active** — nouveau client |
| Commercial | Élodie Guillotin |
| Client depuis | 15/10/2026 (0 an) |
| Paiement | 45 jours — paie à l'échéance, sans histoire |
| Encours | 8 507,17 € TTC (plafond 11 000 €) |
| Tempérament | pressé, négociateur, désorganisé — qualité ★★★, prix ★★★, risque de départ moyen |

## Qui ils sont

Brasserie de bord de mer à Royan, 25 couverts, moules-frites et plateaux de fruits de mer, terrasse pleine de mai à septembre.

## Les personnes

- **Fabrice André** — Chef de cuisine · 06 34 69 21 14
  - Comment il/elle parle : Joue la surprise sur chaque tarif, finit par commander.
  - Tics : « À ce prix-là, je prends ailleurs. » · « Et si j'en prends le double ? »
- **Tony Guyot** — Directeur · 06 97 71 60 02
  - Comment il/elle parle : Appelle tard, oublie des produits, rappelle dix minutes après.
  - Tics : « C'était quoi déjà la dernière fois ? » · « Ah attends, j'ai oublié un truc… »

## Ce qu'ils achètent et comment

Commande deux fois par semaine, en général la veille entre 10h et 15h, surtout par téléphone et e-mail. Produits habituels : crevette rose cuite 30/40, bulot cuit, anneaux d'encornet à frire surgelés, spéciale de claire n°3 (bourriche 4 douzaines), moule de bouchot de Charron (sac 10 kg), praire, fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines), homard bleu vivant 500-700 g.

Sur les 90 derniers jours (11 commandes livrées) :

| Produit | Volume 90 j | Lignes | Dernier prix |
|---|---|---|---|
| Spéciale de claire n°3 (bourriche 4 douzaines) (`prd_0007`) | 156,00 douzaine | 6 | 10,80 € |
| Moule de bouchot de Charron (sac 10 kg) (`prd_0013`) | 386,69 kg | 7 | 3,76 € |
| Crevette rose cuite 30/40 (`prd_0027`) | 79,05 kg | 6 | 17,77 € |
| Homard bleu vivant 500-700 g (`prd_0033`) | 19,83 kg | 3 | 59,45 € |
| Fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) (`prd_0001`) | 118,00 douzaine | 4 | 7,16 € |
| Anneaux d'encornet à frire surgelés (`prd_0053`) | 71,83 kg | 3 | 9,20 € |
| Fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) (`prd_0002`) | 74,00 douzaine | 3 | 8,80 € |
| Bulot cuit (`prd_0022`) | 57,14 kg | 5 | 10,95 € |

Fiche CRM : _Moules en volume l'été, prix fixé à la semaine. Facture à la semaine. Livré lun. jeu. Accepte les substitutions équivalentes si on prévient._

## Ce qui les agace

- Les moules sableuses
- Les hausses non annoncées

## Historique marquant

- **15/10/2026** · ouverture · Ouverture de compte : Le Café de la Plage de Royan
- **12/11/2026** · qualité · Qualité : spéciale de claire n°3 (bourriche 4 douzaines)

Historique complet : 3 événements (1 ouverture, 1 appel, 1 qualité).

## Dossiers ouverts aujourd'hui

- **12/11/2026 — Qualité : spéciale de claire n°3 (bourriche 4 douzaines)** (`ord_024670`, `inv_013469`)
  Fabrice appelle à réception : huîtres sèches, peu de chair (commande CMD-2026-007206, 24 douzaines). Tony : « Ah attends, j'ai oublié un truc… »

## Comment leur parler

- Parler volumes et prix à la semaine.
- Proposer la moule d'Espagne quand le bouchot manque.

## Prix & groupe

Groupes : **Brasseries** (`brasseries`).
Prix négociés (`clientPrices`, toujours prioritaires) : fine de claire Marennes-Oléron n°3 (bourriche 4 douzaines) 7,24 € (liste 7,60) ; moule de bouchot de Charron (sac 10 kg) 3,76 € (liste 3,95) ; bulot cuit 10,95 € (liste 11,50) ; crevette rose cuite 30/40 17,62 € (liste 18,50) ; fine de claire Marennes-Oléron n°2 (bourriche 4 douzaines) 8,76 € (liste 9,20).

Règles de prix qui peuvent s'appliquer :
- `prl_0005` Volume : moules de bouchot −6 % dès 50 kg
- `prl_0006` Fêtes 2026 : fines n°3 +6 % du 20 au 31 décembre

Dernières notes CRM :

- 16/10/2026, Élodie (commerciale) : Fabrice commande 300 kg de moules par semaine en juillet-août.
