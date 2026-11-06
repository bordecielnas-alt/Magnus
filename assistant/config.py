"""Chargement de la configuration.

Priorité : variables d'environnement > fichier utilisateur > assistant/defaults.yaml.
Le nom de l'assistant et son wake word ne sont jamais écrits dans le code :
ils viennent toujours de la configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

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


class Config(BaseModel):
    identite: Identite
    serveur: Serveur = Serveur()


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
