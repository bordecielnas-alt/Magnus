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
| `POST /api/chat` | `{"message", "historique", "role", "flux"}` ; avec `flux: true`, réponse NDJSON (`delta` puis `fin`) |

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
