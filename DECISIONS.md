# DECISIONS.md — Agent vocal de déstockage, Les Parcs de Marennes

Équipe : Constantin Berthoud, Mathieu Amarger, Victor Borck, Corentin Zornette, Marius Amerdeil.

---

## 1. Pourquoi ce problème

**Les Parcs de Marennes perdent l'équivalent de 30 % de leur chiffre d'affaires en stock périmé.** Nos slides en
donnent trois causes :

- des produits frais : huîtres, coquillages, crustacés cuits, qui se gardent quelques jours ;
- des ventes qui dépendent des saisons ;
- de gros stocks achetés d'un coup.

La parade est connue : **proposer ces articles bradés aux clients avant la date limite**. Elle n'est presque jamais
appliquée. Il faudrait chaque matin :

1. repérer les lots qui ne partiront pas au tarif normal ;
2. trouver les clients susceptibles de les prendre ;
3. calculer une remise qui ne détruise pas les ventes normales ;
4. appeler 30 restaurateurs en plein service ;
5. tout saisir dans l'ERP.

C'est exactement la chaîne que nos slides proposent d'automatiser :

> un modèle cherche les priorités de ventes → un modèle cherche les clients susceptibles d'acheter → un modèle appelle
> pour proposer les promotions → si l'on vend, le stock est à jour ; si le client fait une contre-offre, on évalue sa
> rentabilité ; dans tous les cas, le modèle apprend et enregistre les préférences du client.

**Pourquoi c'est le bon problème pour ce hackathon :**

| Critère du hackathon | Ce que ce problème apporte |
|---|---|
| **Pertinence** | La perte se chiffre directement en euros : **9 849 €** de stock à risque au prix d'achat sur les lots qui expirent dans 8 jours, dans les données du 24/11/2026. |
| **Un agent vocal a du sens** | Les clients sont des restaurateurs, joignables au téléphone et rarement devant un écran. Un appel court, cadré et répétitif est ce qu'un agent vocal fait le mieux, et ce qu'un commercial n'a pas le temps de faire 30 fois par jour. |
| **Smart** | L'agent utilise tout l'ERP : stock par lot et DLC, prix négociés, tournées, crédit, historique d'achats. |
| **Mesurable** | Chaque appel finit dans l'ERP (commande confirmée, refus, rappel), donc on peut tester le taux de réussite. |
| **La donnée qui manque** | Nos slides le soulignent : l'ERP est riche (24 622 livraisons, 1 449 couples client × produit, 85 clients, 93 prix négociés) mais **n'a aucun historique d'offres et de refus**. On ne peut donc pas mesurer l'effet réel d'une remise. L'agent crée cette donnée : chaque offre, sa remise et la réponse du client sont enregistrées, ce qui permet d'apprendre. |

---

## 2. Ce que nous avons construit

```
Page Déstockage ─ « Calculer depuis le stock » ─▶ modèle économique : lots à risque, clients, remise par client
   ▼
Campagne (ERP) ─ « Lancer les appels » ─▶ l'agent appelle (navigateur ou vrai téléphone via Twilio)
   ouverture variée → le client choisit → quantités → récapitulatif → « je valide ? »
   négociation bornée · rappel à l'heure demandée · « ne m'appelez plus »
   ▼
ERP mis à jour en direct : commande confirmée, stock réservé, résultat dans l'historique du client
```

Modèle : `apps/backend/app/services/clearance.py`. Règles des campagnes : `apps/backend/app/services/campaigns.py`.
Agent : `apps/ai/app/services/` et `apps/ai/app/tools/campaign.py`.

---

## 3. Le modèle économique

Il suit notre document « Décider des promotions sur les stocks périssables » (prévoir, décider, apprendre).

1. **Demande normale par analogues calendaires** : le même jour, 2 à 17 semaines plus tôt, avec un poids divisé par 2
   toutes les 8 semaines et les jours sans livraison comptés à 0.
   $\widehat{D} = \sum w_i D_i / \sum w_i$
2. **Stock libre** $S_\ell$ (stock − réservations) et **demande ouverte** (prévision − commandes déjà passées), sans
   compter deux fois. La demande est affectée jour par jour aux lots qui expirent en premier.
3. **Risque** : $Q_r = \max(0, S_\ell - N_\ell)$ et $R = Q_r / S_\ell$.
4. **Remise par client** : $d^* = 40\,\% \times \lambda \times (1+\varepsilon_c) \times R \times U(J)$, avec
   $U(J) = 0{,}5 + 0{,}5/(1+e^{3(J-2)})$. Elle est plafonnée (campagne, prix plancher) et le prix est arrondi au centime.
5. **Classement** par contribution
   $G = f\,[\,q(p - c - A) - k\max(0, p_0 - c - A)\,] - F$ : une offre dont $G \le 0$ est écartée, car la remise ne
   paierait pas les ventes normales qu'elle déplace.
6. **Apprentissage** : λ et ε ne bougent que sur des paires d'offres tirées au sort (λ ± 0,05). λ ne change qu'après
   8 paires sur au moins 2 campagnes, et seulement si la médiane dépasse ±2 % avec au moins 2/3 des paires du même
   signe. Aujourd'hui, λ = 0,60 et ε = 0.

**Incohérences des slides que nous avons corrigées (les principales) :**

| Incohérence | Notre correction |
|---|---|
| Dans $G$, ni $T$ ni $k$ ne sont définis. Avec le sens évident, tout client dont le besoin couvre l'offre aurait $G < 0$ : **les meilleurs clients seraient classés derniers** | $k = q(1-R)$ : la part de l'offre qui se serait vendue au tarif normal |
| Analogues saisonniers (il y a 1 et 2 ans) avec une demi-vie de 8 semaines : ils pèsent 1 % et 0,01 % | Poids fixes (0,5 et 0,25), utilisés seulement si l'historique récent est maigre |
| « Ajustement du rythme » d'après le dernier achat : non défini | Prévision réduite si le client se tait plus de 2 fois son intervalle habituel. Mesuré : sans cette règle, l'erreur sur les kilos passe de 101,7 à 107,9 % |
| $c$ (« coûts variables ») : y mettre le prix d'achat le compterait deux fois | $c$ = coût de la vente seulement ; $A$ = valeur du don (0,6 × coût, à confirmer) |
| Première version : U(J) donné par 3 points seulement, et « 136 pièces sur 228 douzaines » | Formule logistique ; il s'agit en fait de pain de seigle, en pièces |

**Vérification** : l'exemple des slides se retrouve.

| Pain de seigle (lot_023121) | Slides | Notre code |
|---|---|---|
| Stock libre / ventes prévues / part à risque R | 228 / ≈ 92 / 59,7 % | 228 / 89,3 / 60,8 % |
| Prix proposé | 3,20 → 2,86 € (−10,625 %) | 3,20 → 2,86 € (−10,625 %) |

**Erreur de prévision** sur 8 semaines (WAPE, plus bas = meilleur), mesurée par `GET /api/campaigns/backtest` :

| Unité | Analogues | Semaine précédente | Slides (analogues / sem. préc.) |
|---|---|---|---|
| Douzaine | 71,8 % | 83,6 % | 64,0 / 79,5 % |
| Carton | 95,2 % | 113,0 % | 96,2 / 117,1 % |
| Pièce | 101,1 % | 113,3 % | 89,4 / 108,7 % |
| Kilogramme | 101,7 % | 88,4 % | 88,9 / 87,6 % |

La prévision bat la référence sauf en kilos, comme dans les slides, mais elle reste imprécise. Ce n'est pas encore
démontré : la demande réellement créée par une promotion, et la valeur fiscale du don. Ce sont précisément les données
que les campagnes enregistrent désormais.

---

## 4. L'agent vocal

| Choix | Pourquoi |
|---|---|
| **Jev décide, le code agit, le LLM parle** | Une requête Jev (0,12–0,25 s), lancée pendant que le client parle, donne : intention, produit, phrase finie, phrase compréhensible, s'adresse-t-il à l'agent. Les cas sûrs (oui au récapitulatif, refus, « ne m'appelez plus ») sont exécutés par du code. |
| **Sonnet 5.5 pour les quantités et la négociation, Haiku 5.5 pour les tours simples** | Sonnet seul réussissait 8 scénarios sur 10. Avec ce partage : 14/14, sans perdre en justesse. |
| **Deux répliques** | Haiku répond en ≈ 0,5 s à ce que le client vient de dire (« Quarante kilos de moules, je regarde ce que je peux faire sur le prix. »), puis Sonnet donne la réponse avec les chiffres. |
| **Garde-fous sur la validation** | Jev vérifie que c'est un vrai oui ; le code ne valide que si l'agent venait de demander « je valide ? » **et que le client l'a entendu**. |
| **Ce que le client a entendu** | S'il coupe l'agent, l'historique ne garde que ce qui a été joué : position de lecture dans le navigateur, confirmation par phrase au téléphone. L'agent redonne le prix ou la question manqués. |
| **Phrase incomprise** | L'agent demande au lieu de deviner, en reprenant ce qu'il a compris (« Vingt kilos de quoi, exactement ? »), et change de stratégie si ça se répète. |
| **Naturel au téléphone** | Les « euh » et « oui oui » ne coupent pas l'agent. Les apartés (« Kevin, sors les poubelles ! ») sont ignorés. L'écho est filtré. L'ouverture est tirée parmi 210 combinaisons. Les rappels se font à l'heure dite, en reprenant la conversation précédente. |
| **Voix** | `eleven_v4_turbo` : premier son en 232–265 ms, contre 157 ms pour flash, en plus expressif. Les prix et unités sont réécrits pour l'oral (« 15,73 € » → « 15 euros 73 »). |

---

## 5. Mesures

Le banc de scénarios (`python -m app.eval_outbound`) joue 14 appels scriptés, dont des cas difficiles : changement
d'avis, « oui » ambigu, transcription abîmée, phrase incompréhensible. Il vérifie ensuite l'ERP. « Premier son » =
temps entre la fin de la phrase du client et la phrase d'attente ou le premier mot de l'agent.

| Version | Scénarios réussis | 1er son, médiane | Pire tour |
|---|---|---|---|
| Sonnet seul (avant) | 8/10 | 932 ms | 2 374 ms (6,7 s sur une version antérieure) |
| **Version finale** | **14/14** | **301 ms** | 2 676 ms |

**Appel réel** (WebSocket, vraie voix) :
- première réplique en ≈ 0,75 s, réponse chiffrée en 2 à 3,5 s ;
- en mains libres, fin de phrase détectée 0,43 à 0,65 s après le dernier mot.

**Coût par appel** : 30 000 à 190 000 tokens d'entrée, presque tous lus en cache ; 400 à 700 tokens de sortie ; une
requête Jev par tour.

**Restent à mesurer** : le temps ressenti avec un vrai micro et du bruit de cuisine, et le taux d'acceptation réel des
offres.

---

## 6. Ce qui n'a pas marché

| Essai | Problème | Décision |
|---|---|---|
| Validation par le code dès que Jev entend « oui » | « Oui » répondu à « je vous ajoute des bulots ? » **validait la commande** | Valider seulement après « je valide ? » |
| Historique avec toute la réplique, même coupée | Coupé avant le prix, le client dit « oui » : **commande validée** | Ne garder que ce qui a été entendu |
| Phrases d'attente génériques | Sonnaient comme du remplissage | Réplique immédiate propre à la phrase du client |
| Exemple d'ouverture dans le prompt | Recopié mot pour mot, prix arrondis | Styles tirés au hasard, prix exacts |
| Notre propre règle d'apprentissage de ε | Contredisait les slides (λ et ε ne bougent qu'avec des comparaisons randomisées) | Protocole des paires |
| Rappels automatiques pendant nos tests | Notre vrai téléphone a sonné | Seuls les rappels dus depuis moins de 15 min sonnent seuls |
| `eleven_v4` | 0,9 s avant le premier son | `eleven_v4_turbo` |

---

## 7. Reproduire

```bash
make setup && make up && make demo-stock        # ERP + IA + web, avec des lots à date courte
# Page Déstockage → « Calculer depuis le stock » → « Créer et lancer les appels »
docker compose exec ai python -m app.eval_outbound   # banc de scénarios
curl localhost:4000/api/campaigns/backtest            # erreur de prévision
```

Réglages dans `.env` (voir `.env.example`) : modèles (`LLM_MODEL`, `LLM_FAST_MODEL`, `TTS_MODEL`, `TTS_VOICE`) et
comportement au téléphone (`VOICE_*`).
