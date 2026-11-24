# Guide téléphone : l'agent vous appelle pour de vrai (Twilio)

Ce guide explique comment faire sonner **votre téléphone** : l'agent vocal appelle, vous décrochez, et vous jouez le
client d'une campagne de déstockage. Pendant ce temps, la page **Voix** de l'ERP affiche la conversation en direct
(ce que vous dites, ce que répond l'agent, les outils qu'il utilise, les temps de réponse). Les commandes et les
résultats d'appel apparaissent dans l'ERP comme lors d'un appel depuis le navigateur.

## Comment ça marche, en une image

```
 ERP (page Voix)                 Service IA (port 4100)                    Twilio                 Votre téléphone
 « Sur mon téléphone »  ──POST /phone/call──▶  « appelle ce numéro »  ──▶  ça sonne  ──────────▶  vous décrochez
                                                                                                        │
 transcription en direct ◀── WS /voice/phone ──  PhoneConnection  ◀══ WS /phone/media (audio) ══▶  la voix
                                                 = la session vocale du kit, en mode « mains libres »
```

- **Twilio** est un opérateur téléphonique piloté par API : on lui demande « appelle ce numéro », et quand la personne
  décroche, il envoie le son de l'appel en direct à notre service par un WebSocket (« Media Streams »).
- Le son du téléphone est en **µ-law 8 kHz** (la qualité « téléphone »). Soniox (STT) l'accepte tel quel, et ElevenLabs
  (TTS) sait parler directement dans ce format : aucune conversion, donc pas de latence ajoutée.
- Tout le reste est **le code existant** : détection de fin de phrase, routage Jev, agent sortant, phrases d'attente,
  coupure de parole, enregistrement du résultat. Le fichier [apps/ai/app/services/phone.py](apps/ai/app/services/phone.py)
  ne fait que l'adaptation entre Twilio et la session vocale.
- L'agent **raccroche tout seul** quand l'appel est clos dans l'ERP (commande confirmée, refus…) et qu'il a fini de
  parler. Si vous raccrochez avant, le résultat est déduit et enregistré, comme dans le navigateur.

Pour qu'il puisse vous joindre, Twilio a besoin d'une **adresse publique** vers votre ordinateur : c'est le rôle du
**tunnel** (ngrok), qui relie une adresse `https://…` sur internet au port 4100 de votre machine.

## Étape 1 : créer le compte Twilio

1. Inscrivez-vous sur <https://www.twilio.com/try-twilio> (email, puis vérification de votre numéro de portable).
   Le numéro que vous vérifiez à l'inscription est celui que l'agent pourra appeler pendant l'essai.
2. Dans la console (<https://console.twilio.com>), notez dans **Account Info** :
   - **Account SID** (commence par `AC…`) ;
   - **Auth Token** (cliquez sur « show »). C'est un mot de passe : ne le collez nulle part ailleurs que dans `.env`.

> **Compte d'essai et démo.** Un compte d'essai n'appelle que des numéros vérifiés, et chaque appel commence par un
> message en anglais (« You have a trial account… press any key ») : il faut appuyer sur une touche avant que l'agent
> parle. C'est suffisant pour tester, mais **pour la démo devant le jury, passez en compte payant** (menu *Billing →
> Upgrade*, environ 20 $ de crédit) : le message disparaît.

## Étape 2 : obtenir un numéro Twilio

Console → **Phone Numbers → Manage → Buy a number**.

- **Le plus rapide : un numéro américain (+1)** avec la capacité *Voice*. Il est actif tout de suite (environ
  1 $ par mois). Votre téléphone affichera un appel venant des États-Unis : prévenez le jury.
- **Un numéro français (+33)** fait plus « vrai », mais Twilio exige un dossier réglementaire (pièce d'identité et
  adresse en France) dont la validation peut prendre plusieurs jours. À ne tenter que si vous avez le temps.

Puis autorisez l'appel vers la France : **Voice → Settings → Geo permissions**, cochez **France** (ou le pays de votre
téléphone) et enregistrez.

## Étape 3 : le tunnel (ngrok)

Dans le terminal WSL (Ubuntu) :

```bash
# 1. Installer ngrok (une fois)
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# 2. Créer un compte gratuit sur https://dashboard.ngrok.com, copier l'authtoken, puis :
ngrok config add-authtoken <votre-authtoken>

# 3. Dans le dashboard ngrok, menu "Domains" : réservez votre domaine gratuit (ex. ma-team.ngrok-free.app).
#    Il ne change jamais : pas besoin de modifier .env à chaque fois.
ngrok http --url=ma-team.ngrok-free.app 4100
```

Laissez cette fenêtre ouverte pendant les tests et la démo. Vérifiez dans un navigateur que
`https://ma-team.ngrok-free.app/phone` répond (un petit JSON).

> Le tunnel rend tout le service IA joignable depuis internet. Ouvrez-le pour tester et pour la démo, fermez-le
> (Ctrl+C) le reste du temps.

## Étape 4 : remplir `.env`

À la fin du `.env` à la racine du dépôt (voir `.env.example`) :

```bash
TWILIO_ACCOUNT_SID=AC...                  # étape 1
TWILIO_AUTH_TOKEN=...                     # étape 1
TWILIO_FROM_NUMBER=+1...                  # le numéro acheté à l'étape 2
TWILIO_TO_NUMBER=+336...                  # VOTRE portable : le seul numéro que l'agent appellera jamais
PUBLIC_URL=https://ma-team.ngrok-free.app # étape 3, sans / à la fin
TWILIO_MAX_CALL_S=300                     # Twilio coupe après 5 min quoi qu'il arrive
```

Les numéros s'écrivent au format international : `+33 6 12 34 56 78` devient `+33612345678`.
Il faut aussi `STT_PROVIDER=soniox` et `TTS_PROVIDER=elevenlabs` (ou `gradium`).

Puis `make restart` (Docker) ou relancez `make dev`. Vérification : <http://localhost:4100/phone> doit afficher
`"enabled": true`. Sinon, le champ `reason` dit ce qui manque.

## Étape 5 : premier appel

1. Ouvrez l'ERP → **Campagnes** → une campagne → un appel à passer. La page **Voix** s'ouvre avec la barre d'appel.
2. Dans la barre, choisissez **Voix : Téléphone** (ou **ERP** pour faire l'appel dans le navigateur, micro et
   haut-parleurs de l'ordinateur). Le choix est mémorisé dans ce navigateur et vaut aussi pour les appels suivants.
   Cliquez **Appeler**. La barre affiche « Ça sonne… », votre téléphone sonne.
3. Décrochez : l'agent se présente. Répondez comme un client (« Allô ? Oui, c'est moi… »).
4. L'écran montre la conversation en direct. À la fin, l'agent raccroche, ou vous raccrochez, ou vous cliquez
   **Raccrocher** dans la barre.

Si vous ne décrochez pas (25 s de sonnerie), l'appel est enregistré « sans réponse ».

**Vérifier l'audio sans téléphone** (aucun appel, l'ERP n'est pas modifié) :

```bash
docker exec -w /app/apps/ai voice-erp-kit-ai-1 python -m app.eval_phone_audio
```

La voix de l'agent est synthétisée au format téléphone, puis renvoyée au STT : il doit retrouver la même phrase.

## Si ça ne marche pas

| Symptôme | Cause probable |
|---|---|
| « Téléphone indisponible » dans la barre, bouton Appeler grisé | La barre affiche ce qui manque (même texte que `reason` dans `GET /phone`). Pensez à `make restart` après avoir modifié `.env`. |
| Toast « Twilio 400 / 401 / 403 » | SID ou token faux, numéro mal écrit, pays non autorisé (Geo permissions), ou compte d'essai qui appelle un numéro non vérifié. |
| Ça sonne, vous décrochez, puis silence et raccrochage | Twilio n'atteint pas le service : le tunnel est-il ouvert ? `PUBLIC_URL` correspond-il exactement au domaine ngrok ? Regardez `make logs` (lignes `[phone]`) et la page `http://127.0.0.1:4040` de ngrok. |
| Message en anglais au décroché | Compte d'essai : appuyez sur une touche, ou passez en compte payant. |
| L'agent se coupe tout seul | Le haut-parleur est trop fort : sa propre voix revient dans le micro. Baissez le volume. |

## Pour la démo devant le jury

- **Avant** : `make reset` (données propres), tunnel ouvert, un appel de répétition complet.
- **Le son** : téléphone en haut-parleur, volume moyen, près du micro de la salle si possible. Les téléphones annulent
  l'écho, mais un volume trop fort fait croire à l'agent que vous lui coupez la parole.
- **L'écran** : page Voix en grand. Le jury voit la transcription, les outils (`accept_offer`, `confirm_call_order`…) et
  les temps de réponse, puis la commande dans l'ERP.
- **Le scénario** : faites un client crédible. Hésitez, demandez le prix, négociez une quantité, puis acceptez. Montrez
  aussi qu'on peut couper la parole à l'agent.
- **Le plan B** : si le réseau ou Twilio flanche, passez le sélecteur sur **Voix : ERP** : le même appel se fait dans
  le navigateur.
- **La latence** : le téléphone ajoute environ 100 à 200 ms dans chaque sens par rapport au navigateur. Les temps
  affichés à l'écran sont mesurés côté serveur.

## Pour aller plus loin (code)

| Envie | Où |
|---|---|
| Changer quand l'agent raccroche | `PhoneConnection.check_finished` et `HANGUP_GRACE_S` dans `services/phone.py` |
| Laisser la personne parler en premier (« Allô ? ») | `PhoneConnection.on_start` : attendre la première phrase avant `dial()` |
| Appeler plusieurs numéros de l'équipe | `TWILIO_TO_NUMBER` est volontairement unique : n'appelez jamais les numéros (fictifs) de l'ERP |
| Détecter un répondeur | paramètre `MachineDetection` de l'API Twilio, dans `place_call` |
