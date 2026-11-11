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

Les cartes sont construites par le serveur (`assistant/maison/cartes.py`) avec l'état lu dans Home Assistant : aucun modèle n'en écrit le contenu. Dans le flux NDJSON de `/api/chat`, elles arrivent dans une ligne `{"cartes": [...]}` avant `fin` (champ `cartes` sans flux). Un geste sur une carte appelle `POST /api/maison/carte` sans passer par un modèle : mêmes appareils autorisés, actions sensibles refusées, historique et « annule » comme à la voix (`POST /api/maison/carte/annuler`). Les appareils masqués (accès à distance, appareil partagé) n'ont pas de carte.

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
