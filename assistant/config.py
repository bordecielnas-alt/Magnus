"""Chargement de la configuration.

Priorité : variables d'environnement > fichier utilisateur > assistant/defaults.yaml.
Le nom de l'assistant et son wake word ne sont jamais écrits dans le code :
ils viennent toujours de la configuration.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

RACINE = Path(__file__).resolve().parent.parent
DEFAUTS = Path(__file__).resolve().parent / "defaults.yaml"

# Variable d'environnement -> (section, clé)
# Modèles (voix) : ASSISTANT_MODELES (conteneur : /modeles), sinon modeles/ du dépôt
MODELES = Path(os.environ.get("ASSISTANT_MODELES") or (Path(__file__).resolve().parent.parent / "modeles"))

ENV_LISTES: dict[str, tuple[str, str]] = {
    "ASSISTANT_RESEAUX_AUTORISES": ("securite", "reseaux_autorises"),
    "ASSISTANT_PROXIES_DE_CONFIANCE": ("securite", "proxies_de_confiance"),
}

ENV_MAP: dict[str, tuple[str, str]] = {
    "ASSISTANT_NOM": ("identite", "nom"),
    "ASSISTANT_WAKE_WORD": ("identite", "wake_word"),
    "ASSISTANT_LANGUE": ("identite", "langue"),
    "ASSISTANT_HOTE": ("serveur", "hote"),
    "ASSISTANT_PORT": ("serveur", "port"),
    "ASSISTANT_DOCKER_URL": ("docker", "url"),
}


class Identite(BaseModel):
    nom: str = Field(min_length=1, max_length=40)
    wake_word: str = ""
    langue: str = "fr"
    # Apparence et sons de la page : « orbe » (l'orbe vaporeuse, mot de réveil = le nom)
    # ou « hal » (l'œil rouge façon HAL 9000, mot de réveil = « hal »).
    theme: Literal["orbe", "hal"] = "orbe"

    @field_validator("nom")
    @classmethod
    def _nom_propre(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("le nom de l'assistant ne peut pas être vide")
        return v

    @property
    def wake_word_effectif(self) -> str:
        """Thème « hal » : « hal ». Sinon le wake word explicite, ou à défaut le nom en minuscules."""
        if self.theme == "hal":
            return "hal"
        return (self.wake_word or self.nom).strip().lower()

    @property
    def variantes_eveil(self) -> list[str]:
        """Le wake word et ses transcriptions fréquentes (Whisper écrit « Hall » ou « Halle » pour « Hal »)."""
        if self.theme == "hal":
            return ["hal", "hall", "halle", "hale"]
        return [self.wake_word_effectif]


class Serveur(BaseModel):
    hote: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)


class FournisseurConfig(BaseModel):
    type: Literal["openai", "claude_code"]
    modele: str = Field(min_length=1)
    timeout: float = Field(default=60, gt=0)
    # type openai
    url: str = ""
    cle_env: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    # type claude_code
    commande: str = "claude"
    effort: str = ""
    recherche_web: bool = True     # Claude peut chercher sur le web (actualité, météo…)

    @model_validator(mode="after")
    def _url_si_openai(self) -> "FournisseurConfig":
        if self.type == "openai" and not self.url:
            raise ValueError("un fournisseur openai doit avoir une url")
        return self

    def cle(self, env: Mapping[str, str] | None = None) -> str:
        """Lit la clé API dans la variable d'environnement indiquée (vide si aucune)."""
        if not self.cle_env:
            return ""
        return (os.environ if env is None else env).get(self.cle_env, "")


class HomeAssistantConfig(BaseModel):
    url: str = ""                         # vide = intégration désactivée
    jeton_env: str = "HA_JETON"           # NOM de la variable qui contient le jeton
    langue: str = "fr"
    agent: str = "conversation.home_assistant"   # agent Assist intégré, sans LLM
    timeout: float = Field(default=5, gt=0)
    pour_simples: bool = True             # les demandes simples passent d'abord par HA


class MaisonConfig(BaseModel):
    # Domaines HA que l'assistant peut lire et piloter. Serrures, alarme, etc. exclues
    # tant que les niveaux de risque (phase 3) n'existent pas.
    domaines_autorises: list[str] = Field(default_factory=lambda: [
        "light", "switch", "cover", "climate", "media_player", "fan"])
    rafraichir_s: float = Field(default=300, ge=10)   # relecture de l'inventaire
    extraction_par_modele: bool = True                # si les règles ne comprennent pas
    extracteur: str = "rapide"                        # rôle du modèle qui extrait la commande
    fichier_alias: str = ""                           # vide = <config>/alias_appris.json
    assist_en_secours: bool = True                    # agent Assist de HA si l'appareil n'est pas trouvé
    outils_pour_claude: bool = True                   # Claude reçoit les outils maison (serveur MCP local)
    # Actions sensibles : l'assistant peut lire leur état mais refuse de les piloter (pour l'instant).
    # Serrures et alarme sont en plus exclues des domaines autorisés.
    domaines_sensibles: list[str] = Field(default_factory=lambda: ["climate", "water_heater"])
    classes_sensibles: list[str] = Field(default_factory=lambda: ["gate", "garage", "door"])   # volets « portail »
    noms_sensibles: str = r"portail|serrure|verrou|alarme|chauffage|chaudi[eè]re|radiateur|thermostat"

    @field_validator("domaines_autorises")
    @classmethod
    def _pas_de_domaine_sensible(cls, v: list[str]) -> list[str]:
        interdits = {"lock", "alarm_control_panel", "camera", "script", "automation", "shell_command"}
        refuses = sorted(set(v) & interdits)
        if refuses:
            raise ValueError(f"domaines sensibles interdits avant la phase 3 : {refuses}")
        return v


class DockerConfig(BaseModel):
    """Diagnostic des conteneurs, en lecture seule, via un docker-socket-proxy (jamais le socket lui-même)."""
    url: str = ""                  # unix:///var/run/docker.sock (socket monté) ou http://proxy:2375 ; vide = désactivé
    # API officielle d'Unraid (7.2+) : adresse GraphQL ; la clé est le secret UNRAID_CLE (rôle lecture seule)
    unraid_url: str = ""           # ex. http://192.168.1.2/graphql ; prioritaire sur « url »

    def cle_unraid(self) -> str:
        return os.environ.get("UNRAID_CLE", "")

    def cle_unraid_actions(self) -> str:
        """2e clé, limitée à Docker, pour redémarrer un conteneur (après confirmation)."""
        return os.environ.get("UNRAID_CLE_ACTIONS", "")
    timeout: float = Field(default=8, gt=0)


class TelegramConfig(BaseModel):
    """Notifications sur le téléphone ; le jeton du bot est le secret TELEGRAM_JETON."""
    chat_id: str = ""              # trouvé avec « Trouver mon chat » (après /start au bot)
    docker: bool = True            # conteneur arrêté ou en mauvaise santé
    disques: bool = True           # disque en erreur, trop chaud, SMART, array arrêté
    agenda: bool = True            # rendez-vous imminent

    def jeton(self) -> str:
        return os.environ.get("TELEGRAM_JETON", "")


class AgendaConfig(BaseModel):
    """Calendrier CalDAV ; le mot de passe est le secret AGENDA_MOT_DE_PASSE (fichier .env)."""
    url: str = ""                  # URL du calendrier (collection), vide = désactivé
    utilisateur: str = ""
    rappel_min: int = Field(default=15, ge=0)        # rappel ajouté aux événements créés
    annonce_avant_min: int = Field(default=15, ge=0)  # le mode proactif annonce l'événement N min avant

    def mot_de_passe(self) -> str:
        return os.environ.get("AGENDA_MOT_DE_PASSE", "")


class RegleSurveillance(BaseModel):
    """« Préviens-moi si la porte du garage reste ouverte plus de 10 minutes. »"""
    entity_id: str
    etat: str                      # état qui déclenche (on, open, unavailable…)
    minutes: float = Field(default=10, ge=0)
    message: str = ""              # vide = « <nom> est <état> depuis <n> minutes. »
    priorite: Literal["info", "important", "urgent"] = "important"


class ProactifConfig(BaseModel):
    active: bool = False                 # coupé tant que Benjamin ne l'a pas demandé
    heures_calmes: str = "22:00-07:30"   # seules les annonces urgentes sont dites
    intervalle_s: float = Field(default=60, ge=10)
    briefing_heure: str = ""             # ex. « 07:30 » : météo + actualités chaque matin ; vide = jamais
    ville: str = ""                      # pour la météo du briefing
    surveiller: list[RegleSurveillance] = Field(default_factory=list)

    @field_validator("heures_calmes")
    @classmethod
    def _plage(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d\s*-\s*([01]?\d|2[0-3]):[0-5]\d", v):
            raise ValueError("heures calmes au format 22:00-07:30")
        debut, fin = v.replace(" ", "").split("-")
        return f"{debut.zfill(5)}-{fin.zfill(5)}"

    @field_validator("briefing_heure")
    @classmethod
    def _heure(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v):
            raise ValueError("heure du briefing au format 07:30")
        return v.zfill(5) if v else v


CATEGORIES_REVELATION = {"etat_sensible", "historique", "personnel"}


class RevelationConfig(BaseModel):
    """Ce que l'assistant ne révèle pas selon le contexte de la demande.

    Contextes : « distant » (hors du réseau de la maison), « partage » (appareil marqué partagé, ex. tablette
    de l'entrée), « enfant » (compte enfant). Catégories : « etat_sensible » (état du chauffage, du portail…),
    « historique » (actions passées, état de tous les appareils), « personnel » (préférences du profil).
    """
    masquer: dict[str, list[str]] = Field(default_factory=lambda: {
        "distant": ["etat_sensible"],
        "partage": ["etat_sensible", "historique", "personnel"],
        "enfant": ["etat_sensible", "historique"],
    })

    @field_validator("masquer")
    @classmethod
    def _categories_connues(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        inconnues = {c for liste in v.values() for c in liste} - CATEGORIES_REVELATION
        if set(v) - {"distant", "partage", "enfant"} or inconnues:
            raise ValueError(f"contexte ou catégorie inconnu : {sorted(set(v) - {'distant', 'partage', 'enfant'}) or sorted(inconnues)}")
        return v


class VoixConfig(BaseModel):
    active: bool = True
    # dossier du modèle faster-whisper ; vide = large-v3-turbo s'il est là, sinon small
    whisper: str = ""
    # auto = GPU NVIDIA s'il y en a un (RTX, Quadro P2000…), sinon processeur
    appareil: Literal["cpu", "cuda", "auto"] = "auto"
    # int8 : même comportement sur RTX 3080 et Quadro P2000 (Pascal), ~1 Go de VRAM pour turbo
    calcul: str = "int8"
    langue: str = "fr"
    # synthèse : auto = Kokoro s'il est là (plus naturel), sinon Piper
    moteur_voix: Literal["auto", "kokoro", "piper"] = "auto"
    kokoro: str = ""           # dossier kokoro-v1.0.onnx + voices-v1.0.bin ; vide = modeles/kokoro
    voix_kokoro: str = "ff_siwis"
    # Voix choisie dans le panneau de la page : « kokoro:ff_siwis », « piper:fr_FR-siwis-medium »…
    # Vide = moteur_voix / voix_kokoro / piper ci-dessus.
    voix: str = ""
    piper: str = ""            # voix Piper (.onnx) ; vide = modeles/piper/fr_FR-siwis-medium.onnx
    lenteur: float = Field(default=1.0, gt=0.3, lt=3)   # > 1 = parle plus lentement
    max_audio_mo: float = Field(default=10, gt=0)
    # Écoute continue : après le wake word, on peut parler sans le répéter pendant fenetre_s secondes
    # (prolongée à chaque phrase). toujours_a_l_ecoute = plus besoin du wake word du tout.
    fenetre_s: float = Field(default=10, ge=0, le=3600)
    toujours_a_l_ecoute: bool = False
    # Reconnaître qui parle (empreinte vocale) pour appliquer son profil ; jamais pour donner des droits
    identification: bool = True
    seuil_locuteur: float = Field(default=0.5, ge=0, le=1)
    locuteur: str = ""         # modèle ONNX WeSpeaker ; vide = modeles/locuteur/voxceleb_resnet34_LM.onnx

    def chemin_locuteur(self) -> Path:
        return Path(self.locuteur) if self.locuteur else MODELES / "locuteur" / "voxceleb_resnet34_LM.onnx"

    def chemin_whisper(self) -> str:
        if self.whisper:
            return self.whisper
        turbo = MODELES / "whisper-large-v3-turbo"
        return str(turbo if (turbo / "model.bin").is_file() else MODELES / "whisper-small")

    def chemin_kokoro(self) -> Path:
        return Path(self.kokoro) if self.kokoro else MODELES / "kokoro"

    def kokoro_present(self) -> bool:
        d = self.chemin_kokoro()
        return (d / "kokoro-v1.0.onnx").is_file() and (d / "voices-v1.0.bin").is_file()

    def moteur_effectif(self) -> str:
        if self.voix and ":" in self.voix:
            return self.voix.split(":", 1)[0]
        if self.moteur_voix == "auto":
            return "kokoro" if self.kokoro_present() else "piper"
        return self.moteur_voix

    def synthese_presente(self) -> bool:
        return self.kokoro_present() if self.moteur_effectif() == "kokoro" else self.piper_presente()

    def chemin_piper(self) -> str:
        if self.voix.startswith("piper:"):
            fichier = self.voix.split(":", 2)[1]
            return str(MODELES / "piper" / (fichier + ".onnx"))
        return self.piper or str(MODELES / "piper" / "fr_FR-siwis-medium.onnx")

    def voix_kokoro_effective(self) -> str:
        return self.voix.split(":", 1)[1] if self.voix.startswith("kokoro:") else self.voix_kokoro

    def voix_actuelle(self) -> str:
        if self.moteur_effectif() == "kokoro":
            return "kokoro:" + self.voix_kokoro_effective()
        if self.voix.startswith("piper:"):
            return self.voix
        return "piper:" + Path(self.chemin_piper()).stem

    def whisper_present(self) -> bool:
        return (Path(self.chemin_whisper()) / "model.bin").is_file()

    def piper_presente(self) -> bool:
        return Path(self.chemin_piper()).is_file()


class SecuriteConfig(BaseModel):
    """Accès en attendant les comptes (phase 3). Voir assistant/securite.py."""
    active: bool = True
    # Réseaux qui peuvent joindre l'assistant (en plus de la machine elle-même) : réseaux privés
    # habituels et plage Tailscale. Tout le reste reçoit 403, même avec le mot de passe.
    reseaux_autorises: list[str] = Field(default_factory=lambda: [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7", "fe80::/10"])
    # Proxys dont on croit X-Forwarded-For (Nginx Proxy Manager…). Vide = on ignore ces en-têtes.
    proxies_de_confiance: list[str] = Field(default_factory=list)
    machine_sans_mot_de_passe: bool = True     # sur la machine elle-même (127.0.0.1), pas de connexion
    totp_obligatoire: bool = True              # code TOTP à chaque nouvelle connexion (configuré au 1er accès)
    session_jours: float = Field(default=30, gt=0, le=365)
    essais_max: int = Field(default=5, ge=1)
    blocage_s: float = Field(default=300, gt=0)

    @field_validator("reseaux_autorises", "proxies_de_confiance")
    @classmethod
    def _reseaux_valides(cls, v: list[str]) -> list[str]:
        import ipaddress
        for r in v:
            ipaddress.ip_network(r, strict=False)   # lève ValueError si invalide
        return v


class RouteurConfig(BaseModel):
    role_simple: str = "rapide"
    role_complexe: str = "raisonnement"
    classeur: str = "rapide"          # rôle du modèle qui classe ; vide = règles seules
    delai_classeur_ms: int = Field(default=2500, ge=100)
    defaut: Literal["simple", "complexe"] = "complexe"
    longueur_complexe: int = Field(default=220, ge=1)
    regles_simples: list[str] = Field(default_factory=list)
    regles_complexes: list[str] = Field(default_factory=list)


class Config(BaseModel):
    identite: Identite
    serveur: Serveur = Serveur()
    consigne: str = "Tu es {nom}."
    fournisseurs: dict[str, FournisseurConfig] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    routeur: RouteurConfig = Field(default_factory=RouteurConfig)
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    maison: MaisonConfig = Field(default_factory=MaisonConfig)
    voix: VoixConfig = Field(default_factory=VoixConfig)
    securite: SecuriteConfig = Field(default_factory=SecuriteConfig)
    revelation: RevelationConfig = Field(default_factory=RevelationConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    proactif: ProactifConfig = Field(default_factory=ProactifConfig)
    agenda: AgendaConfig = Field(default_factory=AgendaConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    @model_validator(mode="after")
    def _roles_valides(self) -> "Config":
        inconnus = {r: f for r, f in self.roles.items() if f not in self.fournisseurs}
        if inconnus:
            raise ValueError(f"rôles vers des fournisseurs inconnus : {inconnus}")
        r = self.routeur
        manquants = [x for x in (r.role_simple, r.role_complexe, r.classeur) if x and x not in self.roles]
        if manquants:
            raise ValueError(f"routeur : rôles inconnus {manquants}")
        if self.maison.extracteur and self.maison.extracteur not in self.roles:
            raise ValueError(f"maison : rôle extracteur inconnu {self.maison.extracteur}")
        for motif in r.regles_simples + r.regles_complexes:
            try:
                re.compile(motif)
            except re.error as e:
                raise ValueError(f"routeur : règle invalide « {motif} » ({e})") from e
        return self

    @property
    def consigne_effective(self) -> str:
        return self.consigne.replace("{nom}", self.identite.nom)


def _lire_yaml(chemin: Path) -> dict[str, Any]:
    if not chemin.is_file():
        return {}
    with chemin.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{chemin} doit contenir un dictionnaire YAML")
    return data


def _fusion(base: dict[str, Any], dessus: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in dessus.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusion(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def chemin_config_utilisateur() -> Path:
    """ASSISTANT_CONFIG, sinon /config/config.yaml (conteneur), sinon config/config.yaml."""
    explicite = os.environ.get("ASSISTANT_CONFIG")
    if explicite:
        return Path(explicite)
    conteneur = Path("/config/config.yaml")
    if conteneur.is_file() or (os.name != "nt" and conteneur.parent.is_dir()):
        return conteneur
    return RACINE / "config" / "config.yaml"


def charger(env: dict[str, str] | None = None, fichier: Path | None = None) -> Config:
    env = os.environ if env is None else env
    data = _lire_yaml(DEFAUTS)
    data = _fusion(data, _lire_yaml(fichier or chemin_config_utilisateur()))
    for var, (section, cle) in ENV_MAP.items():
        if env.get(var):
            data.setdefault(section, {})[cle] = env[var]
    for var, (section, cle) in ENV_LISTES.items():   # listes séparées par des virgules
        if env.get(var):
            data.setdefault(section, {})[cle] = [x.strip() for x in env[var].split(",") if x.strip()]
    return Config.model_validate(data)


def charger_secrets(chemin: Path | None = None, env: dict[str, str] | None = None) -> Path | None:
    """Charge un fichier de secrets CLE=valeur dans l'environnement (sans écraser l'existant).

    Ordre : ASSISTANT_SECRETS, /config/.env (conteneur), puis .env à la racine du dépôt.
    Renvoie le fichier lu, ou None.
    """
    env = os.environ if env is None else env
    candidats = [chemin] if chemin else [
        Path(p) for p in (env.get("ASSISTANT_SECRETS"), "/config/.env") if p
    ] + [RACINE / ".env"]
    for c in candidats:
        if c and c.is_file():
            for ligne in c.read_text(encoding="utf-8-sig").splitlines():
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, valeur = ligne.split("=", 1)
                cle, valeur = cle.strip(), valeur.strip().strip('"').strip("'")
                if cle and valeur and cle not in env:
                    env[cle] = valeur
            return c
    return None


def chemin_alias(cfg: "Config") -> Path:
    if cfg.maison.fichier_alias:
        return Path(cfg.maison.fichier_alias)
    if os.name != "nt" and Path("/config").is_dir():
        return Path("/config/alias_appris.json")
    return RACINE / "config" / "alias_appris.json"


def enregistrer_reglages(section: str, valeurs: dict[str, Any], fichier: Path | None = None) -> Path:
    """Écrit `valeurs` dans la section `section` du fichier de config utilisateur (config.yaml),
    en gardant tout le reste. Renvoie le fichier écrit."""
    chemin = fichier or chemin_config_utilisateur()
    data = _lire_yaml(chemin)
    data.setdefault(section, {})
    if not isinstance(data[section], dict):
        data[section] = {}
    data[section].update(valeurs)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    texte = "# Config locale (non versionnée). Modifiée aussi depuis l'interface.\n" + yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(texte, encoding="utf-8")
    os.replace(tmp, chemin)
    return chemin


def dossier_etat(cfg: "Config") -> Path:
    """Où l'assistant garde son état (alias appris, sécurité) : /config dans le conteneur,
    config/ du dépôt sur le PC, ou ASSISTANT_ETAT."""
    if os.environ.get("ASSISTANT_ETAT"):
        return Path(os.environ["ASSISTANT_ETAT"])
    if os.name != "nt" and Path("/config").is_dir():
        return Path("/config")
    return RACINE / "config"
