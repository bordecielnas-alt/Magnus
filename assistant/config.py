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
ENV_MAP: dict[str, tuple[str, str]] = {
    "ASSISTANT_NOM": ("identite", "nom"),
    "ASSISTANT_WAKE_WORD": ("identite", "wake_word"),
    "ASSISTANT_LANGUE": ("identite", "langue"),
    "ASSISTANT_HOTE": ("serveur", "hote"),
    "ASSISTANT_PORT": ("serveur", "port"),
}


class Identite(BaseModel):
    nom: str = Field(min_length=1, max_length=40)
    wake_word: str = ""
    langue: str = "fr"

    @field_validator("nom")
    @classmethod
    def _nom_propre(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("le nom de l'assistant ne peut pas être vide")
        return v

    @property
    def wake_word_effectif(self) -> str:
        """Le wake word explicite, sinon le nom en minuscules."""
        return (self.wake_word or self.nom).strip().lower()


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

    @model_validator(mode="after")
    def _roles_valides(self) -> "Config":
        inconnus = {r: f for r, f in self.roles.items() if f not in self.fournisseurs}
        if inconnus:
            raise ValueError(f"rôles vers des fournisseurs inconnus : {inconnus}")
        r = self.routeur
        manquants = [x for x in (r.role_simple, r.role_complexe, r.classeur) if x and x not in self.roles]
        if manquants:
            raise ValueError(f"routeur : rôles inconnus {manquants}")
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
    if conteneur.is_file():
        return conteneur
    return RACINE / "config" / "config.yaml"


def charger(env: dict[str, str] | None = None, fichier: Path | None = None) -> Config:
    env = os.environ if env is None else env
    data = _lire_yaml(DEFAUTS)
    data = _fusion(data, _lire_yaml(fichier or chemin_config_utilisateur()))
    for var, (section, cle) in ENV_MAP.items():
        if env.get(var):
            data.setdefault(section, {})[cle] = env[var]
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
