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
