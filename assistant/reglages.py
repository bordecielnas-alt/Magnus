"""Réglages modifiables depuis la page de configuration.

Seuls les champs listés ici peuvent être changés depuis l'interface. Chaque changement est
validé avec la config complète avant d'être écrit dans config.yaml ; les secrets (jeton HA)
vont dans le fichier de secrets et ne sont jamais renvoyés à la page.
La plupart des changements s'appliquent au redémarrage de l'assistant.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .config import RACINE, Config, _fusion, _lire_yaml, chemin_config_utilisateur

# section -> champs modifiables (pour « fournisseurs », par fournisseur)
MODIFIABLES: dict[str, set[str]] = {
    "identite": {"nom", "wake_word", "theme"},
    "home_assistant": {"url"},
    "roles": {"rapide", "raisonnement", "secours"},
    "voix": {"voix", "fenetre_s", "toujours_a_l_ecoute", "lenteur"},
    "securite": {"reseaux_autorises", "proxies_de_confiance", "session_jours"},
    "docker": {"url", "unraid_url"},
    "agenda": {"url", "utilisateur", "annonce_avant_min"},
    "proactif": {"active", "heures_calmes", "briefing_heure", "ville"},
    "telegram": {"chat_id", "docker", "disques", "agenda"},
}
FOURNISSEURS_MODIFIABLES = {"url", "modele", "effort"}
SECRETS_MODIFIABLES = {"HA_JETON", "AGENDA_MOT_DE_PASSE", "UNRAID_CLE", "UNRAID_CLE_ACTIONS", "TELEGRAM_JETON"}


class ReglageInvalide(ValueError):
    pass


def filtrer(changements: dict[str, Any]) -> dict[str, Any]:
    """Ne garde que les champs autorisés ; lève ReglageInvalide sinon."""
    propre: dict[str, Any] = {}
    for section, valeurs in changements.items():
        if not isinstance(valeurs, dict):
            raise ReglageInvalide(f"section {section} : objet attendu")
        if section == "fournisseurs":
            propre["fournisseurs"] = {}
            for ident, champs in valeurs.items():
                interdits = set(champs) - FOURNISSEURS_MODIFIABLES
                if interdits:
                    raise ReglageInvalide(f"fournisseur {ident} : champs non modifiables {sorted(interdits)}")
                propre["fournisseurs"][ident] = dict(champs)
            continue
        if section not in MODIFIABLES:
            raise ReglageInvalide(f"section non modifiable : {section}")
        interdits = set(valeurs) - MODIFIABLES[section]
        if interdits:
            raise ReglageInvalide(f"{section} : champs non modifiables {sorted(interdits)}")
        propre[section] = dict(valeurs)
    return propre


def appliquer(changements: dict[str, Any], defauts: dict[str, Any], fichier: Path | None = None) -> tuple[Config, Path]:
    """Valide puis écrit les changements dans config.yaml. Renvoie (nouvelle config, fichier)."""
    propre = filtrer(changements)
    chemin = fichier or chemin_config_utilisateur()
    utilisateur = _lire_yaml(chemin)
    if "fournisseurs" in propre:
        connus = set(defauts.get("fournisseurs", {})) | set(utilisateur.get("fournisseurs", {}))
        inconnus = set(propre["fournisseurs"]) - connus
        if inconnus:
            raise ReglageInvalide(f"fournisseurs inconnus : {sorted(inconnus)}")
    nouveau = _fusion(utilisateur, propre)
    try:
        cfg = Config.model_validate(_fusion(defauts, nouveau))
    except ValueError as e:
        raise ReglageInvalide(str(e).split("\n")[0] if "\n" in str(e) else str(e)) from e
    chemin.parent.mkdir(parents=True, exist_ok=True)
    texte = "# Config locale (non versionnée). Modifiée aussi depuis la page de configuration.\n" + yaml.safe_dump(
        nouveau, allow_unicode=True, sort_keys=False)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(texte, encoding="utf-8")
    os.replace(tmp, chemin)
    return cfg, chemin


def fichier_secrets() -> Path:
    if os.environ.get("ASSISTANT_SECRETS"):
        return Path(os.environ["ASSISTANT_SECRETS"])
    if os.name != "nt" and Path("/config").is_dir():
        return Path("/config/.env")
    return RACINE / ".env"


def ecrire_secret(nom: str, valeur: str, fichier: Path | None = None) -> Path:
    """Remplace (ou ajoute) NOM=valeur dans le fichier de secrets, sans toucher au reste."""
    if nom not in SECRETS_MODIFIABLES:
        raise ReglageInvalide(f"secret non modifiable : {nom}")
    if "\n" in valeur or "\r" in valeur:
        raise ReglageInvalide("valeur sur une seule ligne")
    chemin = fichier or fichier_secrets()
    lignes = chemin.read_text(encoding="utf-8-sig").splitlines() if chemin.is_file() else []
    motif = re.compile(rf"^\s*{re.escape(nom)}\s*=")
    remplace = False
    for i, l in enumerate(lignes):
        if motif.match(l):
            lignes[i] = f"{nom}={valeur}"
            remplace = True
    if not remplace:
        lignes.append(f"{nom}={valeur}")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    os.replace(tmp, chemin)
    try:
        os.chmod(chemin, 0o600)
    except OSError:
        pass
    os.environ[nom] = valeur
    return chemin


def secret_defini(nom: str) -> bool:
    if os.environ.get(nom):
        return True
    f = fichier_secrets()
    if not f.is_file():
        return False
    return any(re.match(rf"^\s*{re.escape(nom)}\s*=\s*\S", l) for l in f.read_text(encoding="utf-8-sig").splitlines())


def vue(cfg: Config) -> dict[str, Any]:
    """Réglages actuels, sans aucun secret."""
    return {
        "identite": {"nom": cfg.identite.nom, "wake_word": cfg.identite.wake_word,
                     "wake_word_effectif": cfg.identite.wake_word_effectif,
                     "theme": cfg.identite.theme},
        "home_assistant": {"url": cfg.home_assistant.url, "jeton_defini": secret_defini(cfg.home_assistant.jeton_env)},
        "fournisseurs": {i: {"type": f.type, "url": f.url, "modele": f.modele, "effort": f.effort}
                         for i, f in cfg.fournisseurs.items()},
        "roles": dict(cfg.roles),
        "voix": {"voix": cfg.voix.voix_actuelle(), "fenetre_s": cfg.voix.fenetre_s,
                 "toujours_a_l_ecoute": cfg.voix.toujours_a_l_ecoute, "lenteur": cfg.voix.lenteur},
        "securite": {"reseaux_autorises": cfg.securite.reseaux_autorises,
                     "proxies_de_confiance": cfg.securite.proxies_de_confiance,
                     "session_jours": cfg.securite.session_jours},
        "docker": {"url": cfg.docker.url, "unraid_url": cfg.docker.unraid_url,
                   "cle_definie": secret_defini("UNRAID_CLE"), "cle_actions_definie": secret_defini("UNRAID_CLE_ACTIONS")},
        "agenda": {"url": cfg.agenda.url, "utilisateur": cfg.agenda.utilisateur,
                   "annonce_avant_min": cfg.agenda.annonce_avant_min,
                   "mot_de_passe_defini": secret_defini("AGENDA_MOT_DE_PASSE")},
        "telegram": {"chat_id": cfg.telegram.chat_id, "docker": cfg.telegram.docker, "disques": cfg.telegram.disques,
                     "agenda": cfg.telegram.agenda, "jeton_defini": secret_defini("TELEGRAM_JETON")},
        "proactif": {"active": cfg.proactif.active, "heures_calmes": cfg.proactif.heures_calmes,
                     "briefing_heure": cfg.proactif.briefing_heure, "ville": cfg.proactif.ville},
    }
