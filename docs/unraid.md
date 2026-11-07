# Essai sur Unraid (Docker) et accès par Nginx Proxy Manager

Le développement se fait sur le PC. Cette page sert à vérifier que l'image tourne sur Unraid, avec la Quadro P2000.

## 1. L'image

À chaque push sur `main`, GitHub Actions lance les tests puis publie `ghcr.io/bordecielnas-alt/magnus:latest` (onglet *Actions* du dépôt, environ 10 min la première fois).

La première fois seulement : sur GitHub, *Packages > magnus > Package settings > Change visibility > Public*, sinon Unraid ne pourra pas télécharger l'image sans identifiants.

## 2. Le template Unraid

1. Copier `unraid/magnus.xml` dans `/boot/config/plugins/dockerMan/templates-user/my-magnus.xml` (partage *flash*, dossier `config/plugins/dockerMan/templates-user`).
2. *Docker > Add Container > Template* : choisir **magnus**.
3. Régler les champs :

| Champ | Valeur | Pourquoi |
| --- | --- | --- |
| Repository | `ghcr.io/bordecielnas-alt/magnus:latest` | l'image publiée par GitHub |
| Network Type | `bridge` | NPM le joint par l'IP d'Unraid et le port |
| Extra Parameters (vue avancée) | `--runtime=nvidia` | donne accès au GPU (plugin *Nvidia Driver* requis) |
| Interface web | `8080` (ou un port libre, ex. `8089`) | port côté Unraid |
| Config | `/mnt/user/appdata/magnus` | config.yaml, .env, mot de passe, connexion Claude |
| Modèles voix | `/mnt/user/appdata/magnus/modeles` | ~2 Go, téléchargés au premier démarrage |
| GPU (NVIDIA_VISIBLE_DEVICES) | UUID de la P2000 (`GPU-…`, page *Settings > Nvidia Driver*) | la même carte que Plex : c'est prévu (≈1 Go de VRAM en int8) |
| NVIDIA_DRIVER_CAPABILITIES | `compute,utility` | ne pas changer |
| Télécharger les modèles | `1` | Whisper large-v3-turbo, Kokoro, Piper (Hugging Face, GitHub) |
| Proxy de confiance (NPM) | IP de NPM vue par le conteneur (voir § 4) | pour connaître la vraie IP du client |
| Réseaux autorisés (avancé) | vide | = réseaux privés + Tailscale |
| TZ (avancé) | `Europe/Paris` | |

4. *Apply*. Le premier démarrage télécharge les modèles (journal du conteneur : « Modèles prêts. »).

## 3. Première configuration (dans `/mnt/user/appdata/magnus`)

- **`.env`** (créé vide au premier démarrage) : coller le jeton Home Assistant après `HA_JETON=` (le même que sur le PC, ou un nouveau).
- **`config.yaml`** à créer (adapter les adresses) :

```yaml
home_assistant:
  url: "https://home.bordeciel.net"
fournisseurs:
  ollama-local:
    url: "http://<IP-UNRAID>:11434/v1"   # le conteneur Ollama d'Unraid (sur la P2000)
```

- **Claude** (une fois) : console du conteneur (icône > *Console*), puis :

```bash
claude auth login
```

  Ouvrir le lien affiché, se connecter au compte Max, recopier le code. La connexion est gardée dans `/config/claude`.

- **Mot de passe** : depuis la console du conteneur on n'a pas de navigateur ; le plus simple est de le définir via l'API locale :

```bash
python -c "import json,getpass,urllib.request;r=urllib.request.Request('http://127.0.0.1:8080/api/securite/mot-de-passe',data=json.dumps({'nouveau':getpass.getpass('Mot de passe : ')}).encode(),headers={'Content-Type':'application/json'});print(urllib.request.urlopen(r).read().decode())"
```

  (Le premier mot de passe n'est accepté que depuis la machine elle-même, c'est-à-dire depuis l'intérieur du conteneur.)

Redémarrer le conteneur après avoir modifié `.env` ou `config.yaml`.

## 4. Nginx Proxy Manager

*Hosts > Proxy Hosts > Add Proxy Host* :

| Onglet | Champ | Valeur |
| --- | --- | --- |
| Details | Domain Names | `magnus.bordeciel.net` |
| Details | Scheme | `http` |
| Details | Forward Hostname / IP | IP d'Unraid (ou du PC pour tester le PC) |
| Details | Forward Port | `8080` (le port du template) |
| Details | Websockets Support | coché |
| Details | Block Common Exploits | coché |
| SSL | SSL Certificate | *Request a new SSL Certificate* (Let's Encrypt) |
| SSL | Force SSL, HTTP/2 Support | cochés |
| Access List | | une liste *LAN + VPN* (allow 192.168.0.0/16, 100.64.0.0/10, deny all) si NPM est joignable d'Internet |

Onglet *Advanced* (Custom Nginx Configuration), pour la vraie IP et des réponses vocales longues :

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_read_timeout 180s;
client_max_body_size 12m;
```

**Proxy de confiance** : l'assistant ne croit `X-Forwarded-For` que si la requête vient de l'adresse déclarée dans `ASSISTANT_PROXIES_DE_CONFIANCE`. Pour la trouver : dans les journaux de l'assistant, une requête passée par NPM sans proxy déclaré apparaît avec l'IP de NPM (ex. `172.17.0.5` si NPM est en bridge sur le même Unraid, ou l'IP du serveur NPM). Mettre cette IP en `/32` (ou `172.17.0.0/16` pour tout le bridge Docker).

**Sécurité** : l'assistant refuse tout client hors réseaux privés / Tailscale (403), et demande le mot de passe partout sauf depuis la machine elle-même. Ne pas l'exposer sur Internet tant que les comptes (phase 3) n'existent pas.

## 5. Vérifier

- `https://magnus.bordeciel.net` depuis la tablette : page de connexion, puis l'assistant.
- Carte *Voix* / `GET /api/voix` : `appareil: cuda`.
- `nvidia-smi` sur Unraid : un processus python de ~1 Go sur la P2000 pendant une transcription.
