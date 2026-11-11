# Assistant personnel homelab

Orchestrateur de l'assistant : il reçoit les demandes, vérifie les droits, choisit un modèle et appelle les outils (Home Assistant, Docker, agenda…). Ce dépôt contient le code et le déploiement uniquement.

Le nom de l'assistant et son wake word ne sont jamais écrits dans le code : ils viennent de la configuration (valeur par défaut dans `assistant/defaults.yaml`).

## Lancer en local (Windows, sans Docker)

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m assistant
```

Puis ouvrir http://127.0.0.1:8080/ (la page affiche le nom) et http://127.0.0.1:8080/api/info.

Changer le nom sans toucher au code :

```powershell
$env:ASSISTANT_NOM = "Akatosh"; .venv\Scripts\python -m assistant
```

ou copier `config/config.example.yaml` en `config/config.yaml` et l'éditer.

Priorité : variables d'environnement > `config.yaml` > `defaults.yaml`.

| Variable | Rôle |
| --- | --- |
| `ASSISTANT_NOM` | Nom de l'assistant |
| `ASSISTANT_WAKE_WORD` | Wake word (vide = suit le nom) |
| `ASSISTANT_LANGUE` | Langue (`fr`) |
| `ASSISTANT_PORT` | Port d'écoute (8080) |
| `ASSISTANT_CONFIG` | Chemin d'un `config.yaml` |

## Modèles (fournisseurs)

Chaque modèle est un « fournisseur » interchangeable, déclaré dans la config (`fournisseurs:`) et associé à un rôle (`roles:` : `rapide`, `raisonnement`, `secours`). Le type `openai` couvre toute API compatible OpenAI : Ollama, LM Studio, vLLM ou une clé API. Si le fournisseur d'un rôle ne répond pas, l'orchestrateur bascule sur celui du rôle `secours`.

Par défaut : `rapide` et `secours` sur Ollama (`127.0.0.1:11434`, `qwen2.5:3b`), `raisonnement` sur Claude. Exemples dans `config/config.example.yaml`.

### Claude via l'abonnement (type `claude_code`)

L'orchestrateur appelle le binaire officiel Claude Code en mode headless (`claude -p`), non modifié, avec le compte connecté par `claude auth login`. Il le lance **sans aucun outil** (`--tools ""`, aucun serveur MCP, réglages utilisateur ignorés), depuis un dossier temporaire neutre : Claude répond, il n'agit sur rien. `ANTHROPIC_API_KEY` n'est jamais transmise, pour que ce soit bien l'abonnement qui serve. Si Claude ne répond pas (pas connecté, quota, délai), la demande repasse sur le fournisseur du rôle `secours`.

Installation sur le PC : `npm install -g @anthropic-ai/claude-code`, puis `claude auth login` (ou le bouton « Se connecter » du tableau de suivi). Dans le conteneur Unraid, ce sera prévu au déploiement (binaire dans l'image, identifiants dans un volume).

| Point d'accès | Rôle |
| --- | --- |
| `GET /api/fournisseurs` | Fournisseurs, rôles et disponibilité |
| `POST /api/route` | Décision du routeur seule (sans appeler le modèle) |
| `POST /api/chat` | `{"message", "historique", "role", "flux"}` (`role` = `auto` par défaut) ; avec `flux: true`, réponse NDJSON (`delta` puis `fin`) |

## Routeur

Avec `role: auto` (par défaut), le routeur choisit le rôle `rapide` (simple) ou `raisonnement` (complexe) :

1. **Règles** de `routeur.regles_simples` / `regles_complexes` (expressions régulières), en moins d'une milliseconde ; un message très long part en complexe.
2. Sinon, le modèle du rôle `classeur` (qwen2.5:3b par défaut) répond `SIMPLE` ou `COMPLEXE`, en quelques dizaines de ms une fois chargé.
3. Sinon (classeur absent, trop lent, indécis) : `routeur.defaut` (complexe).

La page affiche sous chaque réponse la catégorie, la raison et le temps de décision.

Évaluation sur les vrais modèles : `python -m assistant.eval_routeur` (ou le bouton du tableau de suivi). Les phrases sont dans `assistant/donnees/phrases_routeur.yaml` : ajoutes-y celles qui sont mal routées.

## Maison (Home Assistant)

L'assistant trouve lui-même l'appareil visé, sans qu'il faille l'exposer à Assist :

1. **Inventaire** : au démarrage puis toutes les 5 minutes, il lit dans HA (WebSocket) les entités, leurs pièces et leurs alias. Seuls les domaines de `maison.domaines_autorises` sont gardés (lumières, prises, volets, chauffage, médias, ventilateurs) ; serrures, alarme, caméras, scripts et automatisations sont refusés par la config jusqu'aux niveaux de risque (phase 3).
2. **Commande** : des règles en français reconnaissent « allume / éteins / ouvre / ferme / règle … à 50 % / … est-elle allumée ? » ; sinon le modèle `maison.extracteur` (qwen2.5:3b) extrait la commande en JSON.
3. **Correspondance** : pièce (et ses alias), type d'appareil (« lumière », « volet », « chauffage »…), nom approché, sans accents ni pluriels.
4. **Ambiguïté** : s'il y a plusieurs candidats, il demande « Lequel ? » ; la réponse (numéro, nom, « tous ») est retenue comme **alias appris** dans `config/alias_appris.json` (lisible, modifiable, jamais versionné).
5. **Action** : appel de service via l'API REST de HA. Si l'appareil reste introuvable, l'agent Assist de HA tente sa chance, puis le modèle rapide répond en le disant.

### Claude avec les outils maison

Pour une demande ouverte sur la maison (« il fait sombre au salon », « prépare une ambiance cinéma », « qu'est-ce qui est allumé ? ») ou une demande simple que les règles n'ont pas comprise, Claude reçoit quatre outils : `lister_appareils`, `lire_etat`, `agir`, `annuler`. Ils sont servis par `assistant/mcp_maison.py`, un petit serveur MCP (stdio) lancé par le binaire `claude` pour la demande, qui relaie vers une API interne de l'orchestrateur (`/interne/maison/…`, 127.0.0.1, jeton tiré à chaque démarrage). Les mêmes règles s'appliquent : seuls les appareils de l'inventaire autorisé, état mémorisé pour « annule » (toutes les actions d'une même demande). Les outils intégrés de Claude (fichiers, commandes, web) restent désactivés. Compter 10 à 15 s par demande ; si Claude échoue, le secours ne peut pas prétendre avoir agi (réponse remplacée). Désactivable : `maison.outils_pour_claude: false`.

Avec un jeton d'utilisateur HA non administrateur, les registres (pièces, alias) peuvent être refusés : la reconnaissance se fait alors sur les noms seuls.

| Point d'accès | Rôle |
| --- | --- |
| `GET /api/maison` | Résumé de l'inventaire et alias appris |
| `POST /api/maison/rafraichir` | Relire l'inventaire tout de suite |
| `DELETE /api/maison/alias/{phrase}` | Oublier un alias appris |

### Cartes dans le chat

Quand une demande touche ou lit des appareils (règles, Assist ou outils de Claude), la réponse est suivie d'une **carte** : interrupteur, intensité, blanc chaud/froid et couleurs pour une lumière (selon ce que l'ampoule sait faire), ouverture pour un volet, interrupteur pour une prise. Plusieurs appareils : une carte « groupe », une ligne chacun, qu'on déplie pour les réglages.

Les cartes arrivent **repliées** (nom et état) : un toucher sur l'en-tête les déplie. Sur téléphone, les réglages sont **verrouillés** pour ne pas gêner le défilement : un **appui long** sur la carte les active (bordure en couleur, « Réglages actifs »), puis ils se reverrouillent après 20 s sans y toucher. Sur ordinateur, pas de verrou.

**Couleurs** : douze teintes du cercle chromatique en pastilles, et une **roue** (dernière pastille) pour toutes les autres, teinte et saturation (vers le centre, plus pâle). Dans une carte « groupe », la section **Régler plusieurs lampes** règle d'un coup la couleur, le blanc ou l'intensité des lampes choisies (toutes par défaut, un toucher sur un nom l'enlève) ; seules celles qui le peuvent sont réglées, les autres sont citées. Chaque lampe se règle aussi seule en dépliant sa ligne. Un « Annuler » remet tout le lot.

**Vie d'une carte** (appareils comme rapports) : toute carte se replie et se déplie d'un toucher sur l'en-tête. Dépliée, elle se **replie toute seule après 1 minute** sans qu'on y touche, puis **disparaît 1 minute plus tard** (si la bulle ne montrait que la carte, la phrase redevient visible). **Dès qu'on enchaîne** (nouveau message, d'ici ou d'un autre appareil), les cartes précédentes se replient et disparaissent une minute après. Toucher, régler ou survoler une carte à la souris relance sa minute. Une conversation rouverte applique ces délais à l'heure de chaque réponse.

Une réponse **en cours d'utilisation ne disparaît pas** : touchée il y a moins de 30 s, survolée à la souris, ou avec une carte dépliée touchée il y a moins de 3 min, elle n'est ni effacée (vue orbe) ni masquée par l'échange suivant. Ensuite elle s'efface comme les autres.

Les cartes sont construites par le serveur (`assistant/maison/cartes.py`) avec l'état lu dans Home Assistant : aucun modèle n'en écrit le contenu. Dans le flux NDJSON de `/api/chat`, elles arrivent dans une ligne `{"cartes": [...], "masquer_texte": …}` avant le texte (champs `cartes` et `masquer_texte` sans flux). Un geste sur une carte appelle `POST /api/maison/carte` sans passer par un modèle : mêmes appareils autorisés, actions sensibles refusées, historique et « annule » comme à la voix (`POST /api/maison/carte/annuler`). Le réglage de plusieurs lampes passe par `POST /api/maison/carte/groupe` (`entity_ids`, plus un réglage : `teinte`/`saturation`, `couleur`, `kelvin`, `intensite`…). Les appareils masqués (accès à distance, appareil partagé) n'ont pas de carte.

### Règles « si… alors… »

On les dicte en phrase normale : « si la température du salon passe sous 19, préviens-moi », « quand tout le monde part, éteins les lumières », « allume l'entrée dès que Léa arrive, entre 18 h et 23 h », « préviens-moi si la porte du garage reste ouverte plus de 10 minutes ». Claude cherche le capteur (`lister_capteurs`) et les appareils, puis appelle `proposer_regle` ; l'assistant relit alors la règle **construite par le serveur à partir de ce qui sera vraiment enregistré** (« Je crée cette règle : Quand Température salon passe sous 19 °C, j'allume Plafonnier. D'accord ? ») et ne l'enregistre qu'après un « oui » de la même personne (5 minutes pour répondre).

- Conditions : présence (quelqu'un arrive, tout le monde est parti, une personne `person.*` arrive ou part), seuil d'un capteur (sous / au-dessus d'une valeur ; `current_temperature` pour un thermostat), état d'un appareil (devient X, ou reste X plus de N minutes), avec un créneau horaire facultatif.
- Actions : les mêmes que les routines (allumer, éteindre, ouvrir, fermer, régler), avec les mêmes garde-fous (appareils autorisés, actions sensibles refusées dès la proposition), et/ou une annonce dite par l'assistant (heures calmes et absence respectées, Telegram si configuré).
- Déclenchement sur front : quand la condition **devient** vraie, pas tant qu'elle l'est ; marge de 0,5 sur les seuils pour ne pas répéter quand la valeur oscille ; rien ne se déclenche au démarrage ou à la reprise sur un état déjà vrai. États lus dans Home Assistant toutes les 15 s.
- Pause : « mets la règle du garage en pause », « reprends-la », ou dans Réglages > Règles (mettre en pause, reprendre, supprimer, dernier déclenchement). Gardées dans `<état>/regles.json`, déclenchements notés au journal.

| Point d'accès | Rôle |
| --- | --- |
| `GET /api/regles` | Règles (texte, active, dernier déclenchement) |
| `POST /api/regles/{id}` | `{"active": false}` met en pause, `true` reprend |
| `DELETE /api/regles/{id}` | Supprimer une règle |

## Un seul assistant par compte (fil partagé)

La conversation n'appartient plus à une page mais au compte : tous les appareils connectés au même compte parlent au même assistant.

- **En direct** : une question posée sur le téléphone s'affiche sur le PC (« depuis iPhone »), puis la réponse arrive mot à mot sur les deux. Seul l'appareil qui a posé la question lit la réponse à voix haute.
- **Reprise** : un appareil qui s'ouvre retrouve les derniers échanges (affichés en vue Conversation, discrets en mode orbe). Le modèle reçoit le contexte du compte (échanges des dernières 24 h, au plus 20) et les dernières actions sur la maison (3 h), d'où qu'elles viennent.
- **Suite d'une question** : « oui » à une proposition de souvenir, la réponse à « Lequel ? » ou la confirmation d'un redémarrage peuvent venir d'un autre appareil du compte.
- **Nouvelle conversation** (menu ⋯) : efface le fil sur tous les appareils ; les souvenirs validés restent.
- Ni sur un appareil partagé, ni pour un compte enfant, ni à distance si l'historique y est masqué : la page garde alors une conversation à elle, rien n'est relu.

Stockage : `fil.db` (SQLite) dans le dossier d'état, 1 000 échanges au plus par compte.

| Point d'accès | Rôle |
| --- | --- |
| `GET /api/fil` | Identifiant de conversation du compte et derniers échanges |
| `GET /api/fil/flux` | Server-Sent Events : questions, réponses mot à mot, effacement (reprise après coupure par `Last-Event-ID`) |
| `DELETE /api/fil` | Nouvelle conversation |

## Un profil par personne

Chaque compte a son profil (⚙ Mon compte) : ton, pièce habituelle, préférences. Quand la voix d'une personne est enregistrée, l'assistant la reconnaît (`assistant/locuteur.py`) et applique **son** profil, même sur l'appareil de quelqu'un d'autre :

- **Préférences** : son ton, sa pièce par défaut, ses consignes et ses souvenirs privés.
- **Rappels personnels** : « rappelle-moi de ranger ma chambre » appartient à la personne reconnue (sinon au compte connecté). Chacun ne voit et ne supprime que les siens ; à l'heure dite, l'annonce dit pour qui (« Rappel pour Léa : … ») dès qu'il y a plusieurs comptes.
- **Droits** : un administrateur coche, pour chaque compte (⚙ Comptes), ce que la personne ne pilote pas : lumières, prises, volets, chauffage, télé et musique, ventilateurs. Un compte enfant ne touche pas au chauffage tant que rien d'autre n'a été choisi. Le refus vaut partout : commandes, outils de Claude, routines, cartes, « annule », « plus fort ». Pour une personne aux droits limités, l'agent Assist de Home Assistant n'est jamais appelé en secours (il ne connaît pas ces droits).

La voix ne peut que **retirer** des droits, jamais en donner : les interdits du compte connecté et ceux de la voix reconnue s'additionnent. Si Léa (enfant) parle sur la tablette d'un parent et qu'elle est reconnue, ses limites s'appliquent ; si sa voix n'est pas reconnue, ce sont celles du compte connecté. Pour une limite garantie, l'enfant utilise son propre compte.

Rappel : le chauffage, le portail ou le garage restent refusés à tout le monde par `maison.domaines_sensibles` et `maison.noms_sensibles`. Si tu ouvres le chauffage aux adultes en le retirant de cette liste, la limite par personne garde l'enfant à l'écart.

## Surveillance du serveur Unraid

Avec l'API Unraid (`docker.unraid_url` et la clé `UNRAID_CLE` en lecture seule, dans Réglages) et le mode proactif actif, l'assistant vérifie chaque minute l'array et les disques, et prévient de lui-même (annonce sur la page, et Telegram si coché) :

- **Disques** : état anormal, erreurs de lecture/écriture, SMART pas OK, température au-delà des seuils d'Unraid (chaud / critique), disque plein à `docker.plein_pct` % (95 par défaut).
- **Espace** : l'array entier a moins de `docker.libre_min_pct` % libre (10 par défaut).
- **Parité** : erreurs au dernier contrôle, ou dernier contrôle plus vieux que `docker.parite_max_jours` jours (45 par défaut, pas pendant un contrôle en cours).
- **Mises à jour d'images Docker** (toutes les 6 h, `docker.mises_a_jour`) : l'état que Unraid calcule lui-même (page Docker). Une annonce par conteneur, avec deux boutons, réservés aux administrateurs :
  - **Mettre à jour** : comme « update » dans Unraid (nouvelle image, conteneur recréé). Demande la 2e clé `UNRAID_CLE_ACTIONS` (droits Docker) ; sans elle, l'annonce le dit et rien n'est fait. Chaque choix est noté dans le journal d'audit.
  - **Ignorer** : plus d'annonce pour ce conteneur tant qu'il n'est pas à jour, avec un rappel au bout de 30 jours.

Un seuil à 0 coupe l'alerte correspondante. Telegram ne reçoit les mises à jour que si la case « mises à jour » est cochée (décochée par défaut, pour ne pas être bavard). L'outil `etat_serveur` de Claude renvoie aussi la liste des mises à jour en attente.

## Voix (locale)

- **Transcription** : faster-whisper « small » en français (`POST /api/voix/transcrire`, corps = l'audio enregistré par le navigateur, webm/opus ou wav). Les noms de l'assistant, des appareils et des pièces sont donnés à Whisper comme indices.
- **Synthèse** : Piper, voix `fr_FR-siwis-medium` (`POST /api/voix/parler` -> WAV).
- Modèles dans `modeles/` (jamais versionnés) : `modeles/whisper-small/` et `modeles/piper/fr_FR-siwis-medium.onnx(.json)`. Chemins, appareil (`cpu`, `cuda`) et vitesse dans `voix:` de la config.
- Les modèles se chargent en arrière-plan au démarrage ; `GET /api/voix` donne leur état.
- Sur la page : 🎤 (clic pour parler, clic pour envoyer : la réponse est lue), 🔈 pour lire aussi les réponses écrites. Le micro marche sur `127.0.0.1` sans HTTPS ; une tablette demandera HTTPS.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

`tests/test_nom_pas_en_dur.py` échoue si le nom par défaut apparaît dans le code.

## Accès et sécurité

En attendant les comptes (phase 3) :
- seuls les réseaux privés et Tailscale peuvent joindre l'assistant (`securite.reseaux_autorises`), les autres reçoivent 403 ;
- hors de la machine elle-même, il faut se connecter (page `/connexion`) ; l'appareil garde une session signée 30 jours ;
- le premier mot de passe se choisit depuis la machine (ouvrir `http://127.0.0.1:8080/connexion`) ; il est stocké haché (scrypt) dans `config/securite.json` (jamais versionné) ; le changer ferme toutes les sessions ;
- 5 essais ratés bloquent l'adresse 5 minutes ;
- derrière Nginx Proxy Manager, déclarer son adresse dans `securite.proxies_de_confiance` (ou `ASSISTANT_PROXIES_DE_CONFIANCE`) pour que la vraie IP du client soit utilisée ;
- l'API interne (`/interne/…`, outils de Claude) n'est joignable que depuis la machine, jamais via le proxy.

## Déployer sur Unraid

Image publiée par GitHub Actions (`ghcr.io/bordecielnas-alt/magnus`), template `unraid/magnus.xml`, réglages Nginx Proxy Manager : voir **`docs/unraid.md`**.

## Rapports à la demande (cartes de synthèse)

« Fais-moi un rapport du CAC 40 », « rapport météo à Lyon », « fais le point sur Docker » : l'assistant répond par une phrase courte et une carte de synthèse sous la réponse (`assistant/rapports.py`, dessinée par `static/rapports.js`). Le mot « rapport », « synthèse », « bilan » ou « fais le point » déclenche la carte ; le sujet choisit le modèle :

| Carte | Données | Modèle |
| --- | --- | --- |
| Météo | Open-Meteo (sans clé) : maintenant, prochaines heures, 5 jours, icônes dessinées | aucun |
| Bourse | Yahoo Finance : cours, variations, courbe du mois (CAC 40, SBF 120, Euro Stoxx, DAX, FTSE, Nasdaq, Dow Jones, S&P 500, Nikkei, Bitcoin, Ethereum) | Claude (web) pour l'analyse du jour |
| Agenda | CalDAV : 7 prochains jours | aucun |
| Docker | conteneurs en marche, arrêtés, en souci ; serveur Unraid (administrateurs) | aucun |
| Actualités | titres du jour, ou sur un sujet | Claude (web) |
| Libre | tout autre sujet : chiffres clés, points, sources | Claude (web) |

La ville par défaut de la météo est `proactif.ville`. Si Claude ne répond pas, le modèle de secours (sans web) n'est pas utilisé pour une carte : rien d'inventé. `GET /api/rapports` liste les cartes, `POST /api/rapport` (`{"type", "sujet"}`) en refait une (bouton « Actualiser »).

### Carte ou texte selon le contexte

L'assistant choisit seul entre une carte et du texte (`assistant/presentation.py`). **Quand il y a une carte, la bulle ne montre que la carte** et la phrase est seulement dite à voix haute (elle reste écrite si elle pose une question ou si elle est longue, par exemple une règle à confirmer). Si la carte échoue, il revient au texte. Les règles, de la plus forte à la plus faible :

1. **Tu demandes à voir** (« montre-moi », « affiche », « fais voir », « je veux voir », « en carte ») : carte obligatoire si le sujet en a une (météo, bourse, agenda, Docker, actus, appareils). Sujet sans carte toute faite (« montre-moi un point sur la Fed ») : carte libre ; un appareil : sa carte ; sinon Claude dit qu'il n'a pas de carte pour ça.
2. **Question sur un sujet à carte rapide**, faite sans modèle (« quel temps demain ? », « où en est le CAC ? », « j'ai quoi cette semaine ? », « docker ça va ? ») : phrase et carte. La phrase répond au jour demandé (« Demain à Lyon : averses, 6 à 11 °C… »). Question fermée (« il pleut demain ? ») : la carte arrive repliée, un toucher sur l'en-tête la déplie. La bourse se fait ici sans l'analyse de Claude, pour ne pas attendre.
3. **Explication** (« dis-moi », « explique », « pourquoi », « raconte ») : texte seul, sans carte.
4. **Suite de conversation** : dans les 10 min après une carte, « et à Lyon ? », « et demain ? », « et le DAX ? » refont une carte du même genre.
5. **Claude décide pour le reste** : il peut commencer sa réponse par un marqueur (`[carte:meteo:Lyon]`, `[carte:bourse:DAX]`, `[carte:agenda]`, `[carte:docker]`) que le serveur retire et remplace par la vraie carte, lue après sa phrase. « Je sors à vélo cet après-midi, ça passe ? » donne ainsi la carte météo. Le modèle n'écrit jamais les données de la carte.

Une demande de règle, de routine ou de rappel (« quand il pleut, ferme les volets ») n'est jamais prise pour une question météo. Dans le flux de `/api/chat`, la ligne `{"cartes": [...], "masquer_texte": true}` arrive **avant** le texte.
