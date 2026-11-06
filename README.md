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

## Tests

```powershell
.venv\Scripts\python -m pytest
```

`tests/test_nom_pas_en_dur.py` échoue si le nom par défaut apparaît dans le code.

## Déployer sur Unraid

```bash
ASSISTANT_CONFIG_DIR=/mnt/user/appdata/assistant/config docker compose up -d --build
```

La config utilisateur va dans `config.yaml` de ce dossier.
